"""Offline contracts for the explicit native-console daily update control."""

from __future__ import annotations

import datetime as dt
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

import qtrade_adapters.deepseek_harness.runtime as update_runtime
import server


ROOT = Path(__file__).resolve().parents[1]
CONTROL_JS = ROOT / "static" / "js" / "control.js"


def _write_status(path: Path, target: dt.date, state: str, reason: str = "completed") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "trade_date": target.isoformat(),
        "state": state,
        "reason": reason,
        "started_at": f"{target.isoformat()}T18:30:00",
        "finished_at": f"{target.isoformat()}T18:31:00" if state != "running" else None,
        "step": None,
        "outputs": {
            "portal": state == "success",
            "factors": state == "success",
            "decision": state == "success",
            "sync": state == "success",
        },
        "freshness": {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _controller(tmp_path: Path, run_fn, now: dt.datetime | None = None):
    status = tmp_path / "logs" / "manual.status.json"
    lock = tmp_path / "logs" / "manual.lock"
    return update_runtime.ManualUpdateController(
        base_dir_fn=lambda: tmp_path / "deck",
        project_root=tmp_path,
        status_file=status,
        lock_path=lock,
        clock=(lambda: now) if now is not None else None,
        run_fn=run_fn,
    ), status, lock


def test_manual_controller_runs_one_fixed_full_pipeline_after_cutoff(tmp_path, monkeypatch):
    target = dt.date(2026, 8, 28)
    now = dt.datetime(2026, 8, 28, 18, 31)
    calls = []

    def fake_run(base, run_date, **kwargs):
        calls.append((base, run_date, kwargs))
        _write_status(kwargs["status_file"], run_date, "success")
        return 0

    controller, status, _ = _controller(tmp_path, fake_run, now)
    monkeypatch.setenv("QTRADE_NO_AUTOUPDATE", "1")

    accepted = controller.start()
    assert accepted["state"] == "accepted"
    assert accepted["trade_date"] == target.isoformat()
    worker = controller._worker
    assert worker is not None
    worker.join(timeout=2)
    assert not worker.is_alive()

    completed = controller.status()
    assert completed["state"] == "success"
    assert completed["outputs"] == {
        "portal": True,
        "factors": True,
        "decision": True,
        "sync": True,
    }
    assert len(calls) == 1
    base, run_date, kwargs = calls[0]
    assert base == tmp_path / "deck"
    assert run_date == target
    assert kwargs["project_root"] == tmp_path
    assert kwargs["status_file"] == status
    assert kwargs["python_executable"] == sys.executable
    assert kwargs["stop_event"].is_set() is False


def test_manual_controller_enforces_cutoff_calendar_result_and_idempotency(tmp_path):
    target = dt.date(2026, 8, 28)
    calls = []

    def fake_run(base, run_date, **kwargs):
        calls.append(run_date)
        _write_status(kwargs["status_file"], run_date, "failure", "calendar_unavailable: offline")
        return 1

    before, status, lock = _controller(
        tmp_path / "before",
        fake_run,
        dt.datetime(2026, 8, 28, 18, 29),
    )
    result = before.start()
    assert result["state"] == "skip"
    assert result["reason"] == "before_cutoff"
    assert calls == []

    after, status, lock = _controller(
        tmp_path / "after",
        fake_run,
        dt.datetime(2026, 8, 28, 18, 31),
    )
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("other process", encoding="utf-8")
    locked = after.start()
    assert locked["state"] == "skip"
    assert locked["reason"] == "lock_busy"
    lock.unlink()

    accepted = after.start()
    worker = after._worker
    assert accepted["state"] == "accepted"
    assert worker is not None
    worker.join(timeout=2)
    assert after.status()["state"] == "failure"
    assert after.status()["reason"] == "calendar_unavailable"

    _write_status(status, target, "success")
    already = after.start()
    assert already["state"] == "success"
    assert already["reason"] == "already_success"
    assert calls == [target]


def test_manual_controller_single_flight_and_safe_failure(tmp_path):
    target_time = dt.datetime(2026, 8, 28, 18, 31)
    started = threading.Event()
    release = threading.Event()
    calls = []

    def blocking_run(base, run_date, **kwargs):
        calls.append(run_date)
        started.set()
        release.wait(timeout=2)
        _write_status(kwargs["status_file"], run_date, "success")
        return 0

    controller, _, _ = _controller(tmp_path / "running", blocking_run, target_time)
    accepted = controller.start()
    assert accepted["state"] == "accepted"
    assert started.wait(timeout=1)
    duplicate = controller.start()
    assert duplicate["state"] in {"accepted", "running"}
    assert duplicate["reason"] == "already_running"
    release.set()
    worker = controller._worker
    assert worker is not None
    worker.join(timeout=2)
    assert calls == [target_time.date()]

    def no_status_run(*args, **kwargs):
        return 0

    failed, _, _ = _controller(tmp_path / "missing-status", no_status_run, target_time)
    result = failed.start()
    worker = failed._worker
    assert result["state"] == "accepted"
    assert worker is not None
    worker.join(timeout=2)
    assert failed.status()["state"] == "failure"
    assert failed.status()["reason"] == "status_unavailable"


@pytest.mark.parametrize("reason", ["weekend", "calendar_cache_closed", "calendar_api_closed"])
def test_manual_controller_does_not_repeat_deterministic_calendar_skip(tmp_path, reason):
    target = dt.date(2026, 8, 28)
    calls = []
    controller, status, lock = _controller(
        tmp_path,
        lambda *args, **kwargs: calls.append(args),
        dt.datetime(2026, 8, 28, 18, 31),
    )
    _write_status(status, target, "skip", reason)

    result = controller.start()

    assert result["state"] == "skip"
    assert result["reason"] == reason
    assert calls == []
    assert not lock.exists()


def test_manual_controller_calendar_unavailable_is_fail_closed_and_idempotent(tmp_path):
    target = dt.date(2026, 8, 28)
    calls = []
    controller, status, lock = _controller(
        tmp_path,
        lambda *args, **kwargs: calls.append(args),
        dt.datetime(2026, 8, 28, 18, 31),
    )
    _write_status(status, target, "failure", "calendar_unavailable: offline")

    result = controller.start()

    assert result["state"] == "failure"
    assert result["reason"] == "calendar_unavailable"
    assert calls == []
    assert not lock.exists()


class _BlockingProcess:
    def __init__(self):
        self.pid = 91234
        self.returncode = None
        self.started = threading.Event()
        self.terminated = threading.Event()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            if self.terminated.is_set():
                self.returncode = -15
                return self.returncode
            raise subprocess.TimeoutExpired(["daily_update_1830.py"], timeout)
        return self.returncode

    def terminate(self):
        self.terminated.set()

    def kill(self):
        self.terminated.set()
        self.returncode = -9


class _BlockingProcesses:
    DEVNULL = object()

    def __init__(self):
        self.process = _BlockingProcess()
        self.calls = []

    def Popen(self, command, **kwargs):
        self.calls.append((command, kwargs))
        self.process.started.set()
        return self.process


def test_manual_controller_stop_interrupts_only_its_child_and_discards_late_result(tmp_path):
    processes = _BlockingProcesses()
    controller = update_runtime.ManualUpdateController(
        base_dir_fn=lambda: tmp_path / "deck",
        project_root=tmp_path,
        status_file=tmp_path / "status.json",
        lock_path=tmp_path / "manual.lock",
        clock=lambda: dt.datetime(2026, 8, 28, 18, 31),
        subprocess_module=processes,
    )

    accepted = controller.start()
    worker = controller._worker
    assert accepted["state"] == "accepted"
    assert worker is not None
    assert worker.daemon is True
    assert processes.process.started.wait(timeout=1)
    assert processes.calls
    assert processes.calls[0][1]["shell"] is False
    assert worker.is_alive()

    controller.stop(timeout=1)
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert processes.process.terminated.is_set()
    assert not (tmp_path / "manual.lock").exists()
    assert controller.status()["state"] == "failure"
    assert controller.status()["reason"] == "update_failed"


def test_manual_controller_discards_late_completion_after_stop(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def late_run(base, run_date, **kwargs):
        started.set()
        release.wait(timeout=2)
        _write_status(kwargs["status_file"], run_date, "success")
        return 0

    controller, status, lock = _controller(
        tmp_path,
        late_run,
        dt.datetime(2026, 8, 28, 18, 31),
    )
    assert controller.start()["state"] == "accepted"
    worker = controller._worker
    assert worker is not None
    assert started.wait(timeout=1)

    controller.stop(timeout=0.01)
    assert worker.is_alive()
    assert controller.status()["state"] == "failure"

    release.set()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert status.exists()
    assert controller.status()["state"] == "failure"
    assert not lock.exists()


def test_manual_controller_cross_instance_lease_is_atomic(tmp_path):
    target_time = dt.datetime(2026, 8, 28, 18, 31)
    status = tmp_path / "status.json"
    lock = tmp_path / "manual.lock"
    entered = threading.Event()
    release = threading.Event()

    def blocking_run(base, run_date, **kwargs):
        entered.set()
        release.wait(timeout=2)
        _write_status(kwargs["status_file"], run_date, "success")
        return 0

    controllers = [
        update_runtime.ManualUpdateController(
            base_dir_fn=lambda: tmp_path / "deck",
            project_root=tmp_path,
            status_file=status,
            lock_path=lock,
            clock=lambda: target_time,
            run_fn=blocking_run,
        )
        for _ in range(2)
    ]
    barrier = threading.Barrier(3)
    results = []

    def start(controller):
        barrier.wait(timeout=1)
        results.append(controller.start())

    starters = [threading.Thread(target=start, args=(controller,)) for controller in controllers]
    for starter in starters:
        starter.start()
    barrier.wait(timeout=1)
    for starter in starters:
        starter.join(timeout=1)
    assert entered.wait(timeout=1)

    accepted = [result for result in results if result["state"] == "accepted"]
    busy = [result for result in results if result["reason"] == "lock_busy"]
    assert len(accepted) == 1
    assert len(busy) == 1

    release.set()
    for controller in controllers:
        worker = controller._worker
        if worker is not None:
            worker.join(timeout=2)
        controller.stop(timeout=1)
    assert not lock.exists()


def test_manual_controller_resolver_error_is_safe(tmp_path):
    controller = update_runtime.ManualUpdateController(
        base_dir_fn=lambda: (_ for _ in ()).throw(OSError("private path")),
        project_root=tmp_path,
        status_file=tmp_path / "status.json",
        lock_path=tmp_path / "lock",
        clock=lambda: dt.datetime(2026, 8, 28, 18, 31),
        run_fn=lambda *args, **kwargs: pytest.fail("resolver failure must not start worker"),
    )

    result = controller.start()
    assert result["state"] == "failure"
    assert result["reason"] == "update_failed"
    assert "private path" not in json.dumps(result)


def test_manual_status_reader_is_a_safe_whitelist(tmp_path):
    path = tmp_path / "status.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 999,
                "state": "failure",
                "trade_date": "2026-08-28T99",
                "reason": "private C:/Users/ASUS/secret --force",
                "started_at": "2026-08-28T18:30:00",
                "finished_at": "2026-08-28T18:31:00",
                "command": "python secret.py",
                "api_key": "sk-test-secret",
                "outputs": {"portal": True, "decision": "yes", "sync": True},
                "freshness": {
                    "portal": {
                        "verified": True,
                        "source": "C:/Users/ASUS/private",
                        "reason": "verified",
                        "total": 3,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = update_runtime.read_manual_update_status(path)
    encoded = json.dumps(result)
    assert result["state"] == "failure"
    assert result["reason"] == "update_failed"
    assert result["outputs"] == {"portal": True, "factors": False, "decision": False, "sync": True}
    assert "C:/Users/ASUS" not in encoded
    assert "secret.py" not in encoded
    assert "sk-test-secret" not in encoded


class _Probe:
    """Minimal HTTP handler surface for exercising API body/status contracts."""

    _json = server.APIHandler._json
    _manual_json = server.APIHandler._manual_json
    _manual_method_not_allowed = server.APIHandler._manual_method_not_allowed
    _reject_duplicate_json_pairs = staticmethod(server.APIHandler._reject_duplicate_json_pairs)
    _read_manual_update_body = server.APIHandler._read_manual_update_body
    _update_run = server.APIHandler._update_run
    _update_run_status = server.APIHandler._update_run_status

    def __init__(self, body=b"", headers=None, path=""):
        self.headers = headers or {}
        self.rfile = BytesIO(body)
        self.wfile = BytesIO()
        self.close_connection = False
        self.path = path
        self.responses = []

    def send_response(self, status):
        self.responses.append({"status": status})

    def send_header(self, name, value):
        self.responses[-1][name] = value

    def end_headers(self):
        pass


def _probe_json(probe: _Probe):
    body = probe.wfile.getvalue()
    return json.loads(body.decode("utf-8"))


@pytest.mark.parametrize(
    ("headers", "body", "status"),
    [
        ({"Content-Length": "2"}, b"{}", 415),
        ({"Content-Type": "application/json", "Content-Length": "0"}, b"", 400),
        ({"Content-Type": "application/json", "Content-Length": "14"}, b'{"unknown":1}', 400),
        (
            {"Content-Type": "application/json", "Content-Length": "2048"},
            b"x" * 2048,
            413,
        ),
    ],
)
def test_manual_update_body_requires_empty_json_object(headers, body, status):
    probe = _Probe(body, headers)
    assert probe._read_manual_update_body() is None
    assert probe.responses[0]["status"] == status


def test_manual_update_api_returns_safe_state_and_fixed_status_codes(monkeypatch):
    class FakeController:
        def __init__(self, value):
            self.value = value

        def start(self):
            return self.value

        def status(self):
            return self.value

    value = {
        "state": "accepted",
        "trade_date": "2026-08-28",
        "reason": "accepted",
        "outputs": {"portal": False, "factors": False, "decision": False, "sync": False},
        "command": "private command",
        "path": "C:/Users/ASUS/private",
    }
    monkeypatch.setattr(server, "get_manual_update_controller", lambda: FakeController(value))
    probe = _Probe(
        b"{}",
        {"Content-Type": "application/json; charset=utf-8", "Content-Length": "2"},
    )
    probe._update_run()
    assert probe.responses[0]["status"] == 202
    payload = _probe_json(probe)
    assert payload["state"] == "accepted"
    assert "command" not in payload
    assert "path" not in payload

    status_probe = _Probe()
    status_probe._update_run_status({})
    assert _probe_json(status_probe)["state"] == "accepted"


@pytest.mark.parametrize("path", [server.UPDATE_RUN_PATH, server.UPDATE_RUN_STATUS_PATH])
@pytest.mark.parametrize("method", ["do_PUT", "do_DELETE", "do_PATCH", "do_OPTIONS"])
def test_manual_update_methods_are_no_store_and_not_cors(path, method):
    probe = _Probe(path=path)

    getattr(server.APIHandler, method)(probe)

    assert probe.responses[0]["status"] == 405
    assert probe.responses[0]["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in probe.responses[0]


def test_manual_update_run_get_is_method_not_allowed():
    probe = _Probe(path=server.UPDATE_RUN_PATH)

    server.APIHandler.do_GET(probe)

    assert probe.responses[0]["status"] == 405
    assert probe.responses[0]["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in probe.responses[0]


def test_manual_update_status_post_is_method_not_allowed():
    probe = _Probe(path=server.UPDATE_RUN_STATUS_PATH)

    server.APIHandler.do_POST(probe)

    assert probe.responses[0]["status"] == 405
    assert probe.responses[0]["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in probe.responses[0]


def test_manual_update_status_rejects_unknown_query_without_cors_or_cache():
    probe = _Probe(path=f"{server.UPDATE_RUN_STATUS_PATH}?unexpected=1")

    server.APIHandler.do_GET(probe)

    assert probe.responses[0]["status"] == 400
    assert probe.responses[0]["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in probe.responses[0]


def _run_node(source: str) -> None:
    completed = subprocess.run(
        ["node", "--input-type=commonjs", "-e", source],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_manual_update_dom_flow_uses_fixed_payload_and_safe_states():
    _run_node(
        r"""
        const assert = require('node:assert/strict');
        const fs = require('node:fs');
        const vm = require('node:vm');
        const source = fs.readFileSync('static/js/control.js', 'utf8');

        class Element {
          constructor(tagName = 'div') {
            this.tagName = tagName.toUpperCase();
            this.children = [];
            this.firstChild = null;
            this.firstElementChild = null;
            this.parentNode = null;
            this.hidden = false;
            this.disabled = false;
            this.value = '';
            this.textContent = '';
            this.dataset = {};
            this.attributes = {};
            this.listeners = {};
          }
          appendChild(child) {
            child.parentNode = this;
            this.children.push(child);
            this.firstChild = this.children[0] || null;
            this.firstElementChild = this.firstChild;
            return child;
          }
          removeChild(child) {
            const index = this.children.indexOf(child);
            if (index >= 0) this.children.splice(index, 1);
            this.firstChild = this.children[0] || null;
            this.firstElementChild = this.firstChild;
            child.parentNode = null;
            return child;
          }
          addEventListener(type, listener) {
            (this.listeners[type] ||= []).push(listener);
          }
          dispatch(type) {
            for (const listener of this.listeners[type] || []) listener({ target: this });
          }
          click() {
            if (!this.disabled) this.dispatch('click');
          }
          setAttribute(name, value) { this.attributes[name] = String(value); }
          getAttribute(name) { return this.attributes[name] ?? null; }
        }

        const ids = [
          'controlState', 'controlNotice', 'controlRefresh', 'controlCopy',
          'manualUpdateButton', 'manualUpdateHint', 'manualUpdateStatus', 'manualUpdateOutputs',
          'systemBody', 'pipelineBody', 'universeBody', 'opportunityBody',
          'factorBody', 'harnessBody', 'deepseekChatPanel', 'deepseekChatBody',
          'deepseekChatState', 'deepseekChatToggle', 'deepseekChatNotice',
          'deepseekChatHistory', 'deepseekChatInput', 'deepseekChatCounter',
          'deepseekChatSend', 'deepseekChatCancel',
        ];
        const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
        for (const id of ['controlRefresh', 'controlCopy', 'manualUpdateButton',
          'deepseekChatToggle', 'deepseekChatSend', 'deepseekChatCancel']) {
          elements[id].tagName = 'BUTTON';
        }
        elements.deepseekChatInput.tagName = 'TEXTAREA';
        elements.deepseekChatBody.hidden = true;
        const outputNodes = ['portal', 'factors', 'decision', 'sync'].map(key => {
          const node = new Element('span');
          node.dataset.updateOutput = key;
          return node;
        });
        const nav = [];
        const document = {
          getElementById: id => elements[id] || null,
          createElement: tag => new Element(tag),
          querySelectorAll: selector => {
            if (selector === '[data-qtrade-page]') return nav;
            if (selector === '[data-update-output]') return outputNodes;
            return [];
          },
        };
        const timers = [];
        const windowListeners = {};
        const window = {
          parent: null,
          location: { origin: 'http://qtrade.test' },
          setInterval: (fn, ms) => ({ fn, ms }),
          clearInterval: () => {},
          setTimeout: (fn, ms) => {
            const timer = { fn, ms, cancelled: false };
            timers.push(timer);
            return timer;
          },
          clearTimeout: timer => { if (timer) timer.cancelled = true; },
          addEventListener: (type, listener) => { (windowListeners[type] ||= []).push(listener); },
        };
        window.parent = window;
        const calls = [];
        let manualStatusIndex = 0;
        const manualStatuses = [
          { state: 'idle', trade_date: '2026-08-28', reason: 'before_cutoff', outputs: {} },
          { state: 'accepted', trade_date: '2026-08-28', reason: 'accepted', outputs: {} },
          { state: 'running', trade_date: '2026-08-28', reason: 'running', outputs: { portal: true } },
          { state: 'success', trade_date: '2026-08-28', reason: 'completed',
            finished_at: '2026-08-28T18:31:00',
            outputs: { portal: true, factors: true, decision: true, sync: true } },
        ];
        function response(payload, status = 200) {
          return { ok: status >= 200 && status < 300, status, json: async () => payload };
        }
        async function fetchMock(url, request = {}) {
          calls.push({ url, request });
          if (url === '/api/deepseek-chat/status') return response({ state: 'disabled' });
          if (url === '/api/health') return response({ status: 'ok', mode: 'csv', symbols: 1 });
          if (url === '/api/update/status') return response({ state: 'unknown' });
          if (url === '/api/auto/paper?action=status') return response({ universe_summary: {} });
          if (url === '/api/factor-library') return response({ items: [] });
          if (url === '/api/harness/status') return response({ state: 'disabled' });
          if (url === '/api/update/run') {
            assert.equal(request.method, 'POST');
            assert.equal(request.headers['Content-Type'], 'application/json');
            assert.deepEqual(JSON.parse(request.body), {});
            return response(manualStatuses[1], 202);
          }
          if (url === '/api/update/run/status') {
            const index = manualStatusIndex === 0
              ? 0
              : Math.min(manualStatusIndex + 1, manualStatuses.length - 1);
            manualStatusIndex += 1;
            return response(manualStatuses[index]);
          }
          throw new Error(`unexpected URL ${url}`);
        }
        const navigator = { clipboard: { writeText: async () => {} } };
        const context = {
          document, window, fetch: fetchMock, navigator, AbortController, console, Date,
          Error, JSON, Math, Number, Object, Promise, Set, String, Array,
          encodeURIComponent, isFinite,
        };
        vm.runInNewContext(source, context, { filename: 'control.js' });
        async function flush() { for (let i = 0; i < 30; i += 1) await Promise.resolve(); }
        async function runTimer() {
          const index = timers.findIndex(timer => !timer.cancelled);
          assert.notEqual(index, -1, 'expected manual polling timer');
          const [timer] = timers.splice(index, 1);
          timer.fn();
          await flush();
        }
        (async () => {
          await flush();
          assert.equal(elements.manualUpdateButton.disabled, false);
          assert.match(elements.manualUpdateStatus.textContent, /18:30/);
          elements.manualUpdateButton.click();
          await flush();
          assert.match(elements.manualUpdateStatus.textContent, /已接收/);
          assert.equal(elements.manualUpdateButton.disabled, true);
          await runTimer();
          assert.match(elements.manualUpdateStatus.textContent, /更新中/);
          await runTimer();
          assert.match(elements.manualUpdateStatus.textContent, /已成功/);
          assert.equal(elements.manualUpdateButton.disabled, false);
          assert.equal(outputNodes.every(node => node.textContent.includes('已完成')), true);
          const manualCalls = calls.filter(call => call.url.startsWith('/api/update/run'));
          assert.equal(manualCalls.filter(call => call.url === '/api/update/run').length, 1);
          assert.equal(manualCalls.every(call => !call.request.body || Object.keys(JSON.parse(call.request.body)).length === 0), true);
          assert.equal(elements.manualUpdateStatus.textContent.includes('<script>'), false);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """,
    )


def test_control_manual_update_contract_has_no_dynamic_command_or_force_input():
    source = CONTROL_JS.read_text(encoding="utf-8")
    assert "body: JSON.stringify({})" in source
    assert "MANUAL_UPDATE_PATH = '/api/update/run'" in source
    assert '"force"' not in source
    assert "operation:" not in source
    assert '"operation"' not in source
    assert "shell:" not in source
