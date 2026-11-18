"""Fuzz: the run budget is shared between endpoints, hostile inputs go first, local targets run faster."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import gen_pytest
import parse_openapi
from suite_runner import run_suite

N = 30
BODY = {"required": True, "content": {"application/json": {"schema": {
    "type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}}}}
SPEC = {"openapi": "3.0.0", "info": {"title": "f", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
        "paths": {f"/r{i}": {"post": {"requestBody": BODY, "responses": {"201": {"description": "ok"}}}} for i in range(N)}}
SPEC["paths"]["/read"] = {"get": {"responses": {"200": {"description": "ok"}}}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        code = 500 if b"\\u0000" in raw else 201     # the server chokes on a NUL byte in any string
        self.send_response(code)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    d = tmp_path_factory.mktemp("fz")
    (d / "spec.json").write_text(json.dumps(SPEC))
    gen_pytest.generate(parse_openapi.build(str(d / "spec.json")), d / "suite", ["fuzz"])
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    res, proc = run_suite(d / "suite", f"http://127.0.0.1:{srv.server_port}",
                          {"APITEST_FUZZ_MAX_REQUESTS": "520"}, ["-p", "no:randomly"])
    srv.shutdown()
    return res, proc


def test_every_endpoint_gets_cases_and_the_nul_crash_is_found_on_all(run):
    res, _ = run
    posts = {k: v for k, v in res.items() if "r" in k.split("[")[-1] and "read" not in k}
    assert len(posts) == N
    assert all(s == "failed" and "server error 500" in m for s, m in posts.values()), \
        sorted({(s, m[:80]) for s, m in posts.values() if s != "failed"})
    assert not any("budget" in m for _, m in res.values())


def test_fast_default_rate_for_a_loopback_target(run):
    _, proc = run
    # 30 endpoints x 16 cases at the 5/s non-local default would take 96 s; the whole run stays far below that
    import re
    secs = float(re.search(r"in ([\d.]+)s", proc.stdout).group(1))
    assert secs < 40
