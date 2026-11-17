"""GraphQL integration flows: create, read, list, update, delete on the object the flow made itself."""
from __future__ import annotations

import pytest

import gen_pytest
import parse_graphql
from mock_graphql import running as gql_server
from suite_runner import GQL_ENV, failed, passed, run_suite

from conftest import FIXTURES

FLOW = "test_graphql_integration::test_flow_Pet_lifecycle"


@pytest.fixture(scope="module")
def doc():
    return parse_graphql.build(str(FIXTURES / "shop.graphql"))


@pytest.fixture(scope="module")
def suite(tmp_path_factory, doc):
    out = tmp_path_factory.mktemp("gqli") / "suite"
    gen_pytest.generate(doc, out, ["integration"])
    return out


def test_a_flow_is_generated_for_the_creatable_type(suite):
    text = (suite / "test_graphql_integration.py").read_text()
    assert "def test_flow_Pet_lifecycle" in text and "FLOWS" in text
    assert "'update': 'mutation.updatePet'" in text and "'list': 'query.pets'" in text
    assert (suite / "gql_flow.py").exists()


def test_integration_is_part_of_all_for_graphql(doc, tmp_path):
    res = gen_pytest.generate(doc, tmp_path / "all", ["all"])
    assert "integration" in res["counts"] and "fuzz" not in res["counts"]


def test_a_schema_without_a_delete_has_no_flow_and_says_so(doc, tmp_path):
    import copy
    d = copy.deepcopy(doc)
    d["graphql"]["operations"] = [o for o in d["graphql"]["operations"] if o["name"] != "deletePet"]
    res = gen_pytest.generate(d, tmp_path / "nodel", ["integration"])
    assert "integration" not in res["counts"] and any("no flow was generated" in n for n in res["notes"])


def test_clean_server_passes_and_cleans_up(suite):
    with gql_server() as url:
        results, proc = run_suite(suite, url, GQL_ENV)
        assert failed(results) == [] and FLOW in passed(results), proc.stdout[-2000:]
        again, _ = run_suite(suite, url, GQL_ENV)     # a second run works: nothing was left behind to collide with
    assert FLOW in passed(again)


@pytest.mark.parametrize("bug,needle", [("stale", "old value"), ("undeleted", "still readable")])
def test_seeded_flaws_are_found(suite, bug, needle):
    with gql_server({bug}) as url:
        results, proc = run_suite(suite, url, GQL_ENV)
    assert failed(results) == [FLOW], proc.stdout[-2000:]
    assert needle in results[FLOW][1]


def test_writes_are_skipped_on_production(suite):
    with gql_server() as url:
        results, _ = run_suite(suite, url, {**GQL_ENV, "APITEST_TARGET_ENV": "production"})
    assert results[FLOW][0] == "skipped"
