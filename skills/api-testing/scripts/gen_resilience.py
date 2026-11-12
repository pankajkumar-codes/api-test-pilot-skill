"""Generator for resilience tests: idempotency, pagination, response-time budget, rate-limit behaviour.

The rate-limit burst is small (APITEST_RATE_BURST, default 30), sequential and marked intrusive.
It is not a load test: sustained or concurrent traffic belongs to Phase 3 and is safety-gated separately.
"""
from __future__ import annotations

import re

from gen_common import (
    admin_line, rate_probe, Module, has_json_body, is_list_endpoint, params_in, sample_body, supported_body,
)

LIMIT_NAMES = {"limit", "page_size", "pagesize", "per_page", "perpage", "size", "count", "max", "take", "first"}
OFFSET_NAMES = {"offset", "skip", "start"}
PAGE_NAMES = {"page", "page_number", "pagenumber", "page_no"}


def _snake(n: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", n).replace("-", "_").lower()


def _ref(ep: dict) -> str:
    return f'EP["{ep["id"]}"]'


def _find(ep: dict, names: set[str]) -> str | None:
    return next((p["name"] for p in params_in(ep, "query") if _snake(p["name"]) in names), None)


def generate(doc: dict, mod: Module) -> None:
    eps = [e for e in doc["endpoints"] if supported_body(e)]
    for ep in eps:
        ref, eid = _ref(ep), ep["id"]
        if ep["method"] == "GET":
            mod.add(f"def {mod.unique('test_' + eid + '_is_repeatable')}(api):\n    ep = {ref}\n{admin_line(ep)}"
                    "    first, second = api.send(ep), api.send(ep)\n"
                    "    rt.expect_same_shape(first, second)\n")
            mod.add(f"def {mod.unique('test_' + eid + '_responds_within_budget')}(api):\n{admin_line(ep)}    resp = api.send({ref})\n"
                    "    rt.expect_fast(resp)\n")
        if ep["method"] == "PUT" and has_json_body(ep) and isinstance(sample_body(ep), dict):
            mod.add(f"def {mod.unique('test_' + eid + '_put_is_idempotent')}(api):\n    ep = {ref}\n"
                    "    first, second = api.send(ep), api.send(ep)\n    rt.expect_ok(first, ep)\n"
                    "    rt.expect_status(second, (first.status_code,), \"repeating an identical PUT changed the outcome\")\n"
                    "    rt.expect_same_shape(first, second)\n")
        if ep["method"] == "DELETE":
            mod.add(f"def {mod.unique('test_' + eid + '_repeated_delete_is_safe')}(api):\n    ep = {ref}\n"
                    "    rt.require_destructive()\n    api.send(ep)\n    again = api.send(ep)\n"
                    '    rt.expect_no_crash(again, "second DELETE of the same id")\n')
        idem = next((p["name"] for p in params_in(ep, "header") if p["name"].lower() == "idempotency-key"), None)
        if ep["method"] == "POST" and idem and has_json_body(ep):
            mod.add(f"def {mod.unique('test_' + eid + '_idempotency_key_dedupes')}(api):\n    ep = {ref}\n"
                    f"    h = {{{idem!r}: \"apitest-idem-key-1\"}}\n"
                    "    first, second = api.send(ep, headers=h), api.send(ep, headers=h)\n    rt.expect_ok(first, ep)\n"
                    "    rt.expect_status(second, (first.status_code,), \"same Idempotency-Key must repeat the first result\")\n"
                    "    assert rt.extract_id(first) == rt.extract_id(second), \"same Idempotency-Key created a second resource\"\n")
        if is_list_endpoint(ep):
            _pagination(ep, mod)
    probe = rate_probe(doc)
    if probe:
        mod.add("@pytest.mark.intrusive\n"
                f"def {mod.unique('test_rate_limit_behaviour')}(api):\n    ep = {_ref(probe)}\n"
                '    n = int(os.environ.get("APITEST_RATE_BURST", "30"))\n'
                "    rt.expect_rate_limit_behaviour(api, ep, n)\n")


def _pagination(ep: dict, mod: Module) -> None:
    ref, eid = _ref(ep), ep["id"]
    limit, offset, page = _find(ep, LIMIT_NAMES), _find(ep, OFFSET_NAMES), _find(ep, PAGE_NAMES)
    if limit:
        mod.add(f"def {mod.unique('test_' + eid + '_limit_one_returns_at_most_one')}(api):\n    ep = {ref}\n"
                f"    resp = api.send(ep, query={{**ep.query, {limit!r}: 1}})\n    rt.expect_ok(resp, ep)\n"
                "    rt.expect_page_size(resp, 1)\n")
        mod.add(f"@pytest.mark.parametrize(\"bad\", [0, -1])\n"
                f"def {mod.unique('test_' + eid + '_invalid_limit_is_handled')}(api, bad):\n    ep = {ref}\n"
                f"    resp = api.send(ep, query={{**ep.query, {limit!r}: bad}})\n"
                '    rt.expect_no_crash(resp, "invalid page size")\n')
    nxt = offset or page
    if nxt:
        beyond = 10_000_000 if offset else 1_000_000
        extra = f"{limit!r}: 1, " if limit else ""
        mod.add(f"def {mod.unique('test_' + eid + '_past_the_end_is_empty')}(api):\n    ep = {ref}\n"
                f"    resp = api.send(ep, query={{**ep.query, {extra}{nxt!r}: {beyond}}})\n"
                '    rt.expect_no_crash(resp, "page far past the end")\n'
                "    if resp.status_code < 300:\n        rt.expect_page_size(resp, 0)\n")
        if limit:
            second = 1 if offset else 2
            first = 0 if offset else 1
            mod.add(f"def {mod.unique('test_' + eid + '_pages_do_not_overlap')}(api):\n    ep = {ref}\n"
                    f"    one = api.send(ep, query={{**ep.query, {limit!r}: 1, {nxt!r}: {first}}})\n"
                    f"    two = api.send(ep, query={{**ep.query, {limit!r}: 1, {nxt!r}: {second}}})\n"
                    "    rt.expect_ok(one, ep)\n    rt.expect_ok(two, ep)\n    rt.expect_pages_differ(one, two)\n")
