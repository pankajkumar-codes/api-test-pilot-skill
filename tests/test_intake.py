import json
import subprocess
import sys

import pytest

from conftest import FIXTURES, ROOT
import _common
import intake
import parse_curl
import parse_openapi
import parse_postman

SCRIPTS = ROOT / "skills" / "api-testing" / "scripts"
SECRETS = ["hunter2", "SUPERSECRETTOKEN123", "LEAKME", "sk_live_ABCDEF", "p@ss", '"abc"']


def eps(doc):
    return {e["id"]: e for e in doc["endpoints"]}


# ---------------------------------------------------------------- mini yaml
def test_mini_yaml_matches_json_loader():
    text = (FIXTURES / "petstore.yaml").read_text()
    mini = _common.mini_yaml(text)
    assert mini["info"]["version"] == "1.2.0"
    assert mini["paths"]["/pets"]["get"]["tags"] == ["pets"]
    assert mini["paths"]["/pets"]["post"]["responses"]["201"]["description"] == "created"
    yaml = pytest.importorskip("yaml")
    norm = lambda v: json.dumps(v, sort_keys=True, default=str)  # noqa: E731
    assert norm(yaml.safe_load(text)) == norm(mini)


def test_mini_yaml_scalars_and_block_text():
    d = _common.mini_yaml("a: 1\nb: 'x # y'\nc: [1, two, \"3\"]\nd: |\n  line1\n  line2\ne: {k: v}\nf: ~\n")
    assert d == {"a": 1, "b": "x # y", "c": [1, "two", "3"], "d": "line1\nline2", "e": {"k": "v"}, "f": None}


def test_mini_yaml_rejects_anchors():
    with pytest.raises(_common.IntakeError):
        _common.mini_yaml("a: &x 1\nb: *x\n")


# ---------------------------------------------------------------- openapi
@pytest.fixture(scope="module")
def pet():
    return parse_openapi.build(str(FIXTURES / "petstore.yaml"))


def test_openapi_basics(pet):
    assert pet["source"]["type"] == "openapi" and pet["source"]["version"] == "3.0.3"
    assert pet["servers"] == ["http://localhost:8080/v1"]
    assert {e["method"] + " " + e["path"] for e in pet["endpoints"]} == {
        "GET /pets", "POST /pets", "GET /pets/{petId}", "DELETE /pets/{petId}", "GET /health", "GET /admin/tree"}


def test_path_level_params_merged_and_ref_resolved(pet):
    names = {(p["name"], p["in"]) for p in eps(pet)["listPets"]["parameters"]}
    assert ("X-Trace-Id", "header") in names and ("limit", "query") in names
    limit = next(p for p in eps(pet)["listPets"]["parameters"] if p["name"] == "limit")
    assert limit["schema"]["maximum"] == 100 and limit["required"] is False


def test_path_param_required(pet):
    p = next(p for p in eps(pet)["getPet"]["parameters"] if p["name"] == "petId")
    assert p["required"] is True and p["schema"]["type"] == "integer"


def test_allof_merged(pet):
    schema = eps(pet)["getPet"]["responses"]["200"]["schema"]
    assert set(schema["properties"]) == {"name", "tag", "id"}
    assert set(schema["required"]) == {"name", "id"}
    assert schema["properties"]["tag"]["nullable"] is True


def test_circular_ref_terminates(pet):
    node = eps(pet)["tree"]["responses"]["200"]["schema"]
    assert "$circular" in json.dumps(node)


def test_auth_global_override_and_alternatives(pet):
    e = eps(pet)
    assert e["listPets"]["auth_required"] and e["listPets"]["security"] == [["bearerAuth"]]
    assert e["health"]["auth_required"] is False and e["health"]["security"] == []
    assert e["tree"]["security"] == [["apiKey"], ["oauth"]]


def test_security_schemes_env_names_only(pet):
    s = pet["security_schemes"]
    assert s["bearerAuth"]["env"] == ["APITEST_BEARERAUTH_TOKEN"]
    assert s["apiKey"]["env"] == ["APITEST_APIKEY_KEY"] and s["apiKey"]["name"] == "X-API-Key"
    assert s["oauth"]["flows"]["clientCredentials"]["scopes"] == ["read"]


def test_example_redacted_and_request_body(pet):
    body = eps(pet)["createPet"]["request_body"]
    assert body["required"] and body["content_type"] == "application/json"
    assert body["example"] == {"name": "Rex", "password": "<redacted>"}
    assert "hunter2" not in json.dumps(pet)


def test_missing_operation_id_generates_id_and_warns(pet):
    assert "DELETE_pets_petId" in eps(pet)
    assert any("no operationId" in w for w in pet["warnings"])
    assert any("DELETE /pets/{petId}: no operationId" in w for w in pet["warnings"])


def test_swagger2():
    d = parse_openapi.build(str(FIXTURES / "swagger2.json"))
    assert d["servers"] == ["https://api.example.test/v2"]
    post = next(e for e in d["endpoints"] if e["method"] == "POST")
    assert post["request_body"]["schema"]["required"] == ["n"]
    assert post["auth_required"] and d["security_schemes"]["basic"]["scheme"] == "basic"
    assert len(d["security_schemes"]["basic"]["env"]) == 2


def test_not_openapi_rejected():
    with pytest.raises(_common.IntakeError):
        parse_openapi.parse({"hello": "world"})


def test_remote_url_not_fetched():
    with pytest.raises(_common.IntakeError, match="not fetched"):
        _common.load_document("https://example.com/openapi.yaml")


def test_external_ref_warns_not_fetches():
    doc = {"openapi": "3.0.0", "info": {}, "paths": {"/x": {"get": {"responses": {
        "200": {"description": "", "content": {"application/json": {"schema": {"$ref": "other.yaml#/Foo"}}}}}}}}}
    out = parse_openapi.parse(doc)
    assert any("External $ref" in w for w in out["warnings"])


# ---------------------------------------------------------------- postman
def test_postman():
    d = parse_postman.build(str(FIXTURES / "postman.json"))
    e = eps(d)
    assert d["servers"] == ["http://localhost:3000"]
    create, get = e["POST_orders"], e["GET_orders_orderId"]
    assert create["auth_required"] and d["security_schemes"]["bearerAuth"]["env"] == ["APITEST_BEARERAUTH_TOKEN"]
    assert get["auth_required"] is False
    assert get["path"] == "/orders/{orderId}"
    assert any(p["name"] == "orderId" and p["in"] == "path" for p in get["parameters"])
    assert any(p["name"] == "dry" and p["in"] == "query" for p in create["parameters"])
    assert create["request_body"]["example"] == {"sku": "A1", "password": "<redacted>"}
    assert "201" in create["responses"] and create["tags"] == ["orders"]


def test_postman_never_leaks_secrets():
    blob = json.dumps(parse_postman.build(str(FIXTURES / "postman.json")))
    for s in ("SUPERSECRETTOKEN123", "LEAKME", "p@ss"):
        assert s not in blob


def test_postman_rejects_other_json():
    with pytest.raises(_common.IntakeError):
        parse_postman.parse({"foo": 1})


# ---------------------------------------------------------------- curl
def test_curl():
    d = parse_curl.build(str(FIXTURES / "examples.curl"))
    e = eps(d)
    assert d["servers"] == ["http://localhost:8080"]
    assert e["GET_v1_pets"]["security"] == [["bearerAuth"]]
    assert any(p["name"] == "limit" and p["example"] == "5" for p in e["GET_v1_pets"]["parameters"])
    assert e["POST_v1_pets"]["request_body"]["content_type"] == "application/json"
    assert e["POST_v1_pets"]["request_body"]["example"]["token"] == "<redacted>"
    assert "GET_v1_pets_id" in e and e["GET_v1_pets_id"]["security"] == [["basicAuth"]]
    assert e["DELETE_v1_pets_id"]["security"] == [["apiKeyAuth"]]
    assert d["security_schemes"]["apiKeyAuth"]["name"] == "X-API-Key"


def test_curl_never_leaks_secrets():
    blob = json.dumps(parse_curl.build(str(FIXTURES / "examples.curl")))
    for s in ("hunter2", "sk_live_ABCDEF", "zzz"):
        assert s not in blob
    assert "Credentials found in the input were NOT copied" in blob


def test_curl_method_inference_and_errors():
    assert parse_curl.parse_command("curl -d x=1 http://h/a")["method"] == "POST"
    assert parse_curl.parse_command("curl http://h/a")["method"] == "GET"
    with pytest.raises(_common.IntakeError):
        parse_curl.parse_text("echo hi")


# ---------------------------------------------------------------- intake / cli
@pytest.mark.parametrize("name,kind", [("petstore.yaml", "openapi"), ("swagger2.json", "openapi"),
                                       ("postman.json", "postman"), ("examples.curl", "curl")])
def test_detect(name, kind):
    assert intake.detect(str(FIXTURES / name)) == kind


def test_cli_end_to_end(tmp_path):
    out = tmp_path / "intake.json"
    r = subprocess.run([sys.executable, str(SCRIPTS / "intake.py"), str(FIXTURES / "petstore.yaml"), "-o", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert len(json.loads(out.read_text())["endpoints"]) == 6


def test_cli_summary_and_error_exit(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPTS / "parse_openapi.py"), str(FIXTURES / "petstore.yaml"), "--summary"],
                       capture_output=True, text=True)
    assert "GET     /pets" in r.stdout and "public" in r.stdout
    bad = subprocess.run([sys.executable, str(SCRIPTS / "intake.py"), str(tmp_path / "nope.yaml")], capture_output=True, text=True)
    assert bad.returncode == 2 and "error:" in bad.stderr and "Traceback" not in bad.stderr


# ---------------------------------------------------------------- canary secrets (regression for review of PR #1)
CANARIES = ["CANARYPW", "CANARYTOK", "CANARYSIG", "CANARYSID", "CANARYJWT", "CANARYPWD", "CANARYBEARER",
            "CANARYQ", "CANARYEX", "CANARYCOOKIE", "CANARYHDR"]


def _no_canaries(doc):
    blob = json.dumps(doc)
    leaked = [c for c in CANARIES if c in blob]
    assert not leaked, f"leaked: {leaked}"


def test_canary_curl_all_vectors():
    text = "\n".join([
        "curl https://alice:CANARYPW@api.example.com/v1/x",
        "curl --oauth2-bearer CANARYBEARER https://api.example.com/v1/me",
        "curl -c jar.txt --max-redirs 5 -b CANARYCOOKIE https://api.example.com/v1/jar",
        "curl 'https://api.example.com/v1/q?signature=CANARYSIG&sid=CANARYSID&jwt=CANARYJWT&token=CANARYTOK&page=2'",
        "curl -X POST https://api.example.com/v1/p -H 'X-Custom-Token: CANARYHDR' -H 'Cookie: s=CANARYCOOKIE'"
        " -d '{\"pwd\":\"CANARYPWD\",\"ok\":true,\"secret\":1.5}'",
    ])
    d = parse_curl.parse_text(text)
    _no_canaries(d)
    assert d["servers"] == ["https://api.example.com"]  # no http://TOKEN, http://jar.txt, http://5
    assert any(p["name"] == "page" and p["example"] == "2" for e in d["endpoints"] for p in e["parameters"])
    me = next(e for e in d["endpoints"] if e["path"] == "/v1/me")
    assert me["security"] == [["bearerAuth"]]
    x = next(e for e in d["endpoints"] if e["path"] == "/v1/x")
    assert x["security"] == [["basicAuth"]]
    post = next(e for e in d["endpoints"] if e["method"] == "POST")
    assert post["request_body"]["example"] == {"pwd": "<redacted>", "ok": True, "secret": "<redacted>"}


def test_curl_stray_flag_argument_never_becomes_url():
    c = parse_curl.parse_command("curl --some-future-flag VALUE https://h.example/a")
    assert c["url"] == "https://h.example/a"
    with pytest.raises(_common.IntakeError):
        parse_curl.parse_command("curl --unknown-flag 5")


def test_canary_openapi():
    doc = {"openapi": "3.0.0", "info": {}, "servers": [{"url": "https://u:CANARYPW@api.example.com/v1"}],
           "paths": {"/x": {"get": {
               "parameters": [{"name": "signature", "in": "query", "example": "CANARYSIG", "schema": {"type": "string", "default": "CANARYSIG"}},
                              {"name": "sid", "in": "query", "schema": {"type": "string", "enum": ["CANARYSID"]}},
                              {"name": "X-Auth-Token", "in": "header", "example": "CANARYTOK", "schema": {"type": "string"}}],
               "requestBody": {"content": {"application/json": {
                   "schema": {"type": "object", "properties": {"pwd": {"type": "string", "example": "CANARYPWD"}}},
                   "example": {"pwd": "CANARYPWD", "jwt": "CANARYJWT"}}}},
               "responses": {"200": {"description": "ok"}}}}}}
    d = parse_openapi.parse(doc)
    _no_canaries(d)
    assert d["servers"] == ["https://api.example.com/v1"]
    assert any("stripped" in w for w in d["warnings"])


def test_canary_postman():
    coll = {"info": {"name": "c", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "variable": [{"key": "baseUrl", "value": "https://u:CANARYPW@api.example.com"}],
            "item": [{"name": "a", "request": {
                "method": "POST", "header": [{"key": "X-Auth-Token", "value": "CANARYHDR"}],
                "url": {"raw": "https://u:CANARYPW@api.example.com/a?token=CANARYTOK&signature=CANARYSIG&q=1",
                        "query": [{"key": "token", "value": "CANARYTOK"}, {"key": "signature", "value": "CANARYSIG"}, {"key": "q", "value": "1"}]},
                "body": {"mode": "raw", "raw": "{\"pwd\":\"CANARYPWD\"}", "options": {"raw": {"language": "json"}}}}},
                     {"name": "b", "request": {"method": "GET", "url": "https://api.example.com/b?sid=CANARYSID&jwt=CANARYJWT"}}]}
    d = parse_postman.parse(coll)
    _no_canaries(d)
    assert d["servers"] == ["https://api.example.com"]
    a = next(e for e in d["endpoints"] if e["path"] == "/a")
    assert any(p["name"] == "q" and p["example"] == "1" for p in a["parameters"])


def test_redact_example_any_scalar_and_shared_regex():
    assert _common.redact_example({"pwd": True, "sig": 1.5, "n": 1, "otp": 123456}) == {
        "pwd": "<redacted>", "sig": "<redacted>", "n": 1, "otp": "<redacted>"}
    for name in ("pwd", "signature", "sid", "jwt", "auth", "bearer", "otp", "X-API-Key"):
        assert _common.is_secret_name(name)
    assert not _common.is_secret_name("page")
    assert not hasattr(parse_curl, "SECRET_KEY_RE")  # parsers share _common's single regex
