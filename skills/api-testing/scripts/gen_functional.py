"""Generators for smoke, positive, negative and edge pytest modules (see references/functional.md)."""
from __future__ import annotations

from gen_common import (
    admin_line, BODY_METHODS, SAFE_METHODS, Expr, Module, case_table, error_codes, has_json_body, optional_query,
    params_in, path_params, py, sample_body, supported_body, writable_props,
)

SPECIAL = "!@#$%^&*()_+-={}[]|\\:;\"'<>,.?/~`"


def _ref(ep: dict) -> str:
    return f'EP["{ep["id"]}"]'


def _missing_ids(ep: dict) -> str:
    items = ", ".join(
        f'{py(p["name"])}: api.missing_id({py((p.get("schema") or {}).get("type") or "string")}, '
        f'{py((p.get("schema") or {}).get("format"))})' for p in path_params(ep))
    return "{" + items + "}"


def _plain_string(sch: dict) -> bool:
    return sch.get("type") == "string" and not sch.get("enum") and not sch.get("format") and not sch.get("pattern")


def smoke(doc: dict, mod: Module) -> None:
    for ep in doc["endpoints"]:
        if ep["method"] in SAFE_METHODS and ep["method"] != "OPTIONS":
            name = mod.unique(f"test_smoke_{ep['id']}")
            mod.add(f'@pytest.mark.smoke\ndef {name}(api):\n{admin_line(ep)}    resp = api.send({_ref(ep)})\n'
                    f'    rt.expect_ok(resp, {_ref(ep)}, "smoke")\n')


def positive(doc: dict, mod: Module) -> None:
    for ep in doc["endpoints"]:
        if not supported_body(ep):
            continue
        keys = _echo_keys(ep)
        lines = [f"def {mod.unique('test_' + ep['id'] + '_valid_request')}(api):", f"    ep = {_ref(ep)}"]
        if admin_line(ep):
            lines.append(admin_line(ep).rstrip("\n"))
        if ep["method"] == "DELETE":
            lines.append("    rt.require_destructive()")
        lines += ["    resp = api.send(ep)", "    rt.expect_ok(resp, ep)", "    rt.expect_json(resp, ep)"]
        if keys and ep["method"] in BODY_METHODS:
            lines.append(f"    rt.expect_echo(resp, ep.body, {py(keys)})")
        mod.add("\n".join(lines) + "\n")
        opt = optional_query(ep)
        if opt and ep["method"] in SAFE_METHODS:
            name = mod.unique(f"test_{ep['id']}_with_optional_params")
            mod.add(f"def {name}(api):\n    ep = {_ref(ep)}\n{admin_line(ep)}"
                    f"    resp = api.send(ep, query={{**ep.query, **{py(opt)}}})\n    rt.expect_ok(resp, ep)\n")


def _echo_keys(ep: dict) -> list[str]:
    req = writable_props(ep.get("request_body", {}).get("schema") if ep.get("request_body") else None)
    resp_schema = None
    for code in sorted(c for c in ep.get("responses", {}) if str(c).startswith("2")):
        resp_schema = ep["responses"][code].get("schema")
        if resp_schema:
            break
    rprops = (resp_schema or {}).get("properties") or {}
    return [k for k, v in req.items()
            if k in rprops and v.get("type") in ("string", "integer", "number", "boolean")
            and not v.get("writeOnly") and "password" not in k.lower()]


def negative(doc: dict, mod: Module) -> None:
    for ep in doc["endpoints"]:
        if not supported_body(ep):
            continue
        ref, eid = _ref(ep), ep["id"]
        if ep.get("auth_required"):
            body = f"    ep = {ref}\n"
            path = f", path={_missing_ids(ep)}" if path_params(ep) else ""
            mod.add(f"def {mod.unique('test_' + eid + '_rejects_missing_credentials')}(api):\n{body}"
                    f"    resp = api.send(ep, auth=\"none\"{path})\n"
                    '    rt.expect_status(resp, (401, 403), "request without credentials must be rejected")\n')
        if path_params(ep) and ep["method"] in ("GET", "PUT", "PATCH", "DELETE"):
            codes = error_codes(ep)
            if ep["method"] in ("GET", "PATCH"):
                check = ('    rt.expect_status(resp, (404,), "unknown id")' if 404 in codes
                         else "    rt.expect_rejected(resp, ep, \"unknown id\")")
            else:  # DELETE/PUT may be idempotent or upsert: 404 only when the spec says so
                check = ('    rt.expect_status(resp, (404,), "unknown id")' if 404 in codes
                         else '    rt.expect_no_crash(resp, "unknown id")')
            pre = "    rt.require_destructive()\n" if ep["method"] == "DELETE" else ""
            mod.add(f"def {mod.unique('test_' + eid + '_unknown_id_not_found')}(api):\n    ep = {ref}\n{pre}"
                    f"    resp = api.send(ep, path={_missing_ids(ep)})\n{check}\n")
        if ep["method"] in BODY_METHODS and has_json_body(ep):
            cases = [("malformed_json", {"raw": '{"broken": ', "content_type": "application/json"}),
                     ("wrong_content_type", {"raw": "name=x", "content_type": "text/plain"})]
            if ep["request_body"].get("required"):
                cases.append(("empty_body", {"raw": "", "content_type": "application/json"}))
            table = mod.unique("MALFORMED_" + eid)
            mod.add(case_table(table, cases), is_test=False)
            mod.add(f'@pytest.mark.parametrize("label,kw", {table}, ids=[c[0] for c in {table}])\n'
                    f"def {mod.unique('test_' + eid + '_malformed_requests')}(api, label, kw):\n"
                    f"    ep = {ref}\n    resp = api.send(ep, **kw)\n    rt.expect_rejected(resp, ep, label)\n")


def edge(doc: dict, mod: Module) -> None:
    for ep in doc["endpoints"]:
        if not supported_body(ep):
            continue
        cases: list[tuple] = []
        base = sample_body(ep) if ep["method"] in BODY_METHODS else None
        if isinstance(base, dict):
            cases += _body_edges(ep, base)
        cases += _query_edges(ep)
        if not cases:
            continue
        table = mod.unique("EDGE_" + ep["id"])
        mod.add(case_table(table, cases), is_test=False)
        mod.add(f'@pytest.mark.parametrize("label,kw,expect", {table}, ids=[c[0] for c in {table}])\n'
                f"def {mod.unique('test_' + ep['id'] + '_edge')}(api, label, kw, expect):\n"
                f"    ep = {_ref(ep)}\n    resp = api.send(ep, **kw)\n"
                '    rt.check(resp, ep, expect, kw.get("body"), label)\n')


def _body_edges(ep: dict, base: dict) -> list[tuple]:
    schema = ep["request_body"].get("schema") or {}
    props = writable_props(schema)
    out: list[tuple] = []
    big_done = False
    for k, sch in list(props.items())[:6]:
        def b(v, key=k):
            return {"body": {**base, key: v}}
        t = sch.get("type")
        if _plain_string(sch):
            lo, hi = sch.get("minLength"), sch.get("maxLength")
            if hi is None or hi >= 12:
                out.append((f"{k}_unicode", b("Zoë 🐶 日本語"), "echo"))
            out.append((f"{k}_special_chars", b(SPECIAL[: hi] if hi else SPECIAL), "no_crash"))
            out.append((f"{k}_empty_string", b(""), "reject" if (lo or 0) >= 1 else "no_crash"))
            if (lo or 0) >= 1:
                out.append((f"{k}_min_length", b(Expr(f'"a" * {lo}')), "success"))
            if hi:
                out.append((f"{k}_max_length", b(Expr(f'"a" * {hi}')), "success"))
            elif not big_done:
                out.append((f"{k}_100k_chars", b(Expr('"a" * 100_000')), "no_crash"))
                big_done = True
        elif t in ("integer", "number") and not sch.get("enum"):
            if isinstance(sch.get("minimum"), (int, float)) and not sch.get("exclusiveMinimum"):
                out.append((f"{k}_min_boundary", b(sch["minimum"]), "success"))
            if isinstance(sch.get("maximum"), (int, float)) and not sch.get("exclusiveMaximum"):
                out.append((f"{k}_max_boundary", b(sch["maximum"]), "success"))
            if t == "integer":
                out.append((f"{k}_int64_overflow", b(9223372036854775808), "no_crash"))
        if not sch.get("nullable") and k in (schema.get("required") or []):
            out.append((f"{k}_null", b(None), "no_crash"))
    return out


def _query_edges(ep: dict) -> list[tuple]:
    out: list[tuple] = []
    base = {p["name"]: v for p in params_in(ep, "query") if p.get("required")
            and (v := _qv(p)) is not None}
    for p in params_in(ep, "query")[:6]:
        sch, n = p.get("schema") or {}, p["name"]

        def q(v, name=n):
            return {"query": {**base, name: v}}
        if _plain_string(sch):
            out.append((f"query_{n}_unicode", q("Zoë 🐶 日本語"), "no_crash"))
            out.append((f"query_{n}_special_chars", q(SPECIAL), "no_crash"))
            out.append((f"query_{n}_empty", q(""), "no_crash"))
        elif sch.get("type") in ("integer", "number") and not sch.get("enum"):
            if isinstance(sch.get("minimum"), (int, float)):
                out.append((f"query_{n}_min_boundary", q(sch["minimum"]), "success"))
            if isinstance(sch.get("maximum"), (int, float)):
                out.append((f"query_{n}_max_boundary", q(sch["maximum"]), "success"))
    return out


def _qv(p: dict):
    from gen_common import param_value
    return param_value(p)


GENERATORS = {"smoke": smoke, "positive": positive, "negative": negative, "edge": edge}
