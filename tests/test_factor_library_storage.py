"""Offline contracts for screened factor plans and their safe HTTP API."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, build_opener

import pytest

import server
from qtrade_adapters.deepseek_harness.factor_library import (
    FactorDataError,
    FactorLibrary,
    FactorStorageError,
    FactorValidationError,
    load_factor_records,
    normalize_conditions,
    resolve_factor_library_path,
)


def _write_artifacts(root: Path, as_of: str = "2026-08-25") -> None:
    output = root / "data" / "factorpool" / "output"
    output.mkdir(parents=True)
    factors = [
        {"factor": "alpha", "cn": "Alpha 动量", "eligible": True},
        {"factor": "beta", "cn": "Beta 低波", "eligible": False},
        {"factor": "gamma", "cn": "Gamma 反转", "eligible": True},
    ]
    (output / "factor_manifest_20260825.json").write_text(
        json.dumps({"date": as_of, "factors": factors}), encoding="utf-8"
    )
    (output / "factor_data_freshness_20260825.json").write_text(
        json.dumps({"date": as_of, "updated": as_of, "manifest": {"date": as_of}}),
        encoding="utf-8",
    )
    health = output / "health"
    health.mkdir()
    (health / "health_20260825.csv").write_text(
        "factor,icir120,crowding,test_date\n"
        f"alpha,1.20,0.10,{as_of}\n"
        f"beta,0.20,0.80,{as_of}\n"
        f"gamma,0.75,0.30,{as_of}\n",
        encoding="utf-8",
    )
    (output / "factor_usage_20260825.json").write_text(
        json.dumps({
            "date": as_of,
            "layers": {
                "ext_decision": {"alpha": True, "beta": True},
                "deferred": {"gamma": True},
            },
        }),
        encoding="utf-8",
    )
    (output / "factor_lifecycle_20260825.json").write_text(
        json.dumps({
            "date": as_of,
            "lifecycle": {"alpha": "active", "beta": "watch", "gamma": "active"},
        }),
        encoding="utf-8",
    )


def _library(tmp_path: Path) -> FactorLibrary:
    data_dir = tmp_path / "deck"
    _write_artifacts(data_dir)
    return FactorLibrary(tmp_path / "user-data" / "factor_library.json", data_dir)


def test_real_artifact_fields_are_normalized_and_conditions_are_whitelisted(tmp_path):
    data_dir = tmp_path / "deck"
    _write_artifacts(data_dir)

    records = load_factor_records(data_dir)
    assert [record["name"] for record in records] == ["alpha", "beta", "gamma"]
    assert records[0] == {
        "name": "alpha",
        "label": "Alpha 动量",
        "status": "eligible",
        "usage": ["ext_decision"],
        "lifecycle": "active",
        "icir120": 1.2,
        "crowding": 0.1,
        "as_of": "2026-08-25",
    }
    assert normalize_conditions({
        "status": ["eligible"],
        "usage": "ext_decision",
        "lifecycle": ["active"],
        "icir120_min": 0.7,
        "icir120_max": 1.3,
        "crowding_max": 0.4,
        "keyword": "alpha",
    }) == {
        "status": ["eligible"],
        "usage": ["ext_decision"],
        "lifecycle": ["active"],
        "icir120_min": 0.7,
        "icir120_max": 1.3,
        "crowding_max": 0.4,
        "keyword": "alpha",
    }
    with pytest.raises(FactorValidationError, match="unsupported condition"):
        normalize_conditions({"direction": "positive"})
    with pytest.raises(FactorValidationError):
        normalize_conditions({"icir120_min": float("nan")})


def test_preview_create_update_refresh_delete_and_server_recomputes_matches(tmp_path):
    library = _library(tmp_path)
    preview = library.preview({"status": "eligible", "icir120_min": 0.7})
    assert preview["matched_factors"] == ["alpha", "gamma"]
    assert preview["match_count"] == 2
    assert preview["source_token"].startswith("factor-v1-2026-08-25-")

    item = library.create("Evening set", "server-owned", {"usage": "ext_decision"})
    assert item["matched_factors"] == ["alpha", "beta"]
    assert item["created_at"] == item["updated_at"]
    created_at = item["created_at"]
    updated = library.update(item["id"], conditions={"status": "eligible"}, update_conditions=True)
    assert updated is not None
    assert updated["matched_factors"] == ["alpha", "gamma"]
    assert updated["created_at"] == created_at

    health = tmp_path / "deck" / "data" / "factorpool" / "output" / "health" / "health_20260825.csv"
    health.write_text(health.read_text(encoding="utf-8").replace("gamma,0.75", "gamma,0.95"), encoding="utf-8")
    refreshed = library.refresh(item["id"])
    assert refreshed is not None
    assert refreshed["source_token"] != updated["source_token"]
    assert library.delete(item["id"]) is True
    assert library.get(item["id"]) is None
    assert library.delete(item["id"]) is False


def test_storage_is_atomic_thread_safe_and_rejects_corruption_without_overwrite(tmp_path):
    library = _library(tmp_path)

    def create(index: int):
        library.create(f"Plan {index}", "", {"keyword": ""})

    threads = [threading.Thread(target=create, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    payload = json.loads(library.store_path.read_text(encoding="utf-8"))
    assert set(payload) == {"schema_version", "items"}
    assert payload["schema_version"] == 1
    assert len(payload["items"]) == 8

    original = library.store_path.read_bytes()
    library.store_path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(FactorStorageError):
        library.list_items()
    assert library.store_path.read_text(encoding="utf-8") == "{not-json"
    assert library.store_path.read_bytes() != original


def test_missing_or_stale_factor_artifacts_fail_closed(tmp_path):
    library = FactorLibrary(tmp_path / "user-data" / "factor_library.json", tmp_path / "missing-deck")
    with pytest.raises(FactorDataError, match="manifest"):
        library.preview({})
    data_dir = tmp_path / "stale-deck"
    _write_artifacts(data_dir, as_of="2026-08-24")
    freshness = data_dir / "data" / "factorpool" / "output" / "factor_data_freshness_20260825.json"
    freshness.write_text(
        json.dumps({"date": "2026-08-25", "updated": "2026-08-25"}), encoding="utf-8"
    )
    stale = FactorLibrary(tmp_path / "stale-user" / "factor_library.json", data_dir)
    with pytest.raises(FactorDataError):
        stale.preview({})


def test_factor_library_path_priority_never_defaults_to_data_directory(tmp_path):
    explicit = tmp_path / "explicit.json"
    from_env = tmp_path / "env.json"
    user_data = tmp_path / "user-data"
    assert resolve_factor_library_path(explicit, env={"QTRADE_FACTOR_LIBRARY_FILE": str(from_env)}, user_data_dir=user_data) == explicit.resolve()
    assert resolve_factor_library_path(env={"QTRADE_FACTOR_LIBRARY_FILE": str(from_env)}, user_data_dir=user_data) == from_env.resolve()
    assert resolve_factor_library_path(env={}, user_data_dir=user_data) == (user_data / "factor_library.json").resolve()
    assert resolve_factor_library_path(env={}, user_data_dir=tmp_path / "deck") != (tmp_path / "deck" / "data" / "factor_library.json").resolve()


def _api_request(opener, port: int, path: str, method: str = "GET", payload: dict | None = None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method)
    try:
        with opener.open(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            return error.code, json.loads(error.read().decode("utf-8"))
        finally:
            error.close()


def _factor_http_error(opener, port: int, path: str, method: str, body: bytes):
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with pytest.raises(HTTPError) as raised:
        opener.open(request, timeout=3)
    error = raised.value
    try:
        response_body = json.loads(error.read().decode("utf-8"))
        return error.code, dict(error.headers), response_body
    finally:
        error.close()


def test_http_api_crud_filters_server_owned_matches_and_safe_errors(tmp_path, monkeypatch):
    data_dir = tmp_path / "deck"
    _write_artifacts(data_dir)
    library = FactorLibrary(tmp_path / "user-data" / "factor_library.json", data_dir)
    monkeypatch.setattr(server, "FACTOR_LIBRARY", library)
    monkeypatch.setattr(server, "STATIC_DIR", Path(__file__).resolve().parents[1] / "static")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.APIHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    opener = build_opener()
    port = httpd.server_address[1]
    try:
        assert _api_request(opener, port, "/api/factor-library") == (200, {"schema_version": 1, "items": []})
        status, capabilities = _api_request(opener, port, "/api/factor-library/capabilities")
        assert status == 200
        assert capabilities["facets"] == {
            "status": ["eligible", "ineligible"],
            "usage": ["deferred", "ext_decision"],
            "lifecycle": ["active", "watch"],
        }
        assert capabilities["as_of"] == "2026-08-25"
        status, preview = _api_request(opener, port, "/api/factor-library/preview", "POST", {"conditions": {"status": "eligible"}})
        assert status == 200
        assert preview["matched_factors"] == ["alpha", "gamma"]
        assert "path" not in json.dumps(preview)

        status, error = _api_request(
            opener, port, "/api/factor-library", "POST",
            {"name": "bad", "conditions": {}, "matched_factors": ["beta"]},
        )
        assert status == 422
        assert error["error"] == "invalid_factor_library_request"

        status, item = _api_request(
            opener, port, "/api/factor-library", "POST",
            {"name": "API plan", "conditions": {"status": "eligible"}},
        )
        assert status == 201
        identifier = item["id"]
        assert item["matched_factors"] == ["alpha", "gamma"]
        assert _api_request(opener, port, f"/api/factor-library/{identifier}")[1]["id"] == identifier
        status, item = _api_request(
            opener, port, f"/api/factor-library/{identifier}", "PUT",
            {"name": "Renamed", "conditions": {"keyword": "beta"}},
        )
        assert status == 200
        assert item["name"] == "Renamed"
        assert item["matched_factors"] == ["beta"]
        assert _api_request(opener, port, f"/api/factor-library/{identifier}/refresh", "POST")[0] == 200
        assert _api_request(opener, port, f"/api/factor-library/{identifier}", "DELETE")[0] == 200
        assert _api_request(opener, port, f"/api/factor-library/{identifier}")[0] == 404
        assert _api_request(opener, port, "/api/factor-library/missing", "PUT", {"name": "x"})[0] == 404
        assert _api_request(opener, port, "/api/factor-library/missing", "DELETE")[0] == 404
        assert _api_request(opener, port, "/api/factor-library", "PATCH")[0] == 405

        raw = "x" * 70_000
        request = Request(
            f"http://127.0.0.1:{port}/api/factor-library/preview",
            data=raw.encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as raised:
            opener.open(request, timeout=3)
        error = raised.value
        try:
            assert error.code == 413
            assert error.headers["Connection"].lower() == "close"
            assert json.loads(error.read().decode("utf-8")) == {
                "error": "request_too_large",
                "message": "request body is too large",
            }
        finally:
            error.close()
    finally:
        httpd.shutdown()
        thread.join(timeout=3)
        assert not thread.is_alive()
        httpd.server_close()


def test_factor_rejection_paths_drain_body_and_keep_server_usable(tmp_path, monkeypatch):
    data_dir = tmp_path / "deck"
    _write_artifacts(data_dir)
    library = FactorLibrary(tmp_path / "user-data" / "factor_library.json", data_dir)
    monkeypatch.setattr(server, "FACTOR_LIBRARY", library)
    monkeypatch.setattr(server, "STATIC_DIR", Path(__file__).resolve().parents[1] / "static")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.APIHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    opener = build_opener()
    port = httpd.server_address[1]
    try:
        rejection_cases = (
            ("/api/factor-library/preview", "POST", b"x" * 70_000, 413, "request_too_large"),
            ("/api/factor-library", "PATCH", b'{"ignored":1}', 405, "method_not_allowed"),
            ("/api/factor-library/unknown", "POST", b'{"ignored":1}', 404, "not_found"),
            ("/api/factor-library/capabilities/extra", "PUT", b'{"ignored":1}', 404, "not_found"),
            ("/api/factor-library/capabilities/extra", "DELETE", b'{"ignored":1}', 404, "not_found"),
        )
        for path, method, body, expected_status, expected_error in rejection_cases:
            status, headers, response_body = _factor_http_error(opener, port, path, method, body)
            assert status == expected_status
            assert headers["Connection"].lower() == "close"
            assert response_body["error"] == expected_error
            assert set(response_body) == {"error", "message"}

        status, body = _api_request(opener, port, "/api/factor-library")
        assert status == 200
        assert body == {"schema_version": 1, "items": []}
    finally:
        httpd.shutdown()
        thread.join(timeout=3)
        assert not thread.is_alive()
        httpd.server_close()


def test_factor_oversized_incomplete_body_has_bounded_teardown(tmp_path, monkeypatch):
    data_dir = tmp_path / "deck"
    _write_artifacts(data_dir)
    library = FactorLibrary(tmp_path / "user-data" / "factor_library.json", data_dir)
    monkeypatch.setattr(server, "FACTOR_LIBRARY", library)
    monkeypatch.setattr(server, "STATIC_DIR", Path(__file__).resolve().parents[1] / "static")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.APIHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    client = socket.create_connection(("127.0.0.1", httpd.server_address[1]), timeout=3)
    client.settimeout(3)
    try:
        request = (
            b"POST /api/factor-library/preview HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 65537\r\n\r\n"
            b"{"
        )
        started = time.monotonic()
        client.sendall(request)
        response = bytearray()
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
        assert time.monotonic() - started < 3
        assert b" 413 " in response
        assert b'"error": "request_too_large"' in response
    finally:
        client.close()
        httpd.shutdown()
        thread.join(timeout=3)
        assert not thread.is_alive()
        httpd.server_close()


def test_corrupt_store_api_is_503_and_does_not_replace_file(tmp_path, monkeypatch):
    data_dir = tmp_path / "deck"
    _write_artifacts(data_dir)
    store = tmp_path / "user-data" / "factor_library.json"
    store.parent.mkdir()
    store.write_text("broken", encoding="utf-8")
    library = FactorLibrary(store, data_dir)
    monkeypatch.setattr(server, "FACTOR_LIBRARY", library)
    monkeypatch.setattr(server, "STATIC_DIR", Path(__file__).resolve().parents[1] / "static")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.APIHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _api_request(build_opener(), httpd.server_address[1], "/api/factor-library")
        assert status == 503
        assert body["error"] == "factor_library_storage_unavailable"
        assert "broken" in store.read_text(encoding="utf-8")
        assert str(tmp_path) not in json.dumps(body)
    finally:
        httpd.shutdown()
        thread.join(timeout=3)
        httpd.server_close()
