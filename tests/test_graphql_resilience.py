"""GraphQL resilience: repeatable reads, response budget, paging, rate-limit behaviour and a repeated delete."""
from __future__ import annotations

import pytest

import gen_pytest
import parse_graphql
from mock_graphql import running as gql_server
from suite_runner import GQL_ENV, failed, passed, run_suite

from conftest import FIXTURES

ENV = {**GQL_ENV, "APITEST_RATE_BURST": "20", "APITEST_SLOW_MS": "150"}
M = "test_graphql_resilience::"


@pytest.fixture(scope="module")
def doc():
    return parse_graphql.build(str(FIXTURES / "shop.graphql"))


@pytest.fixture(scope="module")
def suite(tmp_path_factory, doc):
    out = tmp_path_factory.mktemp("gqlr") / "suite"
    gen_pytest.generate(doc, out, ["resilience"])
    return out


def test_generated_checks(suite, doc, tmp_path):
    text = (suite / "test_graphql_resilience.py").read_text()
    for name in ("test_pets_is_repeatable", "test_pets_responds_within_budget", "test_pets_pagination", "test_rate_limit_behaviour",
                 "test_Pet_repeated_delete_is_safe"):
        assert f"def {name}" in text
    assert "@pytest.mark.intrusive" in text and "('first=-1'" in text and "'after=empty'" in text
    assert "test_pet_pagination" not in text          # only list queries with a size or cursor argument are paged
    assert (suite / "gql_flow.py").exists()
    res = gen_pytest.generate(doc, tmp_path / "all", ["all"])
    assert "resilience" in res["counts"]


def test_a_schema_without_a_delete_says_so(doc, tmp_path):
    import copy
    d = copy.deepcopy(doc)
    d["graphql"]["operations"] = [o for o in d["graphql"]["operations"] if o["name"] != "deletePet"]
    res = gen_pytest.generate(d, tmp_path / "nodel", ["resilience"])
    assert any("repeated-delete check was not generated" in n for n in res["notes"])


def test_clean_server_passes(suite):
    with gql_server() as url:
        results, proc = run_suite(suite, url, ENV)
    assert failed(results) == [] and len(passed(results)) >= 10, proc.stdout[-2000:]


@pytest.mark.parametrize("bug,test,needle", [
    ("ignorefirst", "test_pets_pagination[first=1]", "page size argument ignored"),
    ("ignorefirst", "test_pets_pagination[first=0]", "page size argument ignored"),
    ("slowpets", "test_pets_responds_within_budget", "budget"),
    ("throttle", "test_rate_limit_behaviour", "Retry-After"),
])
def test_seeded_flaws_are_found(suite, bug, test, needle):
    with gql_server({bug}) as url:
        results, proc = run_suite(suite, url, ENV)
    assert M + test in failed(results), proc.stdout[-2000:]
    assert needle in results[M + test][1]
    assert set(failed(results)) <= {M + "test_pets_pagination[first=1]", M + "test_pets_pagination[first=0]", M + test}


def test_gate_and_production_skips(suite):
    with gql_server() as url:
        results, _ = run_suite(suite, url, {**ENV, "APITEST_TARGET_ENV": "production"})
    assert results[M + "test_Pet_repeated_delete_is_safe"][0] == "skipped" and results[M + "test_rate_limit_behaviour"][0] == "skipped"
    remote, _ = run_suite(suite, "http://203.0.113.7:9", ENV)
    assert remote[M + "test_rate_limit_behaviour"][0] == "skipped"      # an unconfirmed remote target gets no burst
