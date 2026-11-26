"""seed_ids.py: the optional setup stage that creates records and writes an ids file."""
from __future__ import annotations

import json

import pytest

import parse_openapi
import seed_ids
from conftest import FIXTURES
from mock_rest import LAST_LOG, running

TOKEN = "tokA"


@pytest.fixture()
def intake(tmp_path):
    path = tmp_path / "intake.json"
    path.write_text(json.dumps(parse_openapi.build(str(FIXTURES / "mockapi.yaml"))))
    return path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("APITEST_TARGET_ENV", "APITEST_AUTHORIZED", "APITEST_READ_ONLY", "APITEST_EXAMPLES_FILE", "APITEST_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    for var in ("APITEST_PATH_PETID", "APITEST_PATH_TOYID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APITEST_BEARERAUTH_TOKEN", TOKEN)


def run(*args):
    return seed_ids.main([str(a) for a in args])


def test_creates_one_record_per_resource_and_cleans_up(intake, tmp_path, capsys):
    ids = tmp_path / "ids.json"
    with running(set()) as url:
        assert run(intake, "--out", ids, "--base-url", url) == 0
        data = json.loads(ids.read_text())
        assert set(data["path"]) >= {"petId"}
        assert {c["param"] for c in data["created"]} == set(data["path"])
        out = capsys.readouterr().out
        assert "APITEST_IDS_FILE=" in out and "--cleanup" in out
        assert TOKEN not in out

        assert run(intake, "--cleanup", ids, "--base-url", url) == 0
        after = json.loads(ids.read_text())
        assert "created" not in after and "path" not in after


def test_the_created_pet_exists_on_the_server_until_cleanup(intake, tmp_path):
    ids = tmp_path / "ids.json"
    with running(set()) as url:
        run(intake, "--out", ids, "--base-url", url)
        pet = json.loads(ids.read_text())["path"]["petId"]
        import urllib.request
        req = urllib.request.Request(f"{url}/pets/{pet}", headers={"Authorization": f"Bearer {TOKEN}"})
        assert urllib.request.urlopen(req).status == 200
        run(intake, "--cleanup", ids, "--base-url", url)
        with pytest.raises(Exception) as err:
            urllib.request.urlopen(req)
        assert "404" in str(err.value)


def test_an_id_you_set_is_kept_and_nothing_is_made_for_it(intake, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APITEST_PATH_PETID", "1")
    ids = tmp_path / "ids.json"
    ids.write_text(json.dumps({"path": {"toyId": "7"}, "victim": {"petId": "2"}}))
    with running(set()) as url:
        run(intake, "--out", ids, "--base-url", url)
    data = json.loads(ids.read_text())
    assert data["path"] == {"toyId": "7"}
    assert data["victim"] == {"petId": "2"}
    assert "created" not in data
    assert "already set" in capsys.readouterr().out


def test_dry_run_sends_nothing_and_writes_nothing(intake, tmp_path, capsys):
    ids = tmp_path / "ids.json"
    LAST_LOG.clear()
    with running(set()) as url:
        assert run(intake, "--out", ids, "--base-url", url, "--dry-run") == 0
    assert not ids.exists()
    assert not LAST_LOG
    assert "would create" in capsys.readouterr().out


def test_missing_credentials_name_the_variable_and_send_nothing(intake, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("APITEST_BEARERAUTH_TOKEN")
    ids = tmp_path / "ids.json"
    with running(set()) as url:
        assert run(intake, "--out", ids, "--base-url", url) == 1
    out = capsys.readouterr().out
    assert "APITEST_BEARERAUTH_TOKEN" in out
    assert "created" not in json.loads(ids.read_text())


def test_a_refused_create_is_reported_with_the_status_only(intake, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APITEST_BEARERAUTH_TOKEN", "wrong-token-canary")
    ids = tmp_path / "ids.json"
    with running(set()) as url:
        assert run(intake, "--out", ids, "--base-url", url) == 1
    out = capsys.readouterr().out
    assert "401" in out and "wrong-token-canary" not in out


@pytest.mark.parametrize("env,words", [
    ({"APITEST_TARGET_ENV": "production"}, "production"),
    ({"APITEST_READ_ONLY": "yes"}, "read-only"),
])
def test_the_safety_gate_refuses_before_any_request(intake, tmp_path, monkeypatch, capsys, env, words):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    LAST_LOG.clear()
    with running(set()) as url:
        assert run(intake, "--out", tmp_path / "ids.json", "--base-url", url) == 2
    assert words in capsys.readouterr().err
    assert not LAST_LOG


def test_a_remote_target_needs_the_confirmation_variables(intake, tmp_path, monkeypatch, capsys):
    ids = tmp_path / "ids.json"
    assert run(intake, "--out", ids, "--base-url", "https://api.example.com") == 2
    assert "APITEST_TARGET_ENV" in capsys.readouterr().err
    monkeypatch.setenv("APITEST_TARGET_ENV", "staging")
    assert run(intake, "--out", ids, "--base-url", "https://api.example.com") == 2
    assert "APITEST_AUTHORIZED" in capsys.readouterr().err
    assert not ids.exists()


def test_cleanup_also_stops_on_production(intake, tmp_path, monkeypatch, capsys):
    ids = tmp_path / "ids.json"
    ids.write_text(json.dumps({"created": [{"param": "petId", "id": "1", "delete": "/pets/{petId}"}]}))
    monkeypatch.setenv("APITEST_TARGET_ENV", "prod")
    assert run(intake, "--cleanup", ids, "--base-url", "http://127.0.0.1:1") == 2


def test_a_bad_ids_file_is_refused_without_overwriting_it(intake, tmp_path, capsys):
    ids = tmp_path / "ids.json"
    ids.write_text("not json")
    assert run(intake, "--out", ids, "--base-url", "http://127.0.0.1:1") == 2
    assert ids.read_text() == "not json"


def test_unique_fields_get_a_run_tag_and_examples_file_values_win(intake, tmp_path, monkeypatch):
    ex = tmp_path / "ex.json"
    ex.write_text(json.dumps({"createPet.name": "Chosen"}))
    monkeypatch.setenv("APITEST_EXAMPLES_FILE", str(ex))
    doc = json.loads(intake.read_text())
    ep = next(e for e in doc["endpoints"] if e["id"] == "createPet")
    assert seed_ids.create_body(ep)["name"].startswith("Chosen")


def test_tag_respects_max_length_and_email_shape():
    assert seed_ids.tag("a@b.com", None).endswith("@b.com")
    assert len(seed_ids.tag("x" * 10, 12)) <= 12


def test_required_header_and_query_parameters_are_sent_with_the_create():
    ep = {"id": "createThing", "method": "POST", "path": "/things",
          "parameters": [{"name": "Idempotency-Key", "in": "header", "required": True, "schema": {"type": "string"}},
                         {"name": "X-Trace", "in": "header", "required": False, "schema": {"type": "string"}},
                         {"name": "Authorization", "in": "header", "required": True, "schema": {"type": "string"}},
                         {"name": "dry", "in": "query", "required": True, "schema": {"type": "boolean"}}]}
    headers, query = seed_ids.required_params(ep)
    assert set(headers) == {"Idempotency-Key"} and len(headers["Idempotency-Key"]) == 36
    assert headers != seed_ids.required_params(ep)[0]   # a fresh key on every create
    assert set(query) == {"dry"}


class _StubClient:
    def __init__(self):
        self.sent = []

    def send(self, method, path, headers, query, body=None):
        self.sent.append({"method": method, "path": path, "headers": dict(headers), "query": dict(query)})
        return 201, b'{"id": "t1"}', {}


def test_seed_sends_required_parameters_and_credentials_win_a_name_clash(tmp_path, monkeypatch):
    spec = {"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
            "components": {"securitySchemes": {"tenant": {"type": "apiKey", "in": "header", "name": "X-Tenant"}}},
            "security": [{"tenant": []}],
            "paths": {"/things": {"post": {"operationId": "createThing",
                                           "parameters": [{"name": "Idempotency-Key", "in": "header", "required": True,
                                                           "schema": {"type": "string"}},
                                                          {"name": "X-Tenant", "in": "header", "required": True,
                                                           "schema": {"type": "string", "example": "from-spec"}},
                                                          {"name": "X-Optional", "in": "header", "required": False,
                                                           "schema": {"type": "string"}},
                                                          {"name": "dry", "in": "query", "required": True,
                                                           "schema": {"type": "boolean", "example": False}}],
                                           "requestBody": {"content": {"application/json": {"schema": {
                                               "type": "object", "properties": {"name": {"type": "string"}}}}}},
                                           "responses": {"201": {"description": "made"}}}},
                      "/things/{thingId}": {"get": {"operationId": "getThing",
                                                    "parameters": [{"name": "thingId", "in": "path", "required": True,
                                                                    "schema": {"type": "string"}}],
                                                    "responses": {"200": {"description": "ok"}}}}}}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    doc = parse_openapi.build(str(path))
    env = doc["security_schemes"]["tenant"]["env"][0]
    monkeypatch.setenv("APITEST_SEND_OPTIONAL_HEADERS", "X-Optional")
    monkeypatch.setenv(env, "from-env")
    client = _StubClient()
    ids, failures = seed_ids.seed(doc, {}, client, dry=False)
    assert failures == 0 and ids["path"]["thingId"] == "t1"
    sent = client.sent[0]
    assert sent["headers"]["X-Tenant"] == "from-env"            # the credential, not the spec's example
    assert len(sent["headers"]["Idempotency-Key"]) == 36
    assert "X-Optional" not in sent["headers"]                  # optional headers are not sent while seeding
    assert set(sent["query"]) == {"dry"}
