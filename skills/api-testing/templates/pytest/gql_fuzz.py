"""GraphQL fuzzing for generated suites (shipped next to fuzz_runtime.py and gql_runtime.py). Generated file: regenerate, do not edit.

Same rules as the REST fuzzer: opt-in, `intrusive` (non-production, authorized targets only), seeded and bounded (cases, requests per
second and request budget per run, all from fuzz_runtime), findings never print response bodies. For one operation it sends hostile
variable values (wrong types, boundary numbers, oversize and unicode strings, injection strings, missing and extra variables), malformed
request envelopes (`query`, `variables` and `operationName` of the wrong type, malformed JSON, other content types) and damaged query text
(truncated, unclosed strings, huge names). Payloads always travel as variables, never glued into the query. A 4xx or a well-formed
`errors` answer is the right response to bad input and is never a finding; 5xx, hangs, dropped connections, stack traces and
database errors in the answer, sensitive fields in an error and very slow answers are.
"""
from __future__ import annotations

import itertools
import json
import random
from typing import Any

import pytest

import fuzz_runtime as fz
import gql_runtime as gr

ENVELOPES: list[tuple[str, Any]] = [
    ("query_number", {"query": 5}), ("query_null", {"query": None}), ("query_list", {"query": ["{ __typename }"]}),
    ("query_object", {"query": {"a": 1}}), ("no_query", {}), ("variables_string", {"query": "{ __typename }", "variables": "x"}),
    ("variables_list", {"query": "{ __typename }", "variables": [1]}), ("operation_name_number", {"query": "{ __typename }", "operationName": 5}),
    ("operation_name_long", {"query": "{ __typename }", "operationName": "A" * 10000}),
    ("persisted_query", {"extensions": {"persistedQuery": {"version": 1, "sha256Hash": "0" * 64}}}),
]


def build_cases(op: Any, base: dict, spec: dict, cfg: fz.Config) -> list[dict]:
    """Deterministic list of {label, send: keyword arguments for Gql.post}. Same seed and operation: same list."""
    rng = random.Random(f"{cfg.seed}:{op.id}")
    pool = fz._ordered(rng, fz.STRINGS + fz.NUMBERS + fz.WRONG_TYPES + [("long_string", "A" * min(cfg.max_bytes, 65536))])
    lead = {n: i for i, n in enumerate(("sql_quote", "nul", "crlf"))}   # the likeliest crashers always go first
    pool.sort(key=lambda it: lead.get(it[0], len(lead)))
    classes: list[list[dict]] = []

    paths = fz._leaf_paths(base)
    paths += [(name,) for name in spec.get("optional", []) if name not in base]
    value_cases: list[dict] = []
    for k in range(len(pool)):                       # round-robin over variables, each walking the payload pool
        for j, path in enumerate(paths or [()]):
            if not path:
                break
            name, val = pool[k % len(pool)]   # every variable meets the same payload at the same step
            value_cases.append({"label": f"variables.{fz._label(path)}={name}",
                                "send": {"query": op.query, "variables": fz._set(base, path, fz._capped(val, cfg))}})
        if k < len(paths) and paths[k] and paths[k][0] in base:
            value_cases.append({"label": f"variables.{fz._label(paths[k])}=missing", "send": {"query": op.query, "variables": fz._drop(base, paths[k])}})
        if k < len(fz.EXTRA_KEYS):
            key = fz.EXTRA_KEYS[k]
            value_cases.append({"label": f"variables.extra={key or 'empty'}", "send": {"query": op.query, "variables": {**base, key: {"admin": True}}}})
    classes += [value_cases[i::3] for i in range(3)]   # variable values get the biggest share of the cases

    struct = [("null", None), ("list", []), ("string", "x"), ("number", 0), ("deep_object", fz._nest(cfg.nest, "dict")),
              ("deep_list", fz._nest(cfg.nest, "list")), ("many_keys", {f"k{i}": i for i in range(500)})]
    classes.append([{"label": f"variables.whole={n}", "send": {"query": op.query, "variables": v}} for n, v in struct])

    text = op.query
    cuts = sorted({max(1, int(len(text) * f)) for f in (0.2, 0.4, 0.6, 0.8, 0.95)})
    broken = [(f"truncated_at_{c}", text[:c]) for c in cuts] + [
        ("nul_byte", text + "\x00"), ("unclosed_string", text + ' "'), ("unclosed_brace", text + " {"), ("closing_braces", text + "}" * 50),
        ("unicode_name", text.replace(op.name, op.name + "\u00e9\u20ac", 1)),
        ("empty_query", ""), ("only_comment", "# nothing"), ("huge_whitespace", text + " " * min(100000, cfg.max_bytes)),
    ]
    repeat = min(2000, cfg.max_bytes // max(1, len(op.name)))
    if repeat >= 2:                                   # a tiny byte limit leaves no room for a long name
        broken.append(("long_name", text.replace(op.name, op.name * repeat, 1)))
    classes.append([{"label": f"query={n}", "send": {"query": q, "variables": base}} for n, q in broken])

    envelopes = [{"label": f"envelope={n}", "send": {"query": "", "raw": json.dumps(body)}} for n, body in ENVELOPES]
    raws = [{"label": f"raw={n}", "send": {"query": "", "raw": b}} for n, b in fz.RAW_BODIES]
    rng.shuffle(raws)
    classes.append(envelopes + raws)
    payload = json.dumps({"query": op.query, "variables": base})
    classes.append([{"label": f"content_type={c}", "send": {"query": "", "raw": payload, "headers": {"Content-Type": c}}} for c in fz.CONTENT_TYPES])

    out: list[dict] = []
    for group in itertools.zip_longest(*[c for c in classes if c]):   # round-robin, so every class gets a turn
        out += [c for c in group if c is not None]
    return out[: cfg.cases]


def _leak(resp: Any) -> list[tuple[str, str, str]]:
    if gr.LEAK_RE.search(resp.text[:20000]):
        return [("leak", "HIGH", f"response ({resp.status_code}) leaks internals: stack trace or source path (body withheld)")]
    return []


def fuzz_operation(gql: Any, op: Any, spec: dict | None = None) -> None:
    """Run the fuzz cases for one operation; fail with a ranked list of findings (or pass)."""
    spec = spec or {}
    if not fz.selected(op.name):
        pytest.skip("not in APITEST_FUZZ_ENDPOINTS")
    cfg = fz.config()
    if spec.get("destructive"):
        gr.require_destructive()
    if op.kind == "mutation":
        gr.skip_writes_on_production()
    base = gql.resolve(op)
    cases = build_cases(op, base, spec, cfg)
    if not cases:
        pytest.skip("nothing to fuzz on this operation")
    fz.run_cases(f"{op.kind} {op.name}", cases,
                 lambda case: gql.post(case["send"].get("query") or "", case["send"].get("variables"), raw=case["send"].get("raw"),
                                       headers=case["send"].get("headers"), transient="raise", timeout=min(gql.timeout, 10.0)),
                 cfg, extra_judge=_leak)
