"""Create bodies get a run tag on fields a server usually keeps unique, so repeated creates do not collide."""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import gen_common
import gen_pytest
import parse_openapi

BODY = {"type": "object", "required": ["email", "username"], "properties": {
    "email": {"type": "string", "format": "email"},
    "username": {"type": "string", "maxLength": 12},
    "name": {"type": "string"},
    "role": {"type": "string", "enum": ["user", "admin"]},
    "code": {"type": "string", "pattern": "^[A-Z]{3}$"},
    "password": {"type": "string"},
    "id": {"type": "integer", "readOnly": True}}}
SPEC = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
        "paths": {"/users": {"post": {"requestBody": {"required": True, "content": {"application/json": {"schema": BODY}}},
                                      "responses": {"201": {"description": "created"}}},
                             "get": {"responses": {"200": {"description": "ok"}}}}}}


@pytest.fixture(scope="module")
def suite(tmp_path_factory):
    d = tmp_path_factory.mktemp("uniq")
    (d / "spec.json").write_text(json.dumps(SPEC))
    gen_pytest.generate(parse_openapi.build(str(d / "spec.json")), d / "suite", ["positive"])
    return d / "suite"


@pytest.fixture(scope="module")
def rt(suite):
    spec = importlib.util.spec_from_file_location("uniq_runtime", suite / "apitest_runtime.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_only_safe_unique_looking_string_fields_are_chosen():
    doc = parse_openapi.parse(SPEC)
    post = next(e for e in doc["endpoints"] if e["method"] == "POST")
    assert gen_common.unique_body_fields(post) == {"email": {}, "username": {"max": 12}, "name": {}}
    # enum, pattern, secret and read-only fields are left alone; GET has none
    get = next(e for e in doc["endpoints"] if e["method"] == "GET")
    assert gen_common.unique_body_fields(get) == {}


def test_login_style_endpoints_are_never_tagged():
    spec = json.loads(json.dumps(SPEC))
    spec["paths"]["/users/login"] = spec["paths"]["/users"]
    doc = parse_openapi.parse(spec)
    login = next(e for e in doc["endpoints"] if e["path"] == "/users/login" and e["method"] == "POST")
    assert gen_common.unique_body_fields(login) == {}


def test_generated_endpoints_carry_the_unique_fields(suite):
    src = (suite / "endpoints.py").read_text()
    assert "unique=" in src and "'username': {'max': 12}" in src.replace('"', "'")


def test_each_create_gets_a_fresh_value_that_still_fits(rt):
    ep = rt.Endpoint(id="x", method="POST", path="/users", body={"email": "a@example.com", "username": "sample-user", "role": "user"},
                     unique={"email": {}, "username": {"max": 12}})
    one, two = rt.unique_body(ep, ep.body), rt.unique_body(ep, ep.body)
    assert one["email"] != two["email"] and one["email"].endswith("@example.com") and one["email"].startswith("a-")
    assert len(one["username"]) <= 12 and one["username"] != two["username"]
    assert one["role"] == "user" and ep.body["email"] == "a@example.com"   # the baseline is never edited


def test_a_tag_that_does_not_fit_leaves_the_value_alone(rt):
    ep = rt.Endpoint(id="x", method="POST", path="/u", body={"code": "ab"}, unique={"code": {"max": 3}})
    assert rt.unique_body(ep, ep.body) == {"code": "ab"}


def test_opt_out_and_pinned_run_id(rt, monkeypatch):
    ep = rt.Endpoint(id="x", method="POST", path="/u", body={"name": "n"}, unique={"name": {}})
    monkeypatch.setenv("APITEST_UNIQUE_BODIES", "no")
    assert rt.unique_body(ep, ep.body) == {"name": "n"}


class _Echo(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _Echo.seen.append(json.loads(raw))
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_a):
        pass


def test_send_uses_tagged_bodies_and_the_echo_check_compares_what_was_sent(rt, monkeypatch):
    monkeypatch.setenv("APITEST_TARGET_ENV", "local")
    srv = HTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ep = rt.Endpoint(id="x", method="POST", path="/users", ok=("2xx",), body={"email": "a@example.com", "role": "user"},
                         unique={"email": {}})
        api = rt.Api(f"http://127.0.0.1:{srv.server_address[1]}", {})
        r1, r2 = api.send(ep), api.send(ep)
        assert _Echo.seen[0]["email"] != _Echo.seen[1]["email"] != "a@example.com"
        assert rt.sent_body(r1, ep) == _Echo.seen[0] and rt.sent_body(r2, ep) == _Echo.seen[1]
        rt.expect_echo(r1, rt.sent_body(r1, ep), ["email", "role"])   # passes: the server echoed the tagged body
    finally:
        srv.shutdown()
