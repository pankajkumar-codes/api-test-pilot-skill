"""Tenant isolation matrix (user A's token against another tenant's id) and the role matrix (APITEST_TOKEN_<ROLE>)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import gen_pytest
import parse_openapi
from suite_runner import run_suite

OK = {"200": {"description": "ok"}}
P = {"name": "business_id", "in": "path", "required": True, "schema": {"type": "string"}}
SPEC = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
        "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}}, "security": [{"bearerAuth": []}],
        "paths": {f"/b/{{business_id}}/{n}": {"parameters": [P], "get": {"responses": OK}} for n in ("things", "reports", "settings")}}
SPEC["paths"]["/b/{business_id}/invoices"] = {"parameters": [P], "post": {
    "operationId": "createInvoice", "requestBody": {"required": True, "content": {"application/json": {"schema": {
        "type": "object", "required": ["amount"], "properties": {"amount": {"type": "integer"}}}}}},
    "responses": {"201": {"description": "created"}}}}
STATE = {"leaky": False, "billing_can_create": False}
TOKENS = {"tokA": "t1", "tokOwner": "t1", "tokBilling": "t1"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _go(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        tok = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
        tenant = self.path.split("/")[2]
        code = 200
        if tok not in TOKENS:
            code = 401
        elif tenant != TOKENS[tok] and not STATE["leaky"]:
            code = 404
        elif self.command == "POST":
            code = 403 if tok == "tokBilling" and not STATE["billing_can_create"] else 201
        self.send_response(code)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    do_GET = do_POST = _go


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("tm")
    (d / "spec.json").write_text(json.dumps(SPEC))
    gen_pytest.generate(parse_openapi.build(str(d / "spec.json")), d / "suite", ["security"])
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield d, f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


BASE = {"APITEST_BEARERAUTH_TOKEN": "tokA", "APITEST_PATH_BUSINESS_ID": "t1", "APITEST_VICTIM_BUSINESS_ID": "t2"}


def matrix(res):
    return {k: v for k, v in res.items() if "tenant_isolation" in k}


def test_isolated_tenants_pass_and_writes_are_not_called_by_default(env):
    d, url = env
    STATE.update(leaky=False)
    res, _ = run_suite(d / "suite", url, BASE, ["-k", "tenant_isolation"])
    got = matrix(res)
    assert len(got) == 4
    assert sorted(s for s, _ in got.values()) == ["passed", "passed", "passed", "skipped"]
    assert any("APITEST_TENANT_WRITES" in m for s, m in got.values() if s == "skipped")


def test_leaky_tenant_is_a_finding(env):
    d, url = env
    STATE.update(leaky=True)
    try:
        res, _ = run_suite(d / "suite", url, BASE, ["-k", "tenant_isolation"])
    finally:
        STATE.update(leaky=False)
    failed_ = [(k, m) for k, (s, m) in matrix(res).items() if s == "failed"]
    assert len(failed_) == 3 and all("API1:2023" in m and "[HIGH]" in m for _, m in failed_)


def test_victim_id_missing_skips_with_the_variable_name(env):
    d, url = env
    res, _ = run_suite(d / "suite", url, {k: v for k, v in BASE.items() if "VICTIM" not in k}, ["-k", "tenant_isolation"])
    got = matrix(res)
    assert all(s == "skipped" for s, _ in got.values())
    assert sum("APITEST_VICTIM_BUSINESS_ID" in m for _, m in got.values()) == 3


def test_tenant_param_can_be_forced():
    doc = parse_openapi.parse(SPEC)
    import gen_security
    assert gen_security.tenant_param(doc, doc["endpoints"]) == "business_id"
    assert gen_security.tenant_param({**doc, "tenant_param": "x"}, doc["endpoints"]) == "x"


def test_role_matrix(env, tmp_path):
    d, url = env
    mfile = tmp_path / "roles.json"
    mfile.write_text(json.dumps({"createInvoice": {"allow": ["owner"], "deny": ["billing"]}, "unknownOp": {"allow": ["owner"]}}))
    extra = {**BASE, "APITEST_ROLE_MATRIX": str(mfile), "APITEST_TOKEN_OWNER": "tokOwner", "APITEST_TOKEN_BILLING": "tokBilling"}
    STATE.update(billing_can_create=False)
    res, _ = run_suite(d / "suite", url, extra, ["-k", "role_matrix"])
    assert sorted(s for s, _ in res.values()) == ["passed", "passed"], res
    STATE.update(billing_can_create=True)
    try:
        res, _ = run_suite(d / "suite", url, extra, ["-k", "role_matrix"])
    finally:
        STATE.update(billing_can_create=False)
    bad = [m for k, (s, m) in res.items() if s == "failed"]
    assert len(bad) == 1 and "BFLA" in bad[0] and "billing" in bad[0]


def test_role_without_token_skips_and_no_matrix_means_skipped(env, tmp_path):
    d, url = env
    res, _ = run_suite(d / "suite", url, BASE, ["-k", "role_matrix"])
    assert all(s == "skipped" for s, _ in res.values())
    mfile = tmp_path / "r.json"
    mfile.write_text(json.dumps({"createInvoice": {"allow": ["owner"]}}))
    res, _ = run_suite(d / "suite", url, {**BASE, "APITEST_ROLE_MATRIX": str(mfile)}, ["-k", "role_matrix"])
    assert [(s, "APITEST_TOKEN_OWNER" in m) for s, m in res.values()] == [("skipped", True)]
