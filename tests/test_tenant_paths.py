"""Tenant-scoped APIs (`/b/{business_id}/...`): flows seed the tenant id, the no-credentials probe does not depend on a made-up id,
and length edge cases do not demand success for fields that need structure."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import gen_pytest
import parse_openapi
from suite_runner import failed, passed, run_suite

SPEC = {
    "openapi": "3.0.0", "info": {"title": "tenant", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
    "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}},
    "security": [{"bearerAuth": []}],
    "paths": {
        "/b/{business_id}/things": {
            "parameters": [{"name": "business_id", "in": "path", "required": True, "schema": {"type": "string"}}],
            "get": {"responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {
                "type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer"}}}}}}}}},
            "post": {"requestBody": {"required": True, "content": {"application/json": {"schema": {
                "type": "object", "required": ["name", "email"],
                "properties": {"name": {"type": "string", "maxLength": 20},
                               "email": {"type": "string", "maxLength": 30}}}}}},
                     "responses": {"201": {"description": "created", "content": {"application/json": {"schema": {
                         "type": "object", "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}}}}}}}},
        "/b/{business_id}/things/{id}": {
            "parameters": [{"name": "business_id", "in": "path", "required": True, "schema": {"type": "string"}},
                           {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
            "get": {"responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {
                "type": "object", "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}}}}},
                "404": {"description": "nf"}}},
            "delete": {"responses": {"204": {"description": "gone"}, "404": {"description": "nf"}}},
        },
    },
}


class Handler(BaseHTTPRequestHandler):
    things: dict = {}
    next_id = [1]

    def log_message(self, *a):
        pass

    def _send(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _route(self, method):
        parts = self.path.split("?")[0].strip("/").split("/")
        if len(parts) < 3 or parts[0] != "b":
            return self._send(404, {"detail": "no"})
        if parts[1] != "biz1":            # the tenant is resolved before anyone is authenticated
            return self._send(404, {"detail": "unknown business"})
        if self.headers.get("Authorization") != "Bearer tokA":
            return self._send(401, {"detail": "auth"})
        store = Handler.things
        if len(parts) == 3 and method == "GET":
            return self._send(200, list(store.values()))
        if len(parts) == 3 and method == "POST":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if "@" not in str(body.get("email", "")):
                return self._send(422, {"detail": "email"})
            i = Handler.next_id[0]
            Handler.next_id[0] += 1
            store[i] = {"id": i, "name": body.get("name")}
            return self._send(201, store[i])
        if len(parts) == 4:
            i = int(parts[3]) if parts[3].isdigit() else -1
            if i not in store:
                return self._send(404, {"detail": "nf"})
            if method == "GET":
                return self._send(200, store[i])
            if method == "DELETE":
                del store[i]
                return self._send(204)
        return self._send(405, {})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(scope="module")
def suite(tmp_path_factory):
    d = tmp_path_factory.mktemp("tenant")
    (d / "spec.json").write_text(json.dumps(SPEC))
    gen_pytest.generate(parse_openapi.build(str(d / "spec.json")), d / "suite", ["integration", "negative", "edge"])
    return d / "suite"


BASE = {"APITEST_BEARERAUTH_TOKEN": "tokA"}


def test_flow_without_tenant_id_skips_and_never_raises(suite, server):
    res, _ = run_suite(suite, server, BASE)
    flows = {k: v for k, v in res.items() if "test_flow_" in k}
    assert flows, "expected an integration flow for the nested resource"
    for k, (status, msg) in flows.items():
        assert status == "skipped", (k, status, msg)
        assert "APITEST_PATH_BUSINESS_ID" in msg


def test_flow_with_tenant_id_runs(suite, server):
    res, _ = run_suite(suite, server, {**BASE, "APITEST_PATH_BUSINESS_ID": "biz1"})
    flows = {k: v for k, v in res.items() if "test_flow_" in k}
    assert flows and all(s == "passed" for s, _ in flows.values()), flows


def test_missing_credentials_probe_tolerates_404_for_an_invented_id(suite, server):
    res, _ = run_suite(suite, server, BASE)
    probes = {k: v for k, v in res.items() if "rejects_missing_credentials" in k}
    assert probes and all(s == "passed" for s, _ in probes.values()), probes


def test_missing_credentials_probe_is_strict_with_a_real_id(suite, server):
    res, _ = run_suite(suite, server, {**BASE, "APITEST_PATH_BUSINESS_ID": "biz1", "APITEST_PATH_ID": "1"})
    probes = {k: v for k, v in res.items() if "rejects_missing_credentials" in k}
    assert probes and all(s == "passed" for s, _ in probes.values()), probes


def test_max_length_on_an_email_field_only_checks_for_a_clean_answer(suite, server):
    res, _ = run_suite(suite, server, {**BASE, "APITEST_PATH_BUSINESS_ID": "biz1"})
    mx = {k: v for k, v in res.items() if "email_max_length" in k}
    assert mx and all(s == "passed" for s, _ in mx.values()), mx   # a run of "a" gets 422, which is a clean rejection
    assert not [k for k in failed(res) if "max_length" in k], failed(res)
    assert passed(res)
