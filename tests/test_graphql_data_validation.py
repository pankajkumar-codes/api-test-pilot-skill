"""GraphQL data-validation: one violation per case, and the server must reject it."""
from __future__ import annotations

import pytest

import gen_pytest
import parse_graphql
from mock_graphql import running as gql_server
from suite_runner import GQL_ENV, failed, passed, run_suite

from conftest import FIXTURES

CREATE = "test_graphql_data_validation::test_mutation_createPet_validates_input"


@pytest.fixture(scope="module")
def doc():
    return parse_graphql.build(str(FIXTURES / "shop.graphql"))


@pytest.fixture(scope="module")
def suite(tmp_path_factory, doc):
    out = tmp_path_factory.mktemp("gqlv") / "suite"
    gen_pytest.generate(doc, out, ["data-validation"])
    return out


def test_constraint_limits_reach_the_intake(doc):
    fields = {f["name"]: f for f in doc["graphql"]["types"]["NewPetInput"]["fields"]}
    assert fields["name"]["constraints"] == {"minLength": 1, "maxLength": 50} and fields["tag"]["constraints"] == {"maxLength": 20}
    first = next(a for o in doc["graphql"]["operations"] if o["name"] == "pets" for a in o["args"] if a["name"] == "first")
    assert first["constraints"] == {"min": 0}


def test_odd_constraint_arguments_are_ignored(tmp_path):
    sdl = tmp_path / "s.graphql"
    sdl.write_text('type Query { a(x: String @constraint(minLength: "no", maxLength: true, other: 5, max: 3)): String }\n')
    arg = parse_graphql.build(str(sdl))["graphql"]["operations"][0]["args"][0]
    assert arg["constraints"] == {"max": 3}


def test_cases_are_generated_per_violation(suite):
    text = (suite / "test_graphql_data_validation.py").read_text()
    for label in ("input.name_missing", "input.name_too_short", "input.name_too_long", "input.tag_too_long", "input.species_wrong_type",
                  "input_unknown_field", "first_below_min"):
        assert f"'{label}'" in text
    assert "deletePet" not in text            # nothing to violate: its only argument is an id


def test_a_schema_without_constraints_says_so(tmp_path):
    sdl = tmp_path / "s.graphql"
    sdl.write_text("type Query { a: String }\ntype Mutation { make(input: In!): String }\ninput In { n: String! }\n")
    res = gen_pytest.generate(parse_graphql.build(str(sdl)), tmp_path / "o", ["data-validation"])
    assert res["counts"] == {"data-validation": 1} and any("no @constraint limits" in n for n in res["notes"])


def test_clean_server_rejects_every_violation(suite):
    with gql_server() as url:
        results, proc = run_suite(suite, url, GQL_ENV)
    assert failed(results) == [] and len(passed(results)) >= 13, proc.stdout[-2000:]


def test_a_server_that_skips_validation_is_caught(suite):
    with gql_server({"novalidate"}) as url:
        results, proc = run_suite(suite, url, GQL_ENV)
    bad = [k for k in failed(results)]
    assert any("input.name_too_short" in k for k in bad) and any("input.name_too_long" in k for k in bad) and any("unknown_field" in k for k in bad), proc.stdout[-2000:]


def test_writes_are_skipped_on_production(suite):
    with gql_server() as url:
        results, _ = run_suite(suite, url, {**GQL_ENV, "APITEST_TARGET_ENV": "production"})
    assert results[CREATE + "[input.name_too_short]"][0] == "skipped"
