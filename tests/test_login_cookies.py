"""Sign-in with a real account (APITEST_LOGIN_*) and cookies (APITEST_COOKIES), with every value kept out of reports."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import gen_pytest
import parse_openapi
from suite_runner import run_suite

SPEC = {"openapi": "3.0.0", "info": {"title": "l", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
        "paths": {"/auth/login": {"post": {"requestBody": {"required": True, "content": {"application/json": {"schema": {
            "type": "object", "required": ["email", "password"],
            "properties": {"email": {"type": "string", "format": "email"}, "password": {"type": "string"}}}}}},
            "responses": {"200": {"description": "ok"}}}},
            "/auth/refresh": {"post": {"responses": {"200": {"description": "ok"}}}}}}
SEEN: list = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        SEEN.append((self.path, body, self.headers.get("Cookie")))
        ok = (body == {"email": "pilot@example.org", "password": "s3cret-pw!"}) if self.path == "/auth/login" \
            else "refresh_token=r-12345" in (self.headers.get("Cookie") or "")
        self.send_response(200 if ok else 401)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("login")
    (d / "spec.json").write_text(json.dumps(SPEC))
    gen_pytest.generate(parse_openapi.build(str(d / "spec.json")), d / "suite", ["positive"])
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield d / "suite", f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_login_valid_request_uses_the_configured_account(env):
    suite, url = env
    res, _ = run_suite(suite, url, {})
    assert next(v for k, v in res.items() if "login" in k)[0] == "failed"
    res, _ = run_suite(suite, url, {"APITEST_LOGIN_EMAIL": "pilot@example.org", "APITEST_LOGIN_PASSWORD": "s3cret-pw!"})
    assert next(v for k, v in res.items() if "login" in k)[0] == "passed"


def test_cookie_is_sent_and_never_shown(env):
    suite, url = env
    res, proc = run_suite(suite, url, {"APITEST_COOKIES": "other=x; refresh_token=r-12345"})
    assert next(v for k, v in res.items() if "refresh" in k)[0] == "passed"
    res, proc = run_suite(suite, url, {"APITEST_COOKIES": "refresh_token=wrong-value-1"})
    status, msg = next(v for k, v in res.items() if "refresh" in k)
    assert status == "failed" and "wrong-value-1" not in msg


def test_failure_messages_do_not_show_the_account(env):
    suite, url = env
    res, _ = run_suite(suite, url, {"APITEST_LOGIN_EMAIL": "pilot@example.org", "APITEST_LOGIN_PASSWORD": "wrong-pw-1234"})
    status, msg = next(v for k, v in res.items() if "login" in k)
    assert status == "failed" and "pilot@example.org" not in msg and "wrong-pw-1234" not in msg


def test_username_and_otp_are_scrubbed_too(env):
    suite, url = env
    res, _ = run_suite(suite, url, {"APITEST_LOGIN_EMAIL": "pilot@example.org", "APITEST_LOGIN_USERNAME": "pilot-user-77",
                                    "APITEST_LOGIN_OTP": "482913", "APITEST_LOGIN_PASSWORD": "wrong-pw-1234"})
    status, msg = next(v for k, v in res.items() if "login" in k)
    assert status == "failed" and "pilot-user-77" not in msg and "482913" not in msg
