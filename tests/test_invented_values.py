"""A failure that follows an invented request value is rated INFO ("needs an example"), not HIGH."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import gen_pytest
import parse_openapi
from suite_runner import run_suite

SPEC = {"openapi": "3.0.0", "info": {"title": "p", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
        "paths": {"/period-gate": {"get": {
            "parameters": [{"name": "start", "in": "query", "required": True, "schema": {"type": "string"}}],
            "responses": {"200": {"description": "ok"}}}},
            "/boom": {"get": {"responses": {"200": {"description": "ok"}}}}}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        code = 422 if self.path.startswith("/period-gate") else 500
        self.send_response(code)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    d = tmp_path_factory.mktemp("inv")
    (d / "spec.json").write_text(json.dumps(SPEC))
    gen_pytest.generate(parse_openapi.build(str(d / "spec.json")), d / "suite", ["smoke"])
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    res, _ = run_suite(d / "suite", f"http://127.0.0.1:{srv.server_port}", {})
    srv.shutdown()
    return res


def test_rejected_invented_value_is_info(run):
    status, msg = next(v for k, v in run.items() if "period" in k)
    assert status == "failed" and "[INFO]" in msg and "needs an example" in msg and "[HIGH]" not in msg


def test_a_server_error_keeps_its_severity_rules(run):
    status, msg = next(v for k, v in run.items() if "boom" in k)
    assert status == "failed" and "[INFO]" not in msg
