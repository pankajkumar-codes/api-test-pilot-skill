"""Generator for the fuzz test module (`--types fuzz` in gen_pytest.py). Stdlib only.

Emits test_fuzz.py: one `intrusive` test per eligible REST endpoint that calls fuzz_runtime.fuzz_endpoint().
All logic lives in templates/pytest/fuzz_runtime.py; this file only places endpoint ids and query-parameter
names (from the spec, so untrusted) into the module as Python literals via repr.
Fuzzing is opt-in (not part of `--types all`) and gated like every other intrusive test.
"""
from __future__ import annotations

import re

from gen_common import Module, params_in, py, supported_body

NAME_RE = re.compile(r"^[A-Za-z0-9_.\-\[\]]{1,64}$")


def generate(doc: dict, mod: Module) -> list[str]:
    notes: list[str] = []
    rows = []
    for ep in doc["endpoints"]:
        if not supported_body(ep):
            continue
        if ep["method"] in ("HEAD", "OPTIONS", "TRACE", "CONNECT"):
            continue
        query = [p["name"] for p in params_in(ep, "query") if isinstance(p.get("name"), str) and NAME_RE.match(p["name"])]
        rows.append((ep["id"], {"query": query[:20], "method": ep["method"]}))
    if not rows:
        notes.append("fuzz: no endpoint to fuzz")
        return notes
    for _, spec in rows:
        spec["total"] = len(rows)   # lets the runtime split the run budget fairly between endpoints
    table = "FUZZ_SPECS = {\n" + ",\n".join(f"    {py(i)}: {py(spec)}" for i, spec in rows) + ",\n}\n"
    mod.add(table, is_test=False)
    mod.add('''@pytest.mark.intrusive
@pytest.mark.parametrize("ep_id", [
    # A DELETE is fuzzed with the id the other tests read, so it is marked destructive and conftest runs it after them.
    pytest.param(i, marks=pytest.mark.destructive) if FUZZ_SPECS[i]["method"] == "DELETE" else i
    for i in sorted(FUZZ_SPECS, key=lambda i: (FUZZ_SPECS[i]["method"] == "GET", i))])   # writes first
def test_fuzz(api, ep_id):
    """Seeded, bounded fuzzing of one endpoint: no 5xx, hangs, crashes, leaks or header injection."""
    fz.fuzz_endpoint(api, EP[ep_id], FUZZ_SPECS[ep_id])
''')
    notes.append("fuzz runs are bounded by "
                 "APITEST_FUZZ_CASES / APITEST_FUZZ_RPS / APITEST_FUZZ_MAX_REQUESTS and need the intrusive safety gate")
    return notes
