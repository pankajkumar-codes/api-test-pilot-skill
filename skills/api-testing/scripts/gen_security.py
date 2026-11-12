"""Generator for the OWASP API Security Top 10 (2023) pytest module (see references/security.md).

Active probes carry @pytest.mark.intrusive and are skipped by conftest.py unless the safety gate passes
(non-production target + authorization). Passive checks (headers, CORS, data exposure) are plain GETs.
"""
from __future__ import annotations

import re

from gen_common import (
    BODY_METHODS, SAFE_METHODS, Expr, Module, has_json_body, is_admin_endpoint, is_list_endpoint, rate_probe, params_in, path_params,
    py, sample_body, supported_body, writable_props,
)

PRIVILEGED = {"is_admin": True, "isAdmin": True, "admin": True, "role": "admin", "owner_id": 424242,
              "verified": True, "id": 987654321, "balance": 999999}
INJECTION = ["' OR '1'='1", "1; SELECT 1", '{"$ne": null}', "$(id)", "../../../../etc/passwd",
             "<script>apitest</script>", "{{7*7}}"]
SSRF_URLS = ["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:22/", "file:///etc/passwd"]
SSRF_NAME_RE = re.compile(r"(^|_)(url|uri|callback|webhook|redirect|return|target|dest|destination|feed|link|href|proxy)($|_)")
LIMIT_NAMES = {"limit", "page_size", "pagesize", "per_page", "perpage", "size", "count", "max", "take", "first"}


def _snake(n: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", n).replace("-", "_").lower()


def _ref(ep: dict) -> str:
    return f"EP[{ep['id']!r}]"


def _simple_get(doc: dict, need_auth: bool | None = None) -> dict | None:
    for ep in doc["endpoints"]:
        if ep["method"] == "GET" and not path_params(ep) and not any(p.get("required") for p in ep.get("parameters", [])
                                                                      if p["in"] in ("query", "header")):
            if need_auth is None or bool(ep.get("auth_required")) == need_auth:
                return ep
    return None


def generate(doc: dict, mod: Module) -> None:
    eps = [e for e in doc["endpoints"] if supported_body(e)]
    _api2(doc, eps, mod)
    _api1(eps, mod)
    _api3(eps, mod)
    _api4(doc, eps, mod)
    _api5(eps, mod)
    _api7(eps, mod)
    _api8(doc, eps, mod)
    _injection(eps, mod)


def _api2(doc: dict, eps: list[dict], mod: Module) -> None:
    for ep in eps:
        if not ep.get("auth_required"):
            continue
        ids = ""
        if path_params(ep):  # auth must be evaluated before existence; probe with an unknown id
            ids = ", path={" + ", ".join(f'{p["name"]!r}: api.missing_id({((p.get("schema") or {}).get("type") or "string")!r}, None)'
                                          for p in path_params(ep)) + "}"
        mod.add("@pytest.mark.intrusive\n"
                f"def {mod.unique('test_api2_invalid_credentials_rejected_' + ep['id'])}(api):\n"
                f"    resp = api.send({_ref(ep)}, auth=\"invalid\"{ids})\n"
                '    rt.expect_denied(resp, "API2:2023 broken authentication", "garbage credentials were not rejected")\n')
    bearer = next((n for n, s in doc.get("security_schemes", {}).items()
                   if s.get("type") in ("oauth2", "openIdConnect") or (s.get("type") == "http" and (s.get("scheme") or "").lower() == "bearer")), None)
    probe = _simple_get(doc, need_auth=True)
    if bearer and probe:
        mod.add("@pytest.mark.intrusive\n"
                '@pytest.mark.parametrize("variant", ["bad_signature", "alg_none", "stripped_signature"])\n'
                f"def {mod.unique('test_api2_tampered_jwt_rejected')}(api, variant):\n"
                f"    token = api.token({bearer!r})\n    variants = rt.jwt_variants(token)\n"
                '    if not variants:\n        pytest.skip("bearer token is not a JWT")\n'
                f'    resp = api.send({_ref(probe)}, auth="none", headers={{"Authorization": "Bearer " + variants[variant]}})\n'
                '    rt.expect_denied(resp, "API2:2023 broken authentication", f"tampered JWT ({variant}) was accepted")\n')
        mod.add("@pytest.mark.intrusive\n"
                f"def {mod.unique('test_api2_expired_token_rejected')}(api):\n"
                '    expired = os.environ.get("APITEST_EXPIRED_TOKEN")\n'
                '    if not expired:\n        pytest.skip("set APITEST_EXPIRED_TOKEN to an expired token for this check")\n'
                f'    resp = api.send({_ref(probe)}, auth="none", headers={{"Authorization": "Bearer " + expired}})\n'
                '    rt.expect_denied(resp, "API2:2023 broken authentication", "expired token was accepted")\n')


def _api1(eps: list[dict], mod: Module) -> None:
    for ep in eps:
        if not (path_params(ep) and ep.get("auth_required") and ep["method"] in ("GET", "PUT", "PATCH", "DELETE")):
            continue
        pre = "    rt.require_destructive()\n" if ep["method"] != "GET" else ""
        mod.add("@pytest.mark.intrusive\n"
                f"def {mod.unique('test_api1_bola_' + ep['id'])}(api):\n    ep = {_ref(ep)}\n{pre}"
                "    resp = api.send(ep, victim=True)  # ids of ANOTHER user's objects from APITEST_VICTIM_<PARAM>\n"
                '    rt.expect_denied(resp, "API1:2023 BOLA", "user A can reach an object owned by another user")\n')


def _api3(eps: list[dict], mod: Module) -> None:
    for ep in eps:
        if ep["method"] in SAFE_METHODS and ep["method"] != "OPTIONS" and not path_params(ep):
            mod.add(f"def {mod.unique('test_api3_no_sensitive_fields_' + ep['id'])}(api):\n"
                    f"    resp = api.send({_ref(ep)})\n    rt.expect_no_sensitive_fields(resp)\n")
        if ep["method"] in BODY_METHODS and has_json_body(ep) and isinstance(sample_body(ep), dict):
            declared = set(writable_props(ep["request_body"].get("schema")))
            extras = {k: v for k, v in PRIVILEGED.items() if k not in declared}
            mod.add("@pytest.mark.intrusive\n"
                    f"def {mod.unique('test_api3_mass_assignment_' + ep['id'])}(api):\n    ep = {_ref(ep)}\n"
                    f"    injected = {py(extras)}\n"
                    "    resp = api.send(ep, body={**ep.body, **injected})\n    rt.expect_not_assigned(resp, injected)\n")


def _api4(doc: dict, eps: list[dict], mod: Module) -> None:
    for ep in eps:
        if not is_list_endpoint(ep):
            continue
        for p in params_in(ep, "query"):
            if _snake(p["name"]) in LIMIT_NAMES:
                cap = p.get("schema", {}).get("maximum") or 1000
                mod.add("@pytest.mark.intrusive\n"
                        f"def {mod.unique('test_api4_page_size_capped_' + ep['id'])}(api):\n    ep = {_ref(ep)}\n"
                        f"    resp = api.send(ep, query={{**ep.query, {p['name']!r}: 1_000_000}})\n"
                        f"    rt.expect_page_capped(resp, {cap})\n")
                break
    probe = rate_probe(doc)
    if probe:
        mod.add("@pytest.mark.intrusive\n"
                f"def {mod.unique('test_api4_rate_limiting_present')}(api):\n"
                '    rt.require_flag("APITEST_EXPECT_RATE_LIMIT", "this API is expected to rate-limit")\n'
                f"    ep = {_ref(probe)}\n    n = int(os.environ.get(\"APITEST_RATE_BURST\", \"40\"))\n"
                "    codes = [api.send(ep).status_code for _ in range(n)]\n"
                '    assert 429 in codes, f"[MEDIUM][API4:2023] {n} rapid requests were never throttled (no 429)"\n')


def _api5(eps: list[dict], mod: Module) -> None:
    for ep in eps:
        if not ep.get("auth_required") or not is_admin_endpoint(ep):
            continue
        pre = "    rt.require_destructive()\n" if ep["method"] not in SAFE_METHODS else ""
        mod.add("@pytest.mark.intrusive\n"
                f"def {mod.unique('test_api5_bfla_' + ep['id'])}(api):\n    ep = {_ref(ep)}\n"
                '    rt.require_flag("APITEST_ASSUME_NON_ADMIN", "confirm the configured credentials belong to an ordinary, non-admin user")\n'
                f"{pre}    resp = api.send(ep)\n"
                '    rt.expect_denied(resp, "API5:2023 BFLA", "ordinary credentials can call an administrative function")\n')


def _api7(eps: list[dict], mod: Module) -> None:
    for ep in eps:
        targets: list[tuple[str, str]] = []
        for p in params_in(ep, "query"):
            if SSRF_NAME_RE.search(_snake(p["name"])):
                targets.append(("query", p["name"]))
        if ep["method"] in BODY_METHODS and has_json_body(ep) and isinstance(sample_body(ep), dict):
            targets += [("body", k) for k in writable_props(ep["request_body"].get("schema")) if SSRF_NAME_RE.search(_snake(k))]
        for where, name in targets[:2]:
            arg = (f"query={{**ep.query, {name!r}: url}}" if where == "query"
                   else f"body={{**ep.body, {name!r}: url}}")
            mod.add("@pytest.mark.intrusive\n"
                    f"@pytest.mark.parametrize(\"url\", {py(SSRF_URLS)})\n"
                    f"def {mod.unique('test_api7_ssrf_' + ep['id'] + '_' + name)}(api, url):\n    ep = {_ref(ep)}\n"
                    f"    resp = api.send(ep, {arg})\n    rt.expect_ssrf_blocked(resp, url)\n")


def _api8(doc: dict, eps: list[dict], mod: Module) -> None:
    probe = _simple_get(doc)
    if probe:
        mod.add(f"def {mod.unique('test_api8_security_headers')}(api):\n    resp = api.send({_ref(probe)})\n"
                "    rt.expect_security_headers(resp)\n")
        mod.add(f"def {mod.unique('test_api8_cors_not_permissive')}(api):\n    origin = \"https://untrusted.example\"\n"
                f"    resp = api.send({_ref(probe)}, headers={{\"Origin\": origin}})\n    rt.expect_cors_safe(resp, origin)\n")
    body_ep = next((e for e in eps if e["method"] in BODY_METHODS and has_json_body(e) and not path_params(e)), None)
    if body_ep:
        mod.add(f"def {mod.unique('test_api8_errors_do_not_leak_internals')}(api):\n"
                f'    resp = api.send({_ref(body_ep)}, raw="{{\\"broken\\": ", content_type="application/json")\n'
                "    rt.expect_no_error_leak(resp)\n")


def _injection(eps: list[dict], mod: Module) -> None:
    for ep in eps:
        cases: list[tuple] = []
        for p in params_in(ep, "query"):
            sch = p.get("schema") or {}
            if sch.get("type") == "string" and not sch.get("enum") and not sch.get("format") and len(cases) < 20:
                cases += [(f"query_{p['name']}[{i}]", {"query": Expr(f"{{**_ep.query, {p['name']!r}: {pl!r}}}")})
                          for i, pl in enumerate(INJECTION)]
        if ep["method"] in BODY_METHODS and has_json_body(ep) and isinstance(sample_body(ep), dict):
            for k, sch in list(writable_props(ep["request_body"].get("schema")).items())[:3]:
                if sch.get("type") == "string" and not sch.get("enum") and not sch.get("format"):
                    cases += [(f"body_{k}[{i}]", {"body": Expr(f"{{**_ep.body, {k!r}: {pl!r}}}")})
                              for i, pl in enumerate(INJECTION)]
        for p in path_params(ep)[:1]:
            for i, pl in enumerate(("../../../../etc/passwd", "%2e%2e%2f%2e%2e%2fetc%2fpasswd", "1 OR 1=1")):
                cases.append((f"path_{p['name']}[{i}]", {"path": Expr(f"{{{p['name']!r}: rt.RawPath({pl!r})}}")}))
        if not cases:
            continue
        table = mod.unique("INJECT_" + ep["id"])
        # case values are expressions evaluated at import time against EP[...]
        rows = ",\n".join(f"    ({c[0]!r}, {_render_kw(c[1])})" for c in cases)
        mod.add(f'_ep = {_ref(ep)}\n{table} = [\n{rows},\n]\n', is_test=False)
        mod.add("@pytest.mark.intrusive\n"
                f'@pytest.mark.parametrize("label,kw", {table}, ids=[c[0] for c in {table}])\n'
                f"def {mod.unique('test_injection_' + ep['id'])}(api, label, kw):\n"
                f"    resp = api.send({_ref(ep)}, **kw)\n    rt.expect_injection_safe(resp, label)\n")


def _render_kw(kw: dict) -> str:
    return "{" + ", ".join(f"{k!r}: {v}" if isinstance(v, Expr) else f"{k!r}: {py(v)}" for k, v in kw.items()) + "}"
