"""When no property is required, an empty object is tried too, so a server that breaks on all-empty optional fields is caught."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


import gen_pytest
import parse_openapi
from suite_runner import run_suite

SPEC = {"openapi": "3.0.0", "info": {"title": "m", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
        "paths": {"/items": {"post": {"requestBody": {"required": True, "content": {"application/json": {"schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "note": {"type": "string"}}}}}},
            "responses": {"201": {"description": "created"}}}}}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        code = 201 if body else 500    # the server forgets that every field is optional
        self.send_response(code)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


def test_empty_object_case_finds_the_crash(tmp_path):
    (tmp_path / "s.json").write_text(json.dumps(SPEC))
    gen_pytest.generate(parse_openapi.build(str(tmp_path / "s.json")), tmp_path / "suite", ["edge"])
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        res, _ = run_suite(tmp_path / "suite", f"http://127.0.0.1:{srv.server_port}", {})
    finally:
        srv.shutdown()
    status, msg = next(v for k, v in res.items() if "no_optional_fields" in k)
    assert status == "failed" and "500" in msg
