"""Short-lived tokens (APITEST_TOKEN_CMD) and tests that end the caller's session running last."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import gen_common
import gen_pytest
import parse_openapi
from suite_runner import run_suite

OK = {"200": {"description": "ok"}}
SPEC = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
        "components": {"securitySchemes": {"b": {"type": "http", "scheme": "bearer"}}}, "security": [{"b": []}],
        "paths": {"/a": {"get": {"responses": OK}}, "/b": {"get": {"responses": OK}}, "/c": {"get": {"responses": OK}},
                  "/logout": {"post": {"responses": OK}}, "/auth/sign-out-all": {"post": {"responses": OK}}}}
STATE = {"left": 1}


class H(BaseHTTPRequestHandler):
    """tok1 works for one request only; tok2 (what the token command prints) always works."""

    def log_message(self, *a):
        pass

    def _go(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        auth = self.headers.get("Authorization")
        good = auth == "Bearer tok2"
        if auth == "Bearer tok1" and STATE["left"] > 0:
            STATE["left"] -= 1
            good = True
        self.send_response(200 if good else 401)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    do_GET = do_POST = _go


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("tok")
    (d / "spec.json").write_text(json.dumps(SPEC))
    gen_pytest.generate(parse_openapi.build(str(d / "spec.json")), d / "suite", ["smoke", "positive"])
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield d / "suite", f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_session_ending_endpoints_are_recognised():
    doc = parse_openapi.parse(SPEC)
    ends = {e["path"] for e in doc["endpoints"] if gen_common.ends_session(e)}
    assert ends == {"/logout", "/auth/sign-out-all"}


def test_tests_for_session_ending_endpoints_run_last(env):
    suite, url = env
    out = subprocess.run([sys.executable, "-m", "pytest", str(suite), "--collect-only", "-q", "-p", "no:cacheprovider"],
                         capture_output=True, text=True, env={**os.environ, "APITEST_BASE_URL": url}).stdout
    names = [ln for ln in out.splitlines() if "::" in ln]
    assert len(names) == 8
    assert all("logout" in n or "sign_out" in n for n in names[-2:])
    assert not any("logout" in n or "sign_out" in n for n in names[:-2])


def run_smoke(suite, url, extra):
    STATE["left"] = 1
    return run_suite(suite, url, {"APITEST_B_TOKEN": "tok1", **extra}, ["-k", "smoke_GET"])


def test_without_a_token_command_the_expired_token_fails_the_later_calls(env):
    suite, url = env
    res, _ = run_smoke(suite, url, {})
    assert sorted(s for s, _ in res.values()) == ["failed", "failed", "passed"]


def test_token_cmd_refreshes_on_401_and_retries(env):
    suite, url = env
    cmd = f"{sys.executable} -c \"print('tok2')\""
    res, proc = run_smoke(suite, url, {"APITEST_TOKEN_CMD": cmd})
    assert sorted(s for s, _ in res.values()) == ["passed", "passed", "passed"], res
    assert "tok2" not in proc.stdout        # the new token is never printed


def test_a_failing_token_cmd_changes_nothing(env):
    suite, url = env
    res, _ = run_smoke(suite, url, {"APITEST_TOKEN_CMD": f"{sys.executable} -c \"import sys; sys.exit(3)\""})
    assert sorted(s for s, _ in res.values()) == ["failed", "failed", "passed"]
