"""Mass-assignment probes read the created record back, because some APIs answer a create with a bare message."""
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
        "paths": {
            "/items": {"post": {"operationId": "createItem", "requestBody": {"required": True, "content": {"application/json": {
                "schema": {"type": "object", "properties": {"title": {"type": "string"}}}}}},
                "responses": {"201": {"description": "created"}}}},
            "/items/{itemId}": {
                "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "get": {"operationId": "getItem", "responses": {"200": {"description": "ok"}}},
                "delete": {"operationId": "deleteItem", "responses": {"204": {"description": "gone"}}}},
            "/lonely": {"post": {"operationId": "createLonely", "requestBody": {"required": True, "content": {"application/json": {
                "schema": {"type": "object", "properties": {"title": {"type": "string"}}}}}},
                "responses": {"201": {"description": "created"}}}}}}


@pytest.fixture(scope="module")
def suite(tmp_path_factory):
    d = tmp_path_factory.mktemp("rb")
    (d / "spec.json").write_text(json.dumps(SPEC))
    gen_pytest.generate(parse_openapi.build(str(d / "spec.json")), d / "suite", ["all"])
    return d / "suite"


@pytest.fixture(scope="module")
def rt(suite):
    spec = importlib.util.spec_from_file_location("rb_runtime", suite / "apitest_runtime.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _text(suite):
    return (suite / "test_security.py").read_text()


def test_only_creates_with_a_readable_record_are_read_back(suite):
    text = _text(suite)
    mine = text.split("def test_api3_mass_assignment_createItem")[1].split("\ndef ")[0]
    assert "expect_not_stored(api, resp, EP['getItem'], 'itemId'" in mine and "EP['deleteItem']" in mine
    lonely = text.split("def test_api3_mass_assignment_createLonely")[1].split("\ndef ")[0]
    assert "expect_not_stored" not in lonely


class _Store(BaseHTTPRequestHandler):
    rows: dict = {}
    deleted: list = []

    def _send(self, code, body=None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if body is not None:
            self.wfile.write(json.dumps(body).encode())

    def do_POST(self):
        row = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        _Store.rows[7] = row
        self._send(201, {"message": "created", "id": 7})   # nothing about the stored flags

    def do_GET(self):
        self._send(200, {"data": _Store.rows[7]})

    def do_DELETE(self):
        _Store.deleted.append(self.path)
        self._send(204)

    def log_message(self, *_a):
        pass


def test_a_flag_that_is_stored_but_not_echoed_is_caught_on_read_back(rt, monkeypatch):
    monkeypatch.setenv("APITEST_TARGET_ENV", "local")
    srv = HTTPServer(("127.0.0.1", 0), _Store)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        api = rt.Api(f"http://127.0.0.1:{srv.server_address[1]}", {})
        create = rt.Endpoint(id="c", method="POST", path="/items", ok=("2xx",), body={"title": "t"})
        read = rt.Endpoint(id="r", method="GET", path="/items/{itemId}", ok=("2xx",), path_params={"itemId": {"default": None, "type": "integer"}})
        delete = rt.Endpoint(id="d", method="DELETE", path="/items/{itemId}", ok=("2xx",), path_params={"itemId": {"default": None, "type": "integer"}})
        injected = {"admin": True}
        resp = api.send(create, body={"title": "t", **injected})
        rt.expect_not_assigned(resp, injected)   # the create response alone proves nothing
        cleanups = []

        class _Cleanup:
            def add(self, fn):
                cleanups.append(fn)

        with pytest.raises(BaseException) as err:
            rt.expect_not_stored(api, resp, read, "itemId", injected, _Cleanup(), delete)
        assert "stored" in str(err.value)
        cleanups[0]()
        assert _Store.deleted == ["/items/7"]
    finally:
        srv.shutdown()
