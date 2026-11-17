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
    for name in ("fuzz_runtime.py", "gql_fuzz.py", "test_graphql_fuzz.py"):
        assert (suite / name).exists()
    text = (suite / "test_graphql_fuzz.py").read_text()
    assert "@pytest.mark.intrusive" in text and "FUZZ_OPS" in text and "Rerun" in text
    assert "'mutation.deletePet': {'optional': [], 'destructive': True}" in text


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
