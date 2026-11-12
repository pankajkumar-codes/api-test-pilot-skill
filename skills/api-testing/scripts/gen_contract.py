"""Generator for the contract pytest module: live responses vs the documented status, content type and schema.

Breaking changes between two spec versions are a different tool: spec_diff.py (see references/contract.md).
"""
from __future__ import annotations

from gen_common import admin_line, Module, path_params, success_codes, supported_body


def _ref(ep: dict) -> str:
    return f"EP[{ep['id']!r}]"


def _missing_ids(ep: dict) -> str:
    return "{" + ", ".join(
        f'{p["name"]!r}: api.missing_id({((p.get("schema") or {}).get("type") or "string")!r}, '
        f'{(p.get("schema") or {}).get("format")!r})' for p in path_params(ep)) + "}"


def generate(doc: dict, mod: Module) -> None:
    for ep in doc["endpoints"]:
        if not supported_body(ep) or not ep.get("responses"):
            continue
        ref, eid = _ref(ep), ep["id"]
        if ep["method"] == "GET" and success_codes(ep):
            mod.add(f"def {mod.unique('test_' + eid + '_response_contract')}(api):\n    ep = {ref}\n{admin_line(ep)}"
                    "    resp = api.send(ep)\n    rt.expect_ok(resp, ep)\n    rt.expect_contract(resp, ep)\n")
        elif ep["method"] == "POST" and success_codes(ep) and not path_params(ep):
            mod.add(f"def {mod.unique('test_' + eid + '_response_contract')}(api):\n    ep = {ref}\n{admin_line(ep)}"
                    "    resp = api.send(ep)\n    rt.expect_ok(resp, ep)\n    rt.expect_contract(resp, ep)\n")
        resp_docs = ep["responses"]
        if "401" in resp_docs and resp_docs["401"].get("schema") and ep.get("auth_required"):
            path = f", path={_missing_ids(ep)}" if path_params(ep) else ""
            mod.add(f"def {mod.unique('test_' + eid + '_error_contract_401')}(api):\n    ep = {ref}\n"
                    f'    resp = api.send(ep, auth="none"{path})\n    rt.expect_status(resp, (401,))\n'
                    "    rt.expect_contract(resp, ep)\n")
        if "404" in resp_docs and resp_docs["404"].get("schema") and path_params(ep) and ep["method"] == "GET":
            mod.add(f"def {mod.unique('test_' + eid + '_error_contract_404')}(api):\n    ep = {ref}\n"
                    f"    resp = api.send(ep, path={_missing_ids(ep)})\n    rt.expect_status(resp, (404,))\n"
                    "    rt.expect_contract(resp, ep)\n")
