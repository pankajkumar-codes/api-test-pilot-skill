"""GraphQL fuzzing: opt-in, bounded, seeded, and it finds the seeded server flaws."""
from __future__ import annotations

import importlib
import sys

import pytest

import gen_pytest
import parse_graphql
from mock_graphql import running as gql_server
from suite_runner import GQL_ENV, failed, run_suite

from conftest import FIXTURES

FUZZ_ENV = {**GQL_ENV, "APITEST_FUZZ_RPS": "20", "APITEST_FUZZ_CASES": "40", "APITEST_TIMEOUT": "2", "APITEST_FUZZ_SLOW_MS": "1500"}


@pytest.fixture(scope="module")
def doc():
    return parse_graphql.build(str(FIXTURES / "shop.graphql"))


@pytest.fixture(scope="module")
def suite(tmp_path_factory, doc):
    out = tmp_path_factory.mktemp("gqlf") / "suite"
    gen_pytest.generate(doc, out, ["fuzz"])
    return out


def test_fuzz_is_opt_in_and_ships_its_runtime(doc, suite, tmp_path):
    res = gen_pytest.generate(doc, tmp_path / "all", ["all"])
    assert "fuzz" not in res["counts"] and not (tmp_path / "all" / "gql_fuzz.py").exists()
    both = gen_pytest.generate(doc, tmp_path / "both", ["all", "fuzz"])   # what run_all.py --types all,fuzz asks for
    assert "fuzz" in both["counts"] and set(res["counts"]) < set(both["counts"])
    for name in ("fuzz_runtime.py", "gql_fuzz.py", "test_graphql_fuzz.py"):
        assert (suite / name).exists()
    text = (suite / "test_graphql_fuzz.py").read_text()
    assert "@pytest.mark.intrusive" in text and "FUZZ_OPS" in text and "Rerun" in text
    assert "'mutation.deletePet': {'optional': [], 'destructive': True, 'total': " in text


def test_clean_server_passes(suite):
    with gql_server() as url:
        results, proc = run_suite(suite, url, {**FUZZ_ENV, "APITEST_ALLOW_DESTRUCTIVE": "yes"}, ["-m", "intrusive"])
    assert failed(results) == [], proc.stdout[-3000:]
    assert any(v[0] == "passed" for v in results.values())


@pytest.mark.parametrize("bug,needle", [("crash", "500"), ("verbose", "leak")])
def test_seeded_flaws_are_found(suite, bug, needle):
    with gql_server({bug}) as url:
        results, proc = run_suite(suite, url, FUZZ_ENV)
    assert failed(results), proc.stdout[-3000:]
    assert any(needle in v[1] for v in results.values() if v[0] == "failed"), proc.stdout[-3000:]


def test_destructive_mutations_need_their_own_switch(suite):
    with gql_server() as url:
        results, _ = run_suite(suite, url, FUZZ_ENV)
    skipped = [k for k, v in results.items() if v[0] == "skipped" and "delete" in k.lower()]
    assert skipped


def test_cases_are_deterministic_and_bounded(suite, monkeypatch):
    monkeypatch.syspath_prepend(str(suite))
    for mod in ("apitest_runtime", "fuzz_runtime", "gql_runtime", "gql_fuzz", "operations"):
        sys.modules.pop(mod, None)
    monkeypatch.setenv("APITEST_FUZZ_CASES", "40")
    try:
        gf = importlib.import_module("gql_fuzz")
        fz = importlib.import_module("fuzz_runtime")
        ops = importlib.import_module("operations")
        op = ops.OPS["query.pets"]
        base = dict(op.variables)
        a = gf.build_cases(op, base, {"optional": ["first", "after", "search"]}, fz.config())
        b = gf.build_cases(op, base, {"optional": ["first", "after", "search"]}, fz.config())
        assert [c["label"] for c in a] == [c["label"] for c in b] and 0 < len(a) <= 40
        assert len({c["label"] for c in a}) > 10
        monkeypatch.setenv("APITEST_FUZZ_SEED", "99")
        c = gf.build_cases(op, base, {"optional": ["first", "after", "search"]}, fz.config())
        assert [x["label"] for x in c] != [x["label"] for x in a]
    finally:
        for mod in ("apitest_runtime", "fuzz_runtime", "gql_runtime", "gql_fuzz", "operations"):
            sys.modules.pop(mod, None)


def test_production_targets_are_refused(suite):
    with gql_server() as url:
        results, _ = run_suite(suite, url, {**FUZZ_ENV, "APITEST_TARGET_ENV": "production"})
    assert results and {v[0] for v in results.values()} == {"skipped"}


def test_an_unconfirmed_remote_target_is_refused_before_any_request(suite):
    results, _ = run_suite(suite, "http://203.0.113.7:9", FUZZ_ENV)
    assert results and {v[0] for v in results.values()} == {"skipped"}
    assert all("non-production" in v[1] or "safety gate" in v[1] for v in results.values())


def test_endpoint_list_and_budget_summary_work_for_graphql(suite):
    env = {**FUZZ_ENV, "APITEST_FUZZ_ENDPOINTS": "pet, nope"}
    with gql_server() as url:
        results, proc = run_suite(suite, url, env)
    ran = sorted(k for k, (s, _) in results.items() if s == "passed")
    assert ran == ["test_graphql_fuzz::test_fuzz_operation[query.pet]"], ran
    out = proc.stdout
    assert "Fuzz budget" in out and "of 1 fuzzed endpoint" in out and "nope" in out
    # the budget is split between the one chosen operation only, so it gets the full case count
    assert "min 40, median 40, max 40" in out


def test_side_effect_queries_are_gated_and_kept_out_of_load(tmp_path):
    # DVGA puts deleteAllPastes and systemUpdate in Query: neither may be repeated by k6, and the delete needs the destructive switch
    import chaos_run
    import gen_k6
    sdl = tmp_path / "side.graphql"
    sdl.write_text("type Paste { id: ID! title: String }\n"
                   "type Query { pastes: [Paste] deleteAllPastes: Boolean systemUpdate: String getRunStatus: String }\n")
    d = parse_graphql.build(str(sdl))
    out = tmp_path / "suite"
    gen_pytest.generate(d, out, ["all", "fuzz"])
    positive = (out / "test_graphql_positive.py").read_text()
    head, _, rest = positive.partition("def test_query_deleteAllPastes")
    assert rest and "gr.require_destructive()" in rest.split("\ndef ")[0]
    assert "'query.deleteAllPastes': {'optional': [], 'destructive': True" in (out / "test_graphql_fuzz.py").read_text()
    notes: list[str] = []
    tags = [e["tag"] for e in gen_k6.graphql_endpoints(d, None, notes)]
    assert "gql_pastes" in tags and "gql_getRunStatus" in tags and "gql_deleteAllPastes" not in tags and "gql_systemUpdate" not in tags
    assert chaos_run.pick_probes(d, []) == [d["endpoints"][0]["path"] + "?query=%7B__typename%7D"]
