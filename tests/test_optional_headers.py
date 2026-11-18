"""Headers the spec marks optional but the server enforces: a spec-drift finding, and APITEST_SEND_OPTIONAL_HEADERS."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import gen_pytest
import parse_openapi
from suite_runner import run_suite

HDR = lambda n: {"name": n, "in": "header", "required": False, "schema": {"type": "string"}}  # noqa: E731
SPEC = {"openapi": "3.0.0", "info": {"title": "h", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
        "paths": {"/items": {"post": {
            "parameters": [HDR("Idempotency-Key"), HDR("If-Match")],
            "requestBody": {"required": True, "content": {"application/json": {"schema": {
                "type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}}}},
            "responses": {"201": {"description": "created"}}}}}}
SEEN: list[str] = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        key = self.headers.get("Idempotency-Key")
        if not key:
            body, code = {"detail": "idempotency_key_required"}, 400
        elif self.headers.get("If-Match") is None:
            body, code = {"detail": "if_match_required"}, 422
        else:
            SEEN.append(key)
            body, code = {"id": 1}, 201
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("hdr")
    (d / "spec.json").write_text(json.dumps(SPEC))
    gen_pytest.generate(parse_openapi.build(str(d / "spec.json")), d / "suite", ["positive"])
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield d / "suite", f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_missing_enforced_header_is_reported_as_spec_drift(env):
    suite, url = env
    res, _ = run_suite(suite, url, {})
    status, msg = next(v for k, v in res.items() if "valid_request" in k)
    assert status == "failed" and "spec drift" in msg and "Idempotency-Key" in msg and "[LOW]" in msg
    assert "APITEST_SEND_OPTIONAL_HEADERS=Idempotency-Key" in msg


def test_sending_the_optional_headers_makes_the_create_pass(env):
    suite, url = env
    SEEN.clear()
    res, _ = run_suite(suite, url, {"APITEST_SEND_OPTIONAL_HEADERS": "Idempotency-Key, If-Match"})
    assert next(v for k, v in res.items() if "valid_request" in k)[0] == "passed"
    assert SEEN and all(k.startswith("apitest-") for k in SEEN)


def test_undeclared_names_in_the_list_are_ignored(env):
    suite, url = env
    res, _ = run_suite(suite, url, {"APITEST_SEND_OPTIONAL_HEADERS": "X-Other=1"})
    assert next(v for k, v in res.items() if "valid_request" in k)[0] == "failed"
