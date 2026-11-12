"""Generator for the data-validation pytest module (types, formats, enums, lengths, ranges, required fields).

Every case sends an otherwise valid request with ONE violation and expects a 4xx rejection
(see references/data-validation.md). A server that accepts the value is a finding, a 5xx is a worse one.
"""
from __future__ import annotations

from gen_common import (
    BODY_METHODS, Expr, Module, case_table, has_json_body, param_value, params_in, path_params, sample_body,
    supported_body, without, writable_props,
)

WRONG_TYPE = {"string": 12345, "integer": "not-a-number", "number": "not-a-number", "boolean": "maybe",
              "array": "not-a-list", "object": "not-an-object"}
BAD_FORMAT = {"email": "not-an-email", "uuid": "not-a-uuid", "date": "2024-13-45", "date-time": "yesterday",
              "uri": "not a uri", "ipv4": "999.1.1.1"}


def generate(doc: dict, mod: Module) -> None:
    for ep in doc["endpoints"]:
        if not supported_body(ep):
            continue
        cases: list[tuple] = []
        if ep["method"] in BODY_METHODS and has_json_body(ep):
            base = sample_body(ep)
            if isinstance(base, dict):
                cases += _body_cases(ep, base)
        cases += _param_cases(ep)
        if not cases:
            continue
        table = mod.unique("INVALID_" + ep["id"])
        mod.add(case_table(table, cases), is_test=False)
        mod.add(f'@pytest.mark.parametrize("label,kw", {table}, ids=[c[0] for c in {table}])\n'
                f"def {mod.unique('test_' + ep['id'] + '_rejects_invalid_input')}(api, label, kw):\n"
                f'    ep = EP[{ep["id"]!r}]\n    resp = api.send(ep, **kw)\n'
                "    rt.expect_rejected(resp, ep, label)\n")


def _body_cases(ep: dict, base: dict) -> list[tuple]:
    schema = ep["request_body"].get("schema") or {}
    props = writable_props(schema)
    required = [r for r in schema.get("required") or [] if r in props]
    out: list[tuple] = []
    for k in required[:6]:
        out.append((f"missing_{k}", {"body": without(base, k)}))
    for k, sch in list(props.items())[:8]:
        def b(v, key=k):
            return {"body": {**base, key: v}}
        t = sch.get("type")
        t = t[0] if isinstance(t, list) and t else t
        if t in WRONG_TYPE and not sch.get("enum"):
            out.append((f"{k}_wrong_type", b(WRONG_TYPE[t])))
        if sch.get("enum"):
            out.append((f"{k}_not_in_enum", b("__not_in_enum__")))
        if sch.get("format") in BAD_FORMAT:
            out.append((f"{k}_bad_{sch['format']}", b(BAD_FORMAT[sch["format"]])))
        if t == "string":
            lo, hi = sch.get("minLength"), sch.get("maxLength")
            if isinstance(lo, int) and lo >= 1:
                out.append((f"{k}_below_min_length", b(Expr(f'"a" * {lo - 1}'))))
            if isinstance(hi, int):
                out.append((f"{k}_above_max_length", b(Expr(f'"a" * {hi + 1}'))))
        if t in ("integer", "number"):
            lo, hi = sch.get("minimum"), sch.get("maximum")
            if isinstance(lo, (int, float)):
                out.append((f"{k}_below_minimum", b(lo - 1 if not sch.get("exclusiveMinimum") else lo)))
            if isinstance(hi, (int, float)):
                out.append((f"{k}_above_maximum", b(hi + 1 if not sch.get("exclusiveMaximum") else hi)))
        if t == "array":
            lo, hi = sch.get("minItems"), sch.get("maxItems")
            if isinstance(lo, int) and lo >= 1:
                out.append((f"{k}_too_few_items", b(Expr(f"[None] * {lo - 1}"))))
            if isinstance(hi, int):
                out.append((f"{k}_too_many_items", b(Expr(f"[None] * {hi + 1}"))))
    if schema.get("additionalProperties") is False:
        out.append(("unexpected_property", {"body": {**base, "apitest_unexpected": "x"}}))
    return out


def _param_cases(ep: dict) -> list[tuple]:
    out: list[tuple] = []
    base_q = {p["name"]: v for p in params_in(ep, "query") if p.get("required") and (v := param_value(p)) is not None}
    for p in params_in(ep, "query")[:8]:
        sch, n = p.get("schema") or {}, p["name"]
        t = sch.get("type")

        def q(v, name=n):
            return {"query": {**base_q, name: v}}
        if p.get("required"):
            out.append((f"query_{n}_missing", {"query": {k: v for k, v in base_q.items() if k != n}}))
        if t in ("integer", "number"):
            out.append((f"query_{n}_wrong_type", q("not-a-number")))
            if isinstance(sch.get("minimum"), (int, float)):
                out.append((f"query_{n}_below_minimum", q(sch["minimum"] - 1)))
            if isinstance(sch.get("maximum"), (int, float)):
                out.append((f"query_{n}_above_maximum", q(sch["maximum"] + 1)))
        if sch.get("enum"):
            out.append((f"query_{n}_not_in_enum", q("__not_in_enum__")))
    for p in path_params(ep)[:3]:
        sch = p.get("schema") or {}
        if sch.get("type") in ("integer", "number"):
            out.append((f"path_{p['name']}_wrong_type", {"path": {p["name"]: "not-a-number"}}))
    return out
