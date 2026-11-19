"""A refusal the skill's own guess may have caused is "could not verify", not a failure; a stale If-Match is refreshed first."""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import gen_pytest
import parse_openapi

SPEC = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
        "paths": {"/things/{id}": {
            "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
            "get": {"operationId": "getThing", "responses": {"200": {"description": "ok"}}},
            "put": {"operationId": "putThing",
                    "parameters": [{"name": "If-Match", "in": "header", "required": True, "schema": {"type": "string"}, "example": "\"v0\""}],
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"a": {"type": "string"}}}}}},
                    "responses": {"200": {"description": "ok"}}}}}}


class Thing(BaseHTTPRequestHandler):
    version = 5
    puts: list = []

    def do_GET(self):
        self.send_response(200)
        self.send_header("ETag", f'"v{Thing.version}"')
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_PUT(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Thing.puts.append(self.headers.get("If-Match"))
        ok = self.headers.get("If-Match") == f'"v{Thing.version}"'
        self.send_response(200 if ok else 412)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_a):
        pass


@pytest.fixture(scope="module")
def rt(tmp_path_factory):
    d = tmp_path_factory.mktemp("cnv")
    (d / "spec.json").write_text(json.dumps(SPEC))
    gen_pytest.generate(parse_openapi.build(str(d / "spec.json")), d / "suite", ["positive"])
    spec = importlib.util.spec_from_file_location("cnv_runtime", d / "suite" / "apitest_runtime.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("APITEST_TARGET_ENV", "local")
    srv = HTTPServer(("127.0.0.1", 0), Thing)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Thing.puts.clear()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_a_required_if_match_gets_the_current_etag_from_a_read_first(rt, server):
    api = rt.Api(server, {})
    ep = rt.Endpoint(id="putThing", method="PUT", path="/things/{id}", ok=("2xx",), body={"a": "x"},
                     headers={"If-Match": "\"v0\""}, path_params={"id": {}})
    resp = api.send(ep, path={"id": "7"})
    assert resp.status_code == 200 and Thing.puts == ['"v5"']   # the spec's stale example was replaced
    given = api.send(ep, path={"id": "7"}, headers={"If-Match": "\"old\""})
    assert given.status_code == 412 and Thing.puts[-1] == '"old"'   # a value a test sends itself is left alone


def test_a_412_on_a_sent_if_match_is_could_not_verify_and_a_spec_value_still_fails(rt, server):
    api = rt.Api(server, {})
    ep = rt.Endpoint(id="putThing", method="PUT", path="/things/{id}", ok=("2xx",), body={"a": "x"}, path_params={"id": {}})
    stale = api.send(ep, path={"id": "7"}, headers={"If-Match": "\"old\""})
    with pytest.raises(pytest.skip.Exception, match="could not verify"):
        rt.expect_ok(stale, ep)
    resp = api.send(ep, path={"id": "7"}, headers={"If-Match": '"v5"'})
    rt.expect_ok(resp, ep)
    # a refusal with a value from the spec (nothing guessed) is a real failure
    other = api.get("/things/1")
    other.status_code = 422
    with pytest.raises(pytest.fail.Exception):
        rt.expect_status(other, (200,), "request with valid input should succeed")


def test_a_guessed_value_refusal_skips_but_a_5xx_and_other_codes_fail(rt, server):
    api = rt.Api(server, {})
    r = api.get("/things/1")
    r._guessed = ("period",)
    r.status_code = 422
    with pytest.raises(pytest.skip.Exception, match="could not verify"):
        rt.expect_status(r, (200,), "request with valid input should succeed")
    r.status_code = 500
    with pytest.raises(pytest.fail.Exception):
        rt.expect_status(r, (200,), "request with valid input should succeed")
    r.status_code = 401
    with pytest.raises(pytest.fail.Exception):
        rt.expect_status(r, (200,), "request with valid input should succeed")
