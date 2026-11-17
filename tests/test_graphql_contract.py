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
    ({"id": "1", "name": "x", "tag": None, "species": "DOG", "scores": [1, None]}, "Pet.scores[1] is null although the schema declares [Int!]!"),
    ({"id": "1", "name": "x", "tag": None, "species": "DOG", "scores": [1.5]}, "Pet.scores[0] should be an integer"),
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
    gr.expect_shape(_R(), [{"id": "1", "name": "x", "tag": "t", "species": "CAT", "scores": []}, None], "[Pet]", sel, TYPES, ENUMS, KINDS)
    gr.expect_shape(_R(), None, "Pet", sel, TYPES, ENUMS, KINDS)


def test_a_list_type_answered_with_an_object_fails(gr):
    with pytest.raises(BaseException) as err:
        gr.expect_shape(_R(), {"id": "1"}, "[Pet!]!", ["id"], TYPES, ENUMS, KINDS)
    assert "should be a list" in str(err.value)


def test_nested_lists_are_walked_level_by_level(gr):
    sel = ["id", "name"]
    good = [[{"id": "1", "name": "a"}], [], [{"id": "2", "name": "b"}, None]]
    gr.expect_shape(_R(), good, "[[Pet]!]!", sel, TYPES, ENUMS, KINDS)
    for bad, problem in (([{"id": "1", "name": "a"}], "should be a list"),               # one level short
                         ([[{"id": "1", "name": 5}]], "Pet[0][0].name should be a string"),
                         ([None], "is null although the schema declares [[Pet]!]!"),
                         ([[[]]], "should be an object")):
        with pytest.raises(BaseException) as err:
            gr.expect_shape(_R(), bad, "[[Pet]!]!", sel, TYPES, ENUMS, KINDS)
        assert problem in str(err.value), (bad, str(err.value))


def test_a_query_returning_nested_lists_gets_a_contract_test_and_passes_on_a_correct_server(tmp_path):
    import http.server
    import json
    import threading

    sdl = "type Query { grid: [[Cell!]!]! }\ntype Cell { id: ID! label: String }\n"
    (tmp_path / "grid.graphql").write_text(sdl)
    out = tmp_path / "suite"
    gen_pytest.generate(parse_graphql.build(str(tmp_path / "grid.graphql")), out, ["contract"])

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            raw = json.dumps({"data": {"grid": [[{"id": "1", "label": "a"}, {"id": "2", "label": None}], []]}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        results, proc = run_suite(out, f"http://127.0.0.1:{srv.server_address[1]}", {"APITEST_TARGET_ENV": "local"})
    finally:
        srv.shutdown()
        srv.server_close()
    assert passed(results) == ["test_graphql_contract::test_contract_grid_matches_schema"], proc.stdout[-1500:]


# ---------------------------------------------------------------- non-null arguments with defaults, API key header
def test_a_non_null_argument_with_a_default_is_optional_and_its_variable_nullable(tmp_path):
    import gen_pytest
    import parse_graphql
    sdl = tmp_path / "s.graphql"
    sdl.write_text("type Query { pets(first: Int! = 20, tag: String! = \"x\"): [String!]! }")
    doc = parse_graphql.build(str(sdl))
    out = tmp_path / "suite"
    gen_pytest.generate(doc, out, ["smoke", "positive"])
    text = (out / "operations.py").read_text()
    assert "query ApitestPets($first: Int, $tag: String)" in text      # nullable variables: the request may leave them out


def test_api_key_header_changes_the_scheme_and_the_runtime_sends_it(tmp_path, monkeypatch):
    import gen_pytest
    import parse_graphql
    from _common import IntakeError
    sdl = tmp_path / "s.graphql"
    sdl.write_text("type Query { health: String }")
    doc = parse_graphql.build(str(sdl), None, "x-api-key")
    assert doc["security_schemes"] == {"apiKey": {"type": "apiKey", "in": "header", "name": "x-api-key", "env": ["APITEST_APIKEY_KEY"]}}
    assert any("x-api-key" in w for w in doc["warnings"])
    out = tmp_path / "suite"
    gen_pytest.generate(doc, out, ["smoke"])
    assert "apiKey" in (out / "conftest.py").read_text()
    with pytest.raises(IntakeError):
        parse_graphql.build(str(sdl), None, "bad header\r\nx: y")
