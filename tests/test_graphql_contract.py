"""GraphQL contract tests: the answer has the shape the schema promises."""
from __future__ import annotations

import importlib.util
import sys

import pytest

import gen_pytest
import parse_graphql
from mock_graphql import running as gql_server
from suite_runner import GQL_ENV, failed, passed, run_suite

from conftest import FIXTURES


@pytest.fixture(scope="module")
def suite(tmp_path_factory):
    out = tmp_path_factory.mktemp("gqlc") / "suite"
    gen_pytest.generate(parse_graphql.build(str(FIXTURES / "shop.graphql")), out, ["contract"])
    return out


@pytest.fixture(scope="module")
def gr(suite):
    sys.path.insert(0, str(suite))
    spec = importlib.util.spec_from_file_location("gqlc_runtime", suite / "gql_runtime.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_a_contract_test_is_generated_for_each_object_query(suite):
    text = (suite / "test_graphql_contract.py").read_text()
    assert "def test_contract_pet_matches_schema" in text and "def test_contract_pets_matches_schema" in text and "def test_contract_me_matches_schema" in text
    assert "health" not in text           # a scalar-returning query has no object shape to check
    ops = (suite / "operations.py").read_text()
    assert "TYPES = " in ops and "ENUMS = " in ops and "'DOG'" in ops


def test_the_clean_server_passes_and_a_wrongly_shaped_one_fails(suite):
    with gql_server() as url:
        results, proc = run_suite(suite, url, GQL_ENV)
    assert failed(results) == [] and len(passed(results)) == 3, proc.stdout[-2000:]
    with gql_server({"badshape"}) as url:
        results, _ = run_suite(suite, url, GQL_ENV)
    assert "test_graphql_contract::test_contract_pet_matches_schema" in failed(results)
    assert "test_graphql_contract::test_contract_pets_matches_schema" in failed(results)
    msg = results["test_graphql_contract::test_contract_pets_matches_schema"][1]
    assert "Pet[0].name should be a string" in msg


def _R():
    import datetime

    import requests
    r = requests.Response()
    r.status_code, r._content, r.elapsed = 200, b"{}", datetime.timedelta(0)
    r.request = requests.Request("POST", "http://127.0.0.1/graphql", json={"query": "{ x }"}).prepare()
    return r


TYPES = {"Pet": {"id": "ID!", "name": "String!", "tag": "String", "species": "Species", "scores": "[Int!]!"}}
ENUMS = {"Species": ["DOG", "CAT"]}
KINDS = {"Pet": "OBJECT", "Species": "ENUM"}


@pytest.mark.parametrize("value,problem", [
    ({"id": "1", "name": 5, "tag": None, "species": "DOG", "scores": []}, "Pet.name should be a string"),
    ({"id": "1", "name": None, "tag": None, "species": "DOG", "scores": []}, "Pet.name is null"),
    ({"id": "1", "name": "x", "tag": None, "species": "FISH", "scores": []}, "Pet.species should be one of"),
    ({"id": True, "name": "x", "tag": None, "species": "DOG", "scores": []}, "Pet.id should be a string or integer"),
    ({"id": "1", "name": "x", "tag": 3, "species": "DOG", "scores": []}, "Pet.tag should be a string"),
    ({"id": "1", "name": "x", "tag": None, "species": "DOG", "scores": "none"}, "Pet.scores should be a list"),
    ({"id": "1", "name": "x", "tag": None, "species": "DOG", "scores": [1, None]}, "has a null item"),
    ({"id": "1", "name": "x", "tag": None, "species": "DOG", "scores": [1.5]}, "Pet.scores should be an integer"),
    ({"id": "1", "tag": None, "species": "DOG", "scores": []}, "Pet.name was selected but is missing"),
])
def test_the_shape_check_names_the_problem(gr, value, problem):
    sel = ["id", "name", "tag", "species", "scores"]
    with pytest.raises(BaseException) as err:
        gr.expect_shape(_R(), value, "Pet", sel, TYPES, ENUMS, KINDS)
    assert problem in str(err.value)


def test_a_good_answer_and_nulls_where_allowed_pass(gr):
    sel = ["id", "name", "tag", "species", "scores"]
    gr.expect_shape(_R(), {"id": 7, "name": "x", "tag": None, "species": None, "scores": [1, 2]}, "Pet", sel, TYPES, ENUMS, KINDS)
    gr.expect_shape(_R(), [{"id": "1", "name": "x", "tag": "t", "species": "CAT", "scores": []}, None], "Pet", sel, TYPES, ENUMS, KINDS, True)
    gr.expect_shape(_R(), None, "Pet", sel, TYPES, ENUMS, KINDS)


def test_a_list_type_answered_with_an_object_fails(gr):
    with pytest.raises(BaseException) as err:
        gr.expect_shape(_R(), {"id": "1"}, "Pet", ["id"], TYPES, ENUMS, KINDS, True)
    assert "expected a JSON array" in str(err.value)
