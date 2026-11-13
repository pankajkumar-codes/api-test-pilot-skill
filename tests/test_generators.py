"""Generated suites are validated by running them against local mock servers (tests/mock_rest.py, mock_graphql.py).

Every seeded bug must make at least one specific generated test fail; the clean server must pass everything
that is not skipped by a documented gate. Nothing here touches the network beyond 127.0.0.1.
"""
import ast
import copy
import importlib.util
import json
import subprocess
import sys

import pytest

from conftest import FIXTURES, ROOT
import gen_common
import gen_pytest
import parse_graphql
import parse_openapi
import spec_diff
from _common import IntakeError
from mock_graphql import running as gql_server
from mock_rest import running as rest_server
from suite_runner import GQL_ENV, REST_ENV, failed, passed, run_suite

SCRIPTS = ROOT / "skills" / "api-testing" / "scripts"


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def rest_doc():
    return parse_openapi.build(str(FIXTURES / "mockapi.yaml"))


@pytest.fixture(scope="module")
def rest_suite(tmp_path_factory, rest_doc):
    out = tmp_path_factory.mktemp("rest") / "suite"
    gen_pytest.generate(rest_doc, out, ["all"])
    return out


@pytest.fixture(scope="module")
def gql_doc():
    return parse_graphql.build(str(FIXTURES / "shop.graphql"))


@pytest.fixture(scope="module")
def gql_suite(tmp_path_factory, gql_doc):
    out = tmp_path_factory.mktemp("gql") / "suite"
    gen_pytest.generate(gql_doc, out, ["all"])
    return out


def load_runtime(suite):
    spec = importlib.util.spec_from_file_location("generated_runtime", suite / "apitest_runtime.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["generated_runtime"] = mod  # dataclasses look the module up here
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- generator output
def test_generates_expected_files_and_compiles_for_py311(rest_suite, gql_suite):
    names = {p.name for p in rest_suite.iterdir()}
    assert {"conftest.py", "apitest_runtime.py", "endpoints.py", "test_positive.py", "test_negative.py", "test_edge.py",
            "test_data_validation.py", "test_contract.py", "test_security.py", "test_integration.py",
            "test_resilience.py", "test_smoke.py"} <= names
    assert {"gql_runtime.py", "operations.py", "test_graphql_security.py", "test_graphql_positive.py"} <= {p.name for p in gql_suite.iterdir()}
    for suite in (rest_suite, gql_suite):
        for f in suite.glob("*.py"):
            ast.parse(f.read_text(), filename=str(f), feature_version=(3, 11))
    for f in SCRIPTS.glob("*.py"):  # the skill's own scripts must stay 3.11-compatible
        ast.parse(f.read_text(), filename=str(f), feature_version=(3, 11))


def test_every_file_states_the_rerun_command(rest_suite):
    text = (rest_suite / "test_positive.py").read_text()
    assert "Rerun: APITEST_BASE_URL=" in text and "python -m pytest" in text


def test_data_validation_cases_cover_schema_constraints(rest_suite):
    text = (rest_suite / "test_data_validation.py").read_text()
    for label in ("missing_name", "name_above_max_length", "age_below_minimum", "age_above_maximum",
                  "contact_bad_email", "name_wrong_type", "query_limit_above_maximum", "query_status_not_in_enum",
                  "path_petId_wrong_type"):
        assert f"'{label}'" in text, label


def test_edge_cases_include_boundaries_and_unicode(rest_suite):
    text = (rest_suite / "test_edge.py").read_text()
    for label in ("name_unicode", "name_max_length", "age_min_boundary", "age_max_boundary", "name_special_chars"):
        assert f"'{label}'" in text, label


def test_mass_assignment_only_probes_undeclared_fields(rest_suite):
    text = (rest_suite / "test_security.py").read_text()
    assert "is_admin" in text and "'name'" not in text.split("injected =")[1].split("\n")[0]


def test_integration_flows_follow_resource_structure(rest_doc):
    res = gen_pytest.gen_integration.build_resources(rest_doc)
    toys = res["/pets/{petId}/toys"]
    assert res["/pets"]["create"]["id"] == "createPet" and toys["parent"] is res["/pets"]
    text = ""
    # nested flow creates the parent first and cleans up child before parent
    from gen_common import Module
    mod = Module("t", "integration", "rerun")
    gen_pytest.gen_integration.generate(rest_doc, mod)
    text = "\n".join(mod.blocks)
    assert text.index("EP['createPet']") < text.index("EP['createToy']")
    assert "cleanup.add" in text and "deleted resource is still readable" in text


def test_unsupported_body_types_are_reported_not_guessed(tmp_path):
    doc = {"source": {"type": "openapi"}, "servers": [], "security_schemes": {}, "warnings": [], "endpoints": [{
        "id": "upload", "method": "POST", "path": "/upload", "parameters": [], "auth_required": False, "security": [],
        "request_body": {"content_type": "multipart/form-data", "required": True, "schema": {"type": "object"}},
        "responses": {"200": {"description": "ok"}}}]}
    res = gen_pytest.generate(doc, tmp_path / "s", ["all"])
    assert any("multipart/form-data" in n for n in res["notes"])


def test_unknown_type_is_a_clean_error(rest_doc, tmp_path):
    with pytest.raises(IntakeError, match="Unknown test type"):
        gen_pytest.generate(rest_doc, tmp_path / "x", ["bogus"])


def test_cli_prints_rerun_command(tmp_path):
    intake = tmp_path / "i.json"
    subprocess.run([sys.executable, str(SCRIPTS / "intake.py"), str(FIXTURES / "mockapi.yaml"), "-o", str(intake)], check=True, capture_output=True)
    p = subprocess.run([sys.executable, str(SCRIPTS / "gen_pytest.py"), str(intake), "-o", str(tmp_path / "out"), "--types", "smoke,positive"],
                       capture_output=True, text=True)
    assert p.returncode == 0 and "Rerun with:" in p.stdout and "python -m pytest" in p.stdout
    bad = subprocess.run([sys.executable, str(SCRIPTS / "gen_pytest.py"), str(intake), "-o", str(tmp_path / "o2"), "--types", "nope"],
                         capture_output=True, text=True)
    assert bad.returncode == 2 and "Unknown test type" in bad.stderr


# ---------------------------------------------------------------- secrets
def test_no_secret_values_reach_generated_files(tmp_path, rest_doc):
    doc = copy.deepcopy(rest_doc)
    create = next(e for e in doc["endpoints"] if e["id"] == "createPet")
    create["request_body"]["example"] = {"name": "x", "password": "CANARY-example-secret"}
    create["request_body"]["schema"]["properties"]["password"] = {
        "type": "string", "example": "CANARY-schema-secret", "default": "CANARY-default-secret", "enum": ["CANARY-enum-secret"]}
    create["request_body"]["schema"]["properties"]["api_key"] = {"type": "string", "example": "CANARY-key"}
    create["request_body"]["schema"]["required"] = ["name", "password", "api_key"]
    create["parameters"].append({"name": "session_token", "in": "query", "required": True, "schema": {"type": "string"},
                                 "example": "CANARY-param-secret"})
    out = tmp_path / "s"
    gen_pytest.generate(doc, out, ["all"])
    for f in out.glob("*.py"):
        assert "CANARY" not in f.read_text(), f.name
    assert "REPLACE_ME" in (out / "endpoints.py").read_text()  # placeholder instead of a value


def test_runtime_secret_regex_is_the_shared_one(rest_suite):
    import _common
    assert load_runtime(rest_suite).SECRET_KEY_RE.pattern == _common.SECRET_KEY_RE.pattern


def test_failure_output_never_contains_the_token(rest_suite):
    with rest_server({"contract"}) as url:
        results, proc = run_suite(rest_suite, url, REST_ENV, ["-k", "test_listPets_response_contract"])
    assert failed(results) == ["test_contract::test_listPets_response_contract"]
    junit_text = (rest_suite.parent / f"{rest_suite.name}-junit.xml").read_text()
    for blob in (proc.stdout, proc.stderr, junit_text):
        assert "tokA" not in blob
    assert "redacted" in junit_text and "curl -sS" in junit_text  # reproducible, but redacted


def test_runtime_redaction_and_validation_helpers(rest_suite):
    rt = load_runtime(rest_suite)
    assert rt.redact({"a": 1, "password": "x", "nested": [{"token": "y", "ok": 2}]}) == \
        {"a": 1, "password": "<redacted>", "nested": [{"token": "<redacted>", "ok": 2}]}
    assert rt.sensitive_fields({"user": {"passwordHash": "x", "author": "ok"}, "items": [{"api_key": "k"}]}) == \
        ["$.user.passwordHash", "$.items[0].api_key"]
    schema = {"type": "object", "required": ["id"], "properties": {
        "id": {"type": "integer"}, "tag": {"type": "string", "nullable": True}, "e": {"enum": ["a"]},
        "mail": {"type": "string", "format": "email"}, "n": {"type": "integer", "minimum": 1}}}
    assert rt.validate({"id": 1, "tag": None, "e": "a", "mail": "a@b.co", "n": 1}, schema) == []
    errs = rt.validate({"id": "1", "e": "z", "mail": "nope", "n": 0, "x": 1}, schema)
    assert len(errs) == 4
    assert any("missing required" in e for e in rt.validate({}, schema))
    assert rt.validate(True, {"type": "integer"}) and rt.validate([1, "a"], {"type": "array", "items": {"type": "integer"}})
    assert set(rt.jwt_variants("a.b.c")) == {"bad_signature", "alg_none", "stripped_signature"} and rt.jwt_variants("opaque") == {}
    assert rt.items_of({"data": [1, 2]}) == [1, 2] and rt.items_of({"x": 1}) is None


# ---------------------------------------------------------------- safety gate
@pytest.mark.parametrize("url,env,expect_blocked", [
    ("http://127.0.0.1:9", {}, False),
    ("http://localhost:9", {"APITEST_TARGET_ENV": "production"}, True),
    ("http://127.0.0.1:9", {"APITEST_TARGET_ENV": "prod"}, True),
    ("https://api.example.com", {}, True),
    ("https://api.example.com", {"APITEST_TARGET_ENV": "staging"}, True),
    ("https://api.example.com", {"APITEST_TARGET_ENV": "staging", "APITEST_AUTHORIZED": "no"}, True),
    ("https://api.example.com", {"APITEST_TARGET_ENV": "production", "APITEST_AUTHORIZED": "yes"}, True),
    ("https://api.example.com", {"APITEST_TARGET_ENV": "staging", "APITEST_AUTHORIZED": "yes"}, False),
])
def test_safety_gate_matrix(rest_suite, monkeypatch, url, env, expect_blocked):
    rt = load_runtime(rest_suite)
    for k in ("APITEST_TARGET_ENV", "APITEST_AUTHORIZED"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert (rt.intrusive_skip_reason(url) is not None) is expect_blocked


def test_intrusive_tests_send_nothing_to_a_production_target(rest_suite):
    with rest_server() as url:
        # the server is local, but the user declared it production: the gate must win
        results, proc = run_suite(rest_suite, url, {**REST_ENV, "APITEST_TARGET_ENV": "production"},
                                  ["-m", "intrusive"])
    assert results and all(s == "skipped" for s, _ in results.values())
    assert all("production" in m for _, m in results.values())


def test_non_local_target_requires_environment_declaration(rest_suite):
    results, proc = run_suite(rest_suite, "https://api.example.com", {}, ["-k", "smoke_health"])
    assert proc.returncode != 0 and "APITEST_TARGET_ENV" in (proc.stdout + proc.stderr)


def test_nothing_runs_intrusively_without_authorization_on_remote_hosts(rest_suite):
    results, proc = run_suite(rest_suite, "https://api.example.com",
                              {"APITEST_TARGET_ENV": "staging"}, ["-m", "intrusive"])
    assert results and all(s == "skipped" and "APITEST_AUTHORIZED" in m for s, m in results.values())


def url_log(url):  # requests the mock actually received (set by the running() context manager)
    import mock_rest
    return mock_rest.LAST_LOG


# ---------------------------------------------------------------- REST end to end
MUST_PASS = [
    "test_positive::test_createPet_valid_request", "test_positive::test_getPet_valid_request",
    "test_negative::test_getPet_unknown_id_not_found", "test_negative::test_createPet_rejects_missing_credentials",
    "test_contract::test_listPets_response_contract", "test_contract::test_getPet_error_contract_404",
    "test_integration::test_flow_pets_lifecycle", "test_integration::test_flow_pets_petId_toys_lifecycle",
    "test_security::test_api1_bola_getPet", "test_security::test_api8_security_headers",
    "test_security::test_api5_bfla_adminStats", "test_resilience::test_listPets_limit_one_returns_at_most_one",
    "test_security::test_api2_invalid_credentials_rejected_getPet", "test_security::test_api3_mass_assignment_createPet",
]


def test_clean_rest_server_passes_the_whole_suite(rest_suite):
    with rest_server() as url:
        results, proc = run_suite(rest_suite, url, REST_ENV)
    assert failed(results) == [], proc.stdout[-3000:]
    assert len(passed(results)) > 150
    for name in MUST_PASS:
        assert results[name][0] == "passed", (name, results[name])
    skipped = {m for s, m in (v for v in results.values()) if s == "skipped"}
    assert any("APITEST_ALLOW_DESTRUCTIVE" in m for m in skipped)  # gated, not silently dropped


SEEDED = {
    "bola": "test_security::test_api1_bola_getPet",
    "noauth": "test_negative::test_listPets_rejects_missing_credentials",
    "leak": "test_security::test_api3_no_sensitive_fields_listPets",
    "contract": "test_contract::test_listPets_response_contract",
    "mass": "test_security::test_api3_mass_assignment_createPet",
    "noval": "test_data_validation::test_createPet_rejects_invalid_input[name_above_max_length]",
    "crash": "test_security::test_injection_createPet[body_name[0]]",
    "stacktrace": "test_security::test_api8_errors_do_not_leak_internals",
    "admin": "test_security::test_api5_bfla_adminStats",
    "headers": "test_security::test_api8_security_headers",
    "cors": "test_security::test_api8_cors_not_permissive",
    "ssrf": "test_security::test_api7_ssrf_preview_url[http://169.254.169.254/latest/meta-data/]",
    "paging": "test_resilience::test_listPets_limit_one_returns_at_most_one",
}


@pytest.mark.parametrize("bug", sorted(SEEDED))
def test_seeded_rest_bug_is_detected(rest_suite, bug):
    with rest_server({bug}) as url:
        results, proc = run_suite(rest_suite, url, REST_ENV)
    assert SEEDED[bug] in failed(results), (bug, failed(results))


def test_findings_carry_severity_owasp_tag_and_a_repro(rest_suite):
    with rest_server({"bola"}) as url:
        results, _ = run_suite(rest_suite, url, REST_ENV, ["-k", "test_api1_bola_getPet"])
    msg = results["test_security::test_api1_bola_getPet"][1]
    assert "[HIGH][API1:2023 BOLA]" in msg and "repro: curl -sS" in msg


def test_destructive_calls_run_only_when_allowed(rest_suite):
    for pattern in ("deletePet", "deleteToy"):  # each consumes the id it is given, so one fresh server per resource
        with rest_server() as url:
            results, _ = run_suite(rest_suite, url, {**REST_ENV, "APITEST_ALLOW_DESTRUCTIVE": "yes"}, ["-k", pattern])
        assert failed(results) == [], (pattern, failed(results))
        assert results[f"test_positive::test_{pattern}_valid_request"][0] == "passed"
    with rest_server() as url:
        results, _ = run_suite(rest_suite, url, REST_ENV, ["-k", "deletePet"])
    assert not any(m == "DELETE" and p == "/pets/1" for m, p in url_log(url)), "no DELETE of existing data without the opt-in"
    assert results["test_positive::test_deletePet_valid_request"][0] == "skipped"


def test_rate_limit_behaviour(rest_suite):
    env = {**REST_ENV, "APITEST_EXPECT_RATE_LIMIT": "yes", "APITEST_RATE_BURST": "25"}
    with rest_server(rate_limit=10) as url:
        results, _ = run_suite(rest_suite, url, env, ["-k", "rate_limit"])
    assert failed(results) == [] and passed(results)
    with rest_server({"noretry"}, rate_limit=10) as url:
        results, _ = run_suite(rest_suite, url, env, ["-k", "rate_limit_behaviour"])
    assert "test_resilience::test_rate_limit_behaviour" in failed(results)
    assert "Retry-After" in results["test_resilience::test_rate_limit_behaviour"][1]
    with rest_server() as url:  # no throttling at all while the user expects some
        results, _ = run_suite(rest_suite, url, env, ["-k", "rate_limiting_present"])
    assert "test_security::test_api4_rate_limiting_present" in failed(results)


def test_missing_credentials_skip_with_the_env_var_name(rest_suite):
    env = {k: v for k, v in REST_ENV.items() if k != "APITEST_BEARERAUTH_TOKEN"}
    with rest_server() as url:
        results, _ = run_suite(rest_suite, url, env, ["-k", "test_createPet_valid_request"])
    status, msg = results["test_positive::test_createPet_valid_request"]
    assert status == "skipped" and "APITEST_BEARERAUTH_TOKEN" in msg


# ---------------------------------------------------------------- GraphQL
def test_graphql_sdl_and_introspection_normalize_identically(tmp_path):
    parsed = parse_graphql.parse_sdl((FIXTURES / "shop.graphql").read_text())

    def intro_ref(ref):
        if "list" in ref:
            inner = {"kind": "LIST", "name": None, "ofType": intro_ref(ref["list"])}
        else:
            inner = {"kind": "SCALAR" if ref["name"] in parse_graphql.SCALARS else parsed["types"][ref["name"]]["kind"],
                     "name": ref["name"], "ofType": None}
        return {"kind": "NON_NULL", "name": None, "ofType": inner} if ref.get("non_null") else inner
    types = []
    for name, t in parsed["types"].items():
        entry = {"kind": t["kind"], "name": name, "fields": None, "inputFields": None, "enumValues": None, "possibleTypes": None}
        if t["kind"] == "ENUM":
            entry["enumValues"] = [{"name": v} for v in t["values"]]
        elif t["kind"] == "INPUT_OBJECT":
            entry["inputFields"] = [{"name": f["name"], "type": intro_ref(f["ref"]),
                                     "defaultValue": json.dumps(f["default"]) if f["default"] is not None else None} for f in t["fields"]]
        else:
            entry["fields"] = [{"name": f["name"], "type": intro_ref(f["ref"]),
                                "args": [{"name": a["name"], "type": intro_ref(a["ref"]),
                                          "defaultValue": json.dumps(a["default"]) if a["default"] is not None else None} for a in f["args"]]}
                               for f in t["fields"]]
        types.append(entry)
    intro = {"data": {"__schema": {"queryType": {"name": "Query"}, "mutationType": {"name": "Mutation"},
                                   "subscriptionType": None, "types": types + [{"kind": "OBJECT", "name": "__Schema", "fields": []}]}}}
    path = tmp_path / "introspection.json"
    path.write_text(json.dumps(intro))
    a, b = parse_graphql.build(str(FIXTURES / "shop.graphql")), parse_graphql.build(str(path))
    assert a["source"]["format"] == "sdl" and b["source"]["format"] == "introspection"
    strip = lambda o: json.loads(json.dumps(o), object_hook=lambda d: {k: v for k, v in d.items() if k != "description"})  # noqa: E731
    assert strip(a["graphql"]["operations"]) == strip(b["graphql"]["operations"])  # introspection omits SDL descriptions here
    assert strip(a["graphql"]["types"]) == strip(b["graphql"]["types"])


def test_graphql_intake_shape_and_details(gql_doc):
    assert set(gql_doc) >= {"source", "info", "servers", "security_schemes", "endpoints", "warnings", "graphql"}
    ep = gql_doc["endpoints"][0]
    assert (ep["method"], ep["path"]) == ("POST", "/graphql") and ep["request_body"]["schema"]["required"] == ["query"]
    ops = {o["id"]: o for o in gql_doc["graphql"]["operations"]}
    assert ops["query.pet"]["args"][0] == {**ops["query.pet"]["args"][0], "name": "id", "type": "ID!", "required": True}
    assert ops["query.pets"]["return_list"] and ops["query.pets"]["selection"][:2] == ["id", "name"]
    assert ops["mutation.createPet"]["return_non_null"]
    types = gql_doc["graphql"]["types"]
    assert types["User"]["fields"][2] == {**types["User"]["fields"][2], "name": "email", "sensitive": True}
    assert types["Species"]["values"] == ["DOG", "CAT", "BIRD"]
    assert any("authentication" in w for w in gql_doc["warnings"]) and any("No endpoint URL" in w for w in gql_doc["warnings"])


def test_sdl_parser_handles_descriptions_extend_union_and_directives(tmp_path):
    sdl = '''
    """Doc"""
    scalar Date
    directive @cost(n: Int = 1) repeatable on FIELD_DEFINITION | OBJECT
    interface Node { id: ID! }
    type Query { node(id: ID!): Node  search(q: String = "x", tags: [String!]! = []): [Result!]! @cost(n: 5) }
    extend type Query { extra: Int }
    union Result = A | B
    type A implements Node & Other { id: ID! }
    type B { id: ID! }
    input I { a: Int = 3 }
    enum E { X Y }
    '''
    f = tmp_path / "s.graphql"
    f.write_text(sdl)
    doc = parse_graphql.build(str(f))
    names = [o["name"] for o in doc["graphql"]["operations"]]
    assert names == ["node", "search", "extra"]
    search = doc["graphql"]["operations"][1]
    assert search["args"][1]["type"] == "[String!]!" and search["args"][1]["list"] and search["return_kind"] == "UNION"
    assert doc["graphql"]["types"]["Result"]["possible"] == ["A", "B"]


def test_graphql_defaults_of_secret_arguments_are_dropped(tmp_path):
    f = tmp_path / "s.graphql"
    f.write_text('type Query { login(apiKey: String = "CANARY-gql-secret", name: String = "ok"): String }')
    text = json.dumps(parse_graphql.build(str(f)))
    assert "CANARY" not in text and '"ok"' in text


def test_graphql_parse_errors_are_clean(tmp_path):
    f = tmp_path / "bad.graphql"
    f.write_text("type Query { a: }")
    with pytest.raises(IntakeError):
        parse_graphql.build(str(f))
    g = tmp_path / "noquery.graphql"
    g.write_text("type Foo { a: Int }")
    with pytest.raises(IntakeError, match="query root"):
        parse_graphql.build(str(g))


def test_intake_dispatches_graphql(tmp_path):
    p = subprocess.run([sys.executable, str(SCRIPTS / "intake.py"), str(FIXTURES / "shop.graphql"), "--summary"],
                       capture_output=True, text=True)
    assert p.returncode == 0 and "query        pet(id: ID!) -> Pet" in p.stdout


def test_graphql_suite_reuses_shared_rest_modules(gql_suite, rest_suite):
    assert (gql_suite / "apitest_runtime.py").read_text() == (rest_suite / "apitest_runtime.py").read_text()
    assert "APITEST_BEARERAUTH_TOKEN" in (gql_suite / "conftest.py").read_text()


def test_clean_graphql_server_passes_the_whole_suite(gql_suite):
    with gql_server() as url:
        results, proc = run_suite(gql_suite, url, GQL_ENV)
    assert failed(results) == [], proc.stdout[-3000:]
    assert len(passed(results)) > 50
    for name in ("test_graphql_positive::test_query_pet_succeeds", "test_graphql_positive::test_mutation_createPet_succeeds",
                 "test_graphql_security::test_bola_pet", "test_graphql_security::test_query_depth_is_limited",
                 "test_graphql_security::test_field_authz_User_email", "test_graphql_security::test_introspection_disabled"):
        assert results[name][0] == "passed", (name, results.get(name))


GQL_SEEDED = {
    "introspection": "test_graphql_security::test_introspection_disabled",
    "nodepth": "test_graphql_security::test_query_depth_is_limited",
    "noalias": "test_graphql_security::test_alias_abuse_is_limited",
    "batching": "test_graphql_security::test_batching_is_disabled_or_limited",
    "fieldauth": "test_graphql_security::test_field_authz_User_email",
    "bola": "test_graphql_security::test_bola_pet",
    "suggest": "test_graphql_security::test_error_messages_do_not_suggest_schema_names",
    "verbose": "test_graphql_security::test_errors_do_not_leak_internals",
    "crash": "test_graphql_security::test_injection_pets[search[0]]",
}


@pytest.mark.parametrize("bug", sorted(GQL_SEEDED))
def test_seeded_graphql_flaw_is_detected(gql_suite, bug):
    with gql_server({bug}) as url:
        results, _ = run_suite(gql_suite, url, GQL_ENV)
    assert GQL_SEEDED[bug] in failed(results), (bug, failed(results))


def test_graphql_http_200_with_errors_is_not_success(gql_suite, tmp_path):
    """A server that answers every operation with 200 + errors must fail the positive tests (assert on the body)."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            raw = json.dumps({"errors": [{"message": "boom"}], "data": None}).encode()
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
        results, _ = run_suite(gql_suite, f"http://127.0.0.1:{srv.server_address[1]}", GQL_ENV, ["-k", "positive or smoke"])
    finally:
        srv.shutdown()
        srv.server_close()
    assert "test_graphql_positive::test_query_pet_succeeds" in failed(results)
    assert "test_graphql_smoke::test_smoke_health" in failed(results)


def test_graphql_unsupported_types_are_reported(gql_doc, tmp_path):
    with pytest.raises(IntakeError, match="Not available for GraphQL"):
        gen_pytest.generate(gql_doc, tmp_path / "x", ["contract"])


# ---------------------------------------------------------------- spec diff
def test_spec_diff_flags_breaking_changes(rest_doc):
    new = copy.deepcopy(rest_doc)
    eps = {e["id"]: e for e in new["endpoints"]}
    del new["endpoints"][[e["id"] for e in new["endpoints"]].index("adminStats")]            # endpoint removed
    pet = eps["getPet"]["responses"]["200"]["schema"]
    del pet["properties"]["owner_id"]                                                       # response field removed
    pet["properties"]["id"]["type"] = "string"                                              # response type changed
    body = eps["createPet"]["request_body"]["schema"]
    body["required"] = ["name", "age"]                                                      # newly required field
    body["properties"]["name"]["maxLength"] = 10                                            # tightened constraint
    eps["listPets"]["parameters"].append({"name": "region", "in": "query", "required": True, "schema": {"type": "string"}})
    eps["listPets"]["parameters"][2]["schema"]["enum"] = ["available"]                      # request enum value removed
    eps["health"]["auth_required"] = True
    eps["listPets"]["responses"]["200"]["schema"]["items"]["properties"]["extra"] = {"type": "string"}  # added: fine
    changes = spec_diff.diff(rest_doc, new)
    brk = {(c["where"], c["change"]) for c in changes if c["severity"] == "breaking"}
    text = "\n".join(f"{w}: {c}" for w, c in brk)
    for needle in ("GET /admin/stats: endpoint removed", "owner_id: response field removed", "response type changed",
                   ".age: field is now required", "maxLength tightened 50 -> 10", "new required parameter",
                   "request enum lost values", "authentication is now required"):
        assert needle in text, needle
    assert not any("extra" in w and s == "breaking" for s, w in [(c["severity"], c["where"]) for c in changes])
    assert spec_diff.diff(rest_doc, rest_doc) == []


def test_spec_diff_cli_exit_codes(tmp_path, rest_doc):
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    old.write_text(json.dumps(rest_doc))
    changed = copy.deepcopy(rest_doc)
    changed["endpoints"] = changed["endpoints"][1:]
    new.write_text(json.dumps(changed))
    run = lambda *a: subprocess.run([sys.executable, str(SCRIPTS / "spec_diff.py"), *a], capture_output=True, text=True)  # noqa: E731
    assert run(str(old), str(old)).returncode == 0
    p = run(str(old), str(new), "--format", "json")
    assert p.returncode == 1 and json.loads(p.stdout)["breaking"] >= 1
    assert run(str(old), str(new), "--fail-on", "never").returncode == 0
    assert run(str(old), str(tmp_path / "missing.json")).returncode == 2
    assert "| breaking |" in run(str(old), str(new), "--format", "markdown").stdout


def test_spec_diff_graphql(gql_doc):
    new = copy.deepcopy(gql_doc)
    ops = new["graphql"]["operations"]
    ops[:] = [o for o in ops if o["id"] != "query.health"]                                   # removed
    pet = next(o for o in ops if o["id"] == "query.pet")
    pet["args"].append({"name": "region", "type": "String!", "required": True, "list": False, "named": "String", "kind": "SCALAR"})
    del new["graphql"]["types"]["User"]["fields"][1]                                          # field removed
    new["graphql"]["types"]["Species"]["values"].remove("CAT")                                # enum value removed
    text = "\n".join(f"{c['severity']} {c['where']}: {c['change']}" for c in spec_diff.diff(gql_doc, new))
    for needle in ("breaking query.health: operation removed", "query.pet(region): new required argument",
                   "User.name: field removed", "enum Species: values removed ['CAT']"):
        assert needle in text, needle


# ---------------------------------------------------------------- unit: sampling and rendering
def test_sample_values_respect_constraints_and_redact_secrets():
    s = gen_common.sample({"type": "string", "minLength": 5, "maxLength": 8})
    assert 5 <= len(s) <= 8
    assert gen_common.sample({"type": "integer", "minimum": 3, "maximum": 9}) == 3
    assert gen_common.sample({"type": "string", "format": "email"}) == "apitest@example.com"
    assert gen_common.sample({"type": "string", "example": "CANARY"}, "password") == "REPLACE_ME"
    assert gen_common.sample({"type": "string"}, "api_key") == "REPLACE_ME"
    obj = gen_common.sample({"type": "object", "required": ["a"], "properties": {"a": {"enum": ["x"]}, "b": {"type": "integer"}}})
    assert obj == {"a": "x"}
    assert gen_common.sample({"$circular": "Node"}) is None


def test_py_renders_expressions_and_literals():
    assert gen_common.py({"a": [1, None, True], "b": gen_common.Expr('"x" * 3')}) == "{'a': [1, None, True], 'b': \"x\" * 3}"
    assert gen_common.py((1,)) == "(1,)"


# ---------------------------------------------------------------- hostile specs and gate hardening
def _hostile_doc(marker):
    pwn = f'__import__("os").system("touch {marker}")'
    evil = f'x"]; {pwn}; #'
    nl = f"/a\n{pwn}\n"
    body = {"type": "object", "required": [evil], "properties": {evil: {"type": "string", "enum": [evil]},
                                                                  "n": {"type": "integer", "minimum": 1}}}
    ep = lambda i, method, path, **kw: {  # noqa: E731
        "id": i, "method": method, "path": path, "summary": evil, "tags": [evil, "admin"], "deprecated": False,
        "auth_required": True, "security": [[evil]], "parameters": kw.get("parameters", []),
        "request_body": kw.get("request_body"), "responses": {"200": {"description": evil, "content_type": "application/json", "schema": body},
                                                              "201": {"description": "c", "content_type": "application/json", "schema": body}}}
    return {
        "source": {"type": "openapi", "version": "3.0.0", "file": evil}, "info": {"title": evil, "version": "1"},
        "servers": [f'http://example.com/"""\n{pwn}\n"""'], "warnings": [],
        "security_schemes": {evil: {"type": "http", "scheme": "bearer", "env": [f'APITEST_X"""\n{pwn}\n']}},
        "endpoints": [
            ep(evil, "GET", nl, parameters=[{"name": evil, "in": "query", "required": False, "schema": {"type": "string"}},
                                           {"name": "limit", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 1}}]),
            ep(evil + "2", "POST", "/things", request_body={"content_type": "application/json", "required": True, "schema": body}),
            ep(evil + "3", "GET", "/things/{" + evil + "}", parameters=[{"name": evil, "in": "path", "required": True, "schema": {"type": "string"}}]),
            ep(evil + "4", "PUT", "/things/{" + evil + "}", parameters=[{"name": evil, "in": "path", "required": True, "schema": {"type": "string"}}],
               request_body={"content_type": "application/json", "required": True, "schema": body}),
            ep(evil + "5", "DELETE", "/things/{" + evil + "}", parameters=[{"name": evil, "in": "path", "required": True, "schema": {"type": "string"}}]),
        ],
    }


def test_hostile_spec_strings_never_become_code(tmp_path):
    marker = tmp_path / "PWNED"
    doc = _hostile_doc(marker)
    out = tmp_path / "suite"
    gen_pytest.generate(doc, out, ["all"], f'http://127.0.0.1:1"""\n__import__("os").system("touch {marker}")')
    for f in out.glob("*.py"):
        ast.parse(f.read_text())
        tree = ast.parse(f.read_text())
        calls = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                 for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert "system" not in calls and "__import__" not in calls, f.name
    with rest_server() as url:  # collection imports every module; the run executes every generated test body
        results, proc = run_suite(out, url, {"APITEST_BEARERAUTH_TOKEN": "x", "APITEST_PATH_X": "1"}, [])
    assert not marker.exists(), "a hostile spec string executed code"


def test_hostile_graphql_names_and_urls_never_become_code(tmp_path, gql_doc):
    marker = tmp_path / "PWNED"
    pwn = f'__import__("os").system("touch {marker}")'
    doc = copy.deepcopy(gql_doc)
    doc["endpoints"][0]["path"] = f'/g"\n{pwn}\n'
    doc["servers"] = [f'http://x/"""\n{pwn}\n']
    for o in doc["graphql"]["operations"]:
        o["id"] = o["id"] + f'"]; {pwn}; #'
    out = tmp_path / "suite"
    gen_pytest.generate(doc, out, ["all"])
    with gql_server() as url:
        run_suite(out, url, GQL_ENV, [])
    assert not marker.exists()


def test_corporate_local_names_do_not_pass_the_gate(rest_suite, monkeypatch):
    rt = load_runtime(rest_suite)
    monkeypatch.delenv("APITEST_TARGET_ENV", raising=False)
    monkeypatch.delenv("APITEST_AUTHORIZED", raising=False)
    for host in ("127.0.0.1.nip.io", "127.0.0.1.evil.com", "127.example.com", "localhost.evil.com", "2130706433", "0x7f.1",
                 "billing.prod.corp.local", "payments.local", "0.0.0.0", "db.internal", "api.example.test", "10.0.0.5", "intranet"):
        assert rt.intrusive_skip_reason(f"https://{host}") is not None, host
    assert rt.intrusive_skip_reason("http://app.localhost:3000") is None
    for ok in ("http://127.0.0.1:8080", "http://127.1.2.3", "http://[::1]:9000/x", "http://localhost/"):
        assert rt.intrusive_skip_reason(ok) is None, ok
    for tricky in ("http://127.0.0.1@evil.com/", "http://evil.com\\@127.0.0.1/", "http://127.0.0.1 .evil.com/", "ftp://127.0.0.1/",
                   "http://evil.com#@127.0.0.1/", "http://127.0.0.1:80@evil.com:80/"):
        assert rt.intrusive_skip_reason(tricky) is not None, tricky
        assert rt.target_host(tricky) in (None, "evil.com"), tricky
    assert not rt.is_local_host("billing.prod.corp.local")


def test_production_target_only_receives_read_calls(rest_suite):
    with rest_server() as url:
        results, _ = run_suite(rest_suite, url, {**REST_ENV, "APITEST_TARGET_ENV": "production"},
                               ["-k", "test_createPet_valid_request or test_listPets_valid_request or test_updatePet_valid_request"])
    assert results["test_positive::test_listPets_valid_request"][0] == "passed"
    for name in ("test_createPet_valid_request", "test_updatePet_valid_request"):
        status, msg = results[f"test_positive::{name}"]
        assert status == "skipped" and "production" in msg
    from mock_rest import LAST_LOG
    assert all(m == "GET" for m, _ in LAST_LOG), LAST_LOG


def test_redirect_only_endpoint_expects_its_declared_3xx():
    ep = {"responses": {"302": {}}}
    assert gen_common.expected_ok_codes(ep) == [302]
    assert gen_common.expected_ok_codes({"responses": {"200": {}, "302": {}}}) == [200]
    assert gen_common.expected_ok_codes({"responses": {"404": {}}}) == []
