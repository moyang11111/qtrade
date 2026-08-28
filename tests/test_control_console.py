"""Offline contracts for the QTrade-owned read-only control console."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import subprocess

import qtrade_base_bridge as bridge


ROOT = Path(__file__).resolve().parents[1]
CONTROL_HTML = ROOT / "static" / "control.html"
CONTROL_JS = ROOT / "static" / "js" / "control.js"
CONTROL_CSS = ROOT / "static" / "css" / "control-console.css"


def _run_node(source: str) -> None:
    completed = subprocess.run(
        ["node", "--input-type=commonjs", "-e", source],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


class _FakeHandler:
    def __init__(self, path: str):
        self.path = path
        self.headers = {}
        self.wfile = BytesIO()
        self.responses = []

    def send_response(self, status):
        self.responses.append({"status": status})

    def send_header(self, name, value):
        self.responses[-1][name] = value

    def end_headers(self):
        pass


def test_control_route_is_qtrade_owned_without_external_base(monkeypatch):
    missing_base = ROOT / "does-not-exist-for-control-contract"
    monkeypatch.setattr(bridge, "base_dir", lambda: missing_base)
    handler = _FakeHandler("/control")

    assert bridge.QtradeDeckHandler(handler).handle_get("/control") is True
    assert handler.responses[0]["status"] == 200
    html = handler.wfile.getvalue().decode("utf-8")
    assert 'data-qtrade-native-control="true"' in html
    assert "QTrade 运维与研究控制台" in html
    assert "不会执行交易或系统命令" in html
    assert "deepseek-harness-quant" not in html.lower()


def test_control_page_loads_qtrade_assets_and_only_fixed_get_cards():
    html = CONTROL_HTML.read_text(encoding="utf-8")
    js = CONTROL_JS.read_text(encoding="utf-8")

    assert html.index('href="/css/tokens.css"') < html.index(
        'href="/css/control-console.css"'
    )
    assert '<script src="/js/control.js" defer></script>' in html
    for endpoint in (
        "/api/health",
        "/api/update/status",
        "/api/auto/paper?action=status",
        "/api/factor-library",
        "/api/harness/status",
        "/api/deepseek-chat/status",
        "/api/deepseek-chat/send",
        "/api/deepseek-chat/poll",
        "/api/deepseek-chat/history",
        "/api/deepseek-chat/cancel",
    ):
        assert endpoint in js
    assert "method: 'GET'" in js
    assert "method: 'POST'" in js
    assert "body: JSON.stringify({ session_id: chatState.sessionId, text: value })" in js
    assert "innerHTML" not in js
    assert "eval(" not in js
    assert "Function(" not in js
    assert "window.open" not in js
    assert "window.location =" not in js
    assert "location.href" not in js
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "quantapi",
        "chat2",
        "window.open",
        "window.location.href",
        "tools:",
        "system_prompt",
    ):
        assert forbidden not in js


def test_deepseek_chat_panel_is_collapsed_and_has_safe_controls():
    html = CONTROL_HTML.read_text(encoding="utf-8")

    assert 'id="deepseekChatPanel"' in html
    assert 'id="deepseekChatBody" hidden' in html
    assert 'aria-expanded="false"' in html
    assert 'maxlength="2000"' in html
    assert 'id="deepseekChatSend"' in html
    assert 'id="deepseekChatCancel"' in html
    assert "不会执行交易、系统命令、脚本或配置修改" in html
    assert "可能产生费用" in html
    assert "不显示或输入密钥" in html

    js = CONTROL_JS.read_text(encoding="utf-8")
    for state in (
        "disabled",
        "unconfigured",
        "ready",
        "accepted",
        "waiting",
        "replied",
        "failed",
        "timed_out",
        "service_unreachable",
    ):
        assert f"'{state}'" in js
    assert "CHAT_POLL_MIN_MS = 250" in js
    assert "CHAT_POLL_MAX_MS = 5000" in js
    assert "CHAT_MAX_WAIT_MS = 35000" in js
    assert "renderChatState('idle', '已停止本地等待；上游取消不受支持。')" in js
    assert "JSON.stringify({ session_id: sessionId, request_id: requestId })" in js


def test_deepseek_chat_dom_and_mock_flow_is_deterministic():
    _run_node(
        r"""
        const assert = require('node:assert/strict');
        const fs = require('node:fs');
        const vm = require('node:vm');

        const source = fs.readFileSync('static/js/control.js', 'utf8');

        class Element {
          constructor(tagName = 'div', id = '') {
            this.tagName = tagName.toUpperCase();
            this.id = id;
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
            for (const listener of this.listeners[type] || []) {
              listener({ target: this });
            }
          }

          click() {
            if (!this.disabled) this.dispatch('click');
          }

          setAttribute(name, value) {
            this.attributes[name] = String(value);
          }

          getAttribute(name) {
            return this.attributes[name] ?? null;
          }
        }

        function makeHarness(options = {}) {
          const ids = [
            'controlState', 'controlNotice', 'controlRefresh', 'controlCopy',
            'systemBody', 'pipelineBody', 'universeBody', 'opportunityBody',
            'factorBody', 'harnessBody', 'deepseekChatPanel', 'deepseekChatBody',
            'deepseekChatState', 'deepseekChatToggle', 'deepseekChatNotice',
            'deepseekChatHistory', 'deepseekChatInput', 'deepseekChatCounter',
            'deepseekChatSend', 'deepseekChatCancel',
          ];
          const elements = Object.fromEntries(ids.map(id => [id, new Element('div', id)]));
          elements.controlRefresh = new Element('button', 'controlRefresh');
          elements.controlCopy = new Element('button', 'controlCopy');
          elements.deepseekChatToggle = new Element('button', 'deepseekChatToggle');
          elements.deepseekChatInput = new Element('textarea', 'deepseekChatInput');
          elements.deepseekChatSend = new Element('button', 'deepseekChatSend');
          elements.deepseekChatCancel = new Element('button', 'deepseekChatCancel');
          elements.deepseekChatBody.hidden = true;
          const navPages = ['market', 'portal', 'pitch', 'factorboard', 'factors', 'autopaper'];
          const nav = navPages.map(page => {
            const button = new Element('button');
            button.dataset.qtradePage = page;
            return button;
          });
          const document = {
            getElementById: id => elements[id] || null,
            createElement: tagName => new Element(tagName),
            querySelectorAll: selector => selector === '[data-qtrade-page]' ? nav : [],
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
            addEventListener: (type, listener) => {
              (windowListeners[type] ||= []).push(listener);
            },
          };
          window.parent = window;
          const calls = [];
          let activePolls = 0;
          let maxActivePolls = 0;
          let deferredPollResolve = null;
          let pollIndex = 0;
          const pollResponses = options.pollResponses || [];
          const historyItems = options.historyItems || [];

          function response(payload, status = 200, rawText = undefined) {
            return {
              ok: status >= 200 && status < 300,
              status,
              json: async () => payload,
              text: async () => rawText === undefined ? JSON.stringify(payload) : rawText,
            };
          }

          async function fetchMock(url, request = {}) {
            calls.push({ url, request });
            if (url === '/api/deepseek-chat/status') {
              if (options.statusReject) throw new Error('private status failure');
              if (options.statusError) {
                return response(
                  options.statusError.payload || {},
                  options.statusError.status,
                  options.statusError.raw,
                );
              }
              return response({ state: options.status || 'ready', session_id: 'session-1' });
            }
            if (url === '/api/deepseek-chat/send') {
              if (options.sendError) {
                return response(
                  options.sendError.payload || {},
                  options.sendError.status,
                  options.sendError.raw,
                );
              }
              if (options.sendStatus) return response({ provider: 'private' }, options.sendStatus);
              return response({ state: 'accepted', request_id: 'request-1', poll_after_ms: 1 });
            }
            if (url.startsWith('/api/deepseek-chat/poll?request_id=')) {
              if (options.pollReject) throw new Error('private poll failure');
              if (options.pollTimeout) {
                const error = new Error('private poll timeout');
                error.name = 'TimeoutError';
                throw error;
              }
              if (options.pollError) {
                return response(
                  options.pollError.payload || {},
                  options.pollError.status,
                  options.pollError.raw,
                );
              }
              activePolls += 1;
              maxActivePolls = Math.max(maxActivePolls, activePolls);
              if (options.deferPoll) {
                return new Promise(resolve => {
                  deferredPollResolve = payload => {
                    activePolls -= 1;
                    resolve(response(payload));
                  };
                });
              }
              const payload = pollResponses[pollIndex++] || { state: 'waiting', poll_after_ms: 1 };
              activePolls -= 1;
              return response(payload);
            }
            if (url.startsWith('/api/deepseek-chat/history?')) {
              if (options.historyError) {
                return response(
                  options.historyError.payload || {},
                  options.historyError.status,
                  options.historyError.raw,
                );
              }
              return response({ items: historyItems });
            }
            if (url === '/api/deepseek-chat/cancel') {
              if (options.cancelError) {
                return response(
                  options.cancelError.payload || {},
                  options.cancelError.status,
                  options.cancelError.raw,
                );
              }
              return response({ state: 'idle' });
            }
            if (url === '/api/health') return response({ status: 'ok', mode: 'csv', symbols: 1 });
            if (url === '/api/update/status') return response({ state: 'unknown' });
            if (url === '/api/auto/paper?action=status') return response({ universe_summary: {} });
            if (url === '/api/factor-library') return response({ items: [] });
            if (url === '/api/harness/status') return response({ state: 'disabled' });
            throw new Error(`unexpected URL: ${url}`);
          }

          const navigator = { clipboard: { writeText: async () => {} } };
          const context = {
            document,
            window,
            fetch: fetchMock,
            navigator,
            AbortController,
            console,
            Date,
            Error,
            JSON,
            Math,
            Number,
            Object,
            Promise,
            Set,
            String,
            Array,
            encodeURIComponent,
            isFinite,
          };
          vm.runInNewContext(source, context, { filename: 'control.js' });
          return {
            elements,
            window,
            calls,
            timers,
            runNextTimer() {
              const index = timers.findIndex(timer => !timer.cancelled);
              assert.notEqual(index, -1, 'expected a scheduled poll');
              const [timer] = timers.splice(index, 1);
              timer.fn();
              return timer.ms;
            },
            resolveDeferred(payload) {
              assert.notEqual(deferredPollResolve, null, 'expected a deferred poll');
              deferredPollResolve(payload);
            },
            get maxActivePolls() { return maxActivePolls; },
          };
        }

        async function flush() {
          for (let index = 0; index < 20; index += 1) await Promise.resolve();
        }

        function chatCalls(harness) {
          return harness.calls
            .map(call => call.url)
            .filter(url => url.startsWith('/api/deepseek-chat'));
        }

        (async () => {
        const disabled = makeHarness({ status: 'disabled' });
        await flush();
        assert.equal(disabled.elements.deepseekChatState.textContent, '功能未启用');
        assert.equal(disabled.elements.deepseekChatInput.disabled, true);
        assert.equal(disabled.elements.deepseekChatToggle.disabled, true);
        assert.deepEqual(chatCalls(disabled), ['/api/deepseek-chat/status']);
        disabled.elements.deepseekChatSend.click();
        assert.equal(chatCalls(disabled).some(url => url.endsWith('/send')), false);

        const unconfigured = makeHarness({ status: 'unconfigured' });
        await flush();
        assert.equal(unconfigured.elements.deepseekChatState.textContent, '需要配置');
        assert.equal(unconfigured.elements.deepseekChatInput.disabled, true);

        const sensitive = '<script>globalThis.compromised=1</script> C:/Users/ASUS/private sk-test-secret-123';
        const unconfigured503 = makeHarness({
          statusError: {
            status: 503,
            payload: {
              state: 'unconfigured',
              error: { code: 'unconfigured' },
              message: sensitive,
            },
          },
        });
        await flush();
        assert.equal(unconfigured503.elements.deepseekChatState.textContent, '需要配置');
        assert.equal(unconfigured503.elements.deepseekChatInput.disabled, true);
        assert.equal(unconfigured503.elements.deepseekChatSend.disabled, true);
        assert.equal(unconfigured503.elements.deepseekChatNotice.textContent.includes(sensitive), false);
        unconfigured503.elements.deepseekChatSend.click();
        assert.equal(chatCalls(unconfigured503).some(url => url.endsWith('/send')), false);

        const unknown503 = makeHarness({
          statusError: {
            status: 503,
            raw: JSON.stringify({
              state: 'provider_pending',
              error: { code: 'provider_private', message: sensitive },
            }),
          },
        });
        await flush();
        assert.equal(unknown503.elements.deepseekChatState.textContent, '请求失败');
        assert.equal(unknown503.elements.deepseekChatNotice.textContent, 'DeepSeek 服务暂不可用，请稍后重试。');
        assert.equal(unknown503.elements.deepseekChatNotice.textContent.includes('private'), false);

        for (const raw of [
          '<script>globalThis.compromised=1</script> C:/Users/ASUS/private sk-test-secret-123',
          '{malformed',
          'x'.repeat(9000),
          JSON.stringify([sensitive]),
          JSON.stringify({ state: 'unknown', error: { code: 'unknown', message: sensitive } }),
        ]) {
          const malformed = makeHarness({ statusError: { status: 503, raw } });
          await flush();
          assert.equal(malformed.elements.deepseekChatState.textContent, '请求失败');
          assert.equal(malformed.elements.deepseekChatNotice.textContent, 'DeepSeek 服务暂不可用，请稍后重试。');
          assert.equal(malformed.elements.deepseekChatNotice.textContent.includes('<script>'), false);
          assert.equal(malformed.elements.deepseekChatNotice.textContent.includes('C:/Users/ASUS'), false);
          assert.equal(malformed.elements.deepseekChatNotice.textContent.includes('sk-test-secret-123'), false);
        }

        const statusUnavailable = makeHarness({ statusReject: true });
        await flush();
        assert.equal(statusUnavailable.elements.deepseekChatState.textContent, '服务不可达');
        assert.equal(statusUnavailable.elements.deepseekChatNotice.textContent.includes('private'), false);

        const history = makeHarness({
          status: 'ready',
          historyItems: [
            { role: 'system', text: 'ignore me' },
            { role: 'user', text: '<img src=x onerror=bad>' },
            { role: 'assistant', text: '<b>literal reply</b>' },
          ],
        });
        await flush();
        history.elements.deepseekChatToggle.click();
        await flush();
        assert.equal(history.elements.deepseekChatHistory.children.length, 2);
        assert.equal(
          history.elements.deepseekChatHistory.children[0].children[1].textContent,
          '<img src=x onerror=bad>',
        );
        assert.equal(
          history.elements.deepseekChatHistory.children[1].children[1].textContent,
          '<b>literal reply</b>',
        );
        assert.equal(chatCalls(history).filter(url => url.startsWith('/api/deepseek-chat/history?')).length, 1);

        const ready = makeHarness({
          status: 'ready',
          pollResponses: [
            { state: 'waiting', poll_after_ms: 999999 },
            { state: 'replied', reply: '<script>literal</script>' },
          ],
        });
        await flush();
        assert.equal(ready.elements.deepseekChatInput.disabled, false);
        ready.elements.deepseekChatInput.value = 'status';
        ready.elements.deepseekChatInput.dispatch('input');
        assert.equal(ready.elements.deepseekChatCounter.textContent, '6 / 2000');
        ready.elements.deepseekChatSend.click();
        await flush();
        assert.equal(ready.elements.deepseekChatState.textContent, '本地已接收');
        assert.equal(ready.elements.deepseekChatHistory.children.length, 1);
        const sendCall = ready.calls.find(call => call.url === '/api/deepseek-chat/send');
        assert.deepEqual(Object.keys(JSON.parse(sendCall.request.body)).sort(), ['session_id', 'text']);
        assert.equal(JSON.parse(sendCall.request.body).text, 'status');
        assert.equal(ready.runNextTimer(), 250);
        await flush();
        assert.equal(ready.elements.deepseekChatState.textContent, '等待回复');
        assert.equal(ready.elements.deepseekChatHistory.children.length, 1);
        assert.equal(ready.runNextTimer(), 5000);
        await flush();
        assert.equal(ready.elements.deepseekChatState.textContent, '已收到回复');
        assert.equal(ready.elements.deepseekChatHistory.children.length, 2);
        assert.equal(
          ready.elements.deepseekChatHistory.children[1].children[1].textContent,
          '<script>literal</script>',
        );
        assert.equal(ready.maxActivePolls, 1);
        assert.equal(ready.window.QTradeDeepSeekChat.clampPollAfterMs(1), 250);
        assert.equal(ready.window.QTradeDeepSeekChat.clampPollAfterMs(999999), 5000);
        assert.equal(ready.window.QTradeDeepSeekChat.MAX_WAIT_MS, 35000);

        const sendUnconfigured = makeHarness({
          status: 'ready',
          sendError: {
            status: 503,
            payload: { state: 'unconfigured', error: { code: 'unconfigured' }, message: sensitive },
          },
        });
        await flush();
        sendUnconfigured.elements.deepseekChatInput.value = 'status';
        sendUnconfigured.elements.deepseekChatSend.click();
        await flush();
        assert.equal(sendUnconfigured.elements.deepseekChatState.textContent, '需要配置');
        assert.equal(sendUnconfigured.elements.deepseekChatInput.disabled, true);
        assert.equal(sendUnconfigured.elements.deepseekChatNotice.textContent.includes('private'), false);
        assert.equal(chatCalls(sendUnconfigured).some(url => url.includes('/poll?')), false);

        for (const [status, code, expectedNotice] of [
          [429, 'upstream_rate_limited', 'DeepSeek 服务繁忙，请稍后重试。'],
          [502, 'upstream_unreachable', 'DeepSeek 服务不可达，请检查服务状态后重试。'],
          [504, 'upstream_timeout', '等待 DeepSeek 回复超时，请稍后重试。'],
        ]) {
          const failed = makeHarness({
            status: 'ready',
            sendError: {
              status,
              payload: { error: { code }, provider_message: sensitive },
            },
          });
          await flush();
          failed.elements.deepseekChatInput.value = 'status';
          failed.elements.deepseekChatSend.click();
          await flush();
          assert.equal(failed.elements.deepseekChatState.textContent, status === 502 ? '服务不可达' : status === 504 ? '等待超时' : '请求失败');
          assert.equal(failed.elements.deepseekChatNotice.textContent, expectedNotice);
          assert.equal(failed.elements.deepseekChatNotice.textContent.includes('provider_message'), false);
        }

        for (const [status, code, expectedState, expectedNotice] of [
          [502, 'upstream_unreachable', '服务不可达', 'DeepSeek 服务不可达，请检查服务状态后重试。'],
          [504, 'upstream_timeout', '等待超时', '等待 DeepSeek 回复超时，请稍后重试。'],
        ]) {
          const failed = makeHarness({
            status: 'ready',
            pollError: {
              status,
              payload: { error: { code }, provider_message: sensitive },
            },
          });
          await flush();
          failed.elements.deepseekChatInput.value = 'status';
          failed.elements.deepseekChatSend.click();
          await flush();
          failed.runNextTimer();
          await flush();
          assert.equal(failed.elements.deepseekChatState.textContent, expectedState);
          assert.equal(failed.elements.deepseekChatNotice.textContent, expectedNotice);
          assert.equal(failed.elements.deepseekChatNotice.textContent.includes('provider_message'), false);
        }

        for (const option of ['pollReject', 'pollTimeout']) {
          const unavailable = makeHarness({ status: 'ready', [option]: true });
          await flush();
          unavailable.elements.deepseekChatInput.value = 'status';
          unavailable.elements.deepseekChatSend.click();
          await flush();
          unavailable.runNextTimer();
          await flush();
          assert.equal(
            unavailable.elements.deepseekChatState.textContent,
            option === 'pollTimeout' ? '等待超时' : '服务不可达',
          );
          assert.equal(unavailable.elements.deepseekChatNotice.textContent.includes('private'), false);
        }

        const cancel = makeHarness({ status: 'ready', deferPoll: true });
        await flush();
        cancel.elements.deepseekChatInput.value = 'status';
        cancel.elements.deepseekChatSend.click();
        await flush();
        cancel.runNextTimer();
        await flush();
        cancel.elements.deepseekChatCancel.click();
        await flush();
        assert.equal(cancel.elements.deepseekChatState.textContent, '等待发送');
        assert.equal(cancel.elements.deepseekChatNotice.textContent, '已停止本地等待；上游取消不受支持。');
        assert.equal(cancel.elements.deepseekChatNotice.textContent.includes('已取消DeepSeek'), false);
        const cancelCall = cancel.calls.find(call => call.url === '/api/deepseek-chat/cancel');
        assert.deepEqual(Object.keys(JSON.parse(cancelCall.request.body)).sort(), ['request_id', 'session_id']);
        cancel.resolveDeferred({ state: 'replied', reply: 'late reply' });
        await flush();
        assert.equal(cancel.elements.deepseekChatState.textContent, '等待发送');
        assert.equal(cancel.elements.deepseekChatHistory.children.length, 1);

        const cancelFailure = makeHarness({
          status: 'ready',
          deferPoll: true,
          cancelError: { status: 502, payload: { error: { code: 'upstream_unreachable' }, message: sensitive } },
        });
        await flush();
        cancelFailure.elements.deepseekChatInput.value = 'status';
        cancelFailure.elements.deepseekChatSend.click();
        await flush();
        cancelFailure.runNextTimer();
        await flush();
        cancelFailure.elements.deepseekChatCancel.click();
        await flush();
        assert.equal(cancelFailure.elements.deepseekChatState.textContent, '等待发送');
        assert.equal(cancelFailure.elements.deepseekChatNotice.textContent, '已停止本地等待；上游取消不受支持。');
        assert.equal(cancelFailure.elements.deepseekChatNotice.textContent.includes('upstream_unreachable'), false);

        const historyFailure = makeHarness({
          status: 'ready',
          historyError: { status: 503, payload: { state: 'unconfigured', error: { code: 'unconfigured' }, message: sensitive } },
        });
        await flush();
        historyFailure.elements.deepseekChatToggle.click();
        await flush();
        assert.equal(historyFailure.elements.deepseekChatState.textContent, '需要配置');
        assert.equal(historyFailure.elements.deepseekChatInput.disabled, true);
        assert.equal(historyFailure.elements.deepseekChatNotice.textContent.includes('private'), false);

        for (const status of [401, 429]) {
          const failed = makeHarness({ status: 'ready', sendStatus: status });
          await flush();
          failed.elements.deepseekChatInput.value = 'status';
          failed.elements.deepseekChatSend.click();
          await flush();
          assert.equal(failed.elements.deepseekChatState.textContent, '请求失败');
          assert.equal(failed.elements.deepseekChatNotice.textContent.includes('private'), false);
        }
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """
    )


def test_control_navigation_is_same_origin_source_and_page_allowlisted():
    control_js = CONTROL_JS.read_text(encoding="utf-8")
    app_js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "window.parent.postMessage({ type: 'qtrade:navigate', page }, window.location.origin)" in control_js
    assert "event.origin !== window.location.origin" in app_js
    assert "event.source !== controlFrame.contentWindow" in app_js
    assert "message.type !== 'qtrade:navigate'" in app_js
    assert "CONTROL_NAVIGATION_PAGES.has(message.page)" in app_js
    for page in ("market", "portal", "pitch", "factorboard", "factors", "autopaper"):
        assert f"'{page}'" in control_js
        assert f"'{page}'" in app_js
    assert "postMessage" not in control_js.replace(
        "window.parent.postMessage({ type: 'qtrade:navigate', page }, window.location.origin)", ""
    )
    assert "'*'" not in control_js


def test_control_cards_render_api_values_as_text_and_redact_diagnostics():
    js = CONTROL_JS.read_text(encoding="utf-8")
    diagnostics = js[js.index("function diagnosticPayload") : js.index("async function copyDiagnostics")]

    assert "textContent = value" in js
    assert "createElement('span')" in js
    assert "generated_at" in js
    for field in (
        "trade_date",
        "state",
        "reason",
        "outputs",
        "mainboard",
        "factor_library",
        "harness",
    ):
        assert field in js
    for secret in ("absolute_path", "QTRADE_BASE_DIR", "api_key", "last_error"):
        assert secret not in diagnostics
    assert "last_error" in js
    assert "hasError" in js
    assert "payload.last_error" in js
    assert "textContent = payload.last_error" not in js


def test_control_console_styles_use_tokens_and_prevent_horizontal_overflow():
    css = CONTROL_CSS.read_text(encoding="utf-8")

    assert "var(--qt-bg-primary)" in css
    assert "var(--qt-color-brand)" in css
    assert "var(--qt-color-down)" in css
    assert "overflow-x: hidden" in css
    assert "@media (max-width: 760px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_parent_message_listener_is_a_small_static_navigation_contract():
    source = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "handleControlNavigationMessage" in source
    assert "window.addEventListener('message', handleControlNavigationMessage)" in source


def test_health_probe_uses_exact_path_without_api_object_concat():
    app = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "const h = await fetch('/api/health').then(r => r.json());" in app
    assert "fetch(API +" not in app

    _run_node(
        """
        const assert = require('node:assert/strict');
        const calls = [];
        global.fetch = async (url) => {
          calls.push(url);
          return { ok: true, json: async () => ({ status: 'ok', mode: 'csv' }) };
        };
        (async () => {
          const health = await fetch('/api/health').then(response => response.json());
          assert.equal(health.status, 'ok');
          assert.deepEqual(calls, ['/api/health']);
        })().catch(error => { console.error(error); process.exitCode = 1; });
        """
    )
