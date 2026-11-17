"""OpenAPI webhooks and callbacks are listed in the intake, never tested."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import gen_pytest
import parse_openapi

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "api-testing" / "scripts"
PAYLOAD = {"type": "object", "properties": {"id": {"type": "integer"}, "status": {"type": "string"}, "secret": {"type": "string", "example": "canary-hook-secret"}}}
SPEC = {
    "openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
    "webhooks": {"orderShipped": {"post": {"summary": "An order shipped", "requestBody": {"content": {"application/json": {"schema": PAYLOAD,
                 "example": {"id": 1, "status": "shipped", "password": "canary-hook-pw"}}}}, "responses": {"200": {"description": "ok"}, "410": {"description": "stop"}}}}},
    "paths": {"/subscriptions": {"post": {
        "operationId": "subscribe", "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {"callbackUrl": {"type": "string"}}}}}},
        "responses": {"201": {"description": "created"}},
        "callbacks": {"onEvent": {"{$request.body#/callbackUrl}": {"post": {"requestBody": {"content": {"application/json": {"schema": PAYLOAD}}},
                                                                         "responses": {"200": {"description": "ok"}}}}}}}}}}


def test_webhooks_and_callbacks_are_listed_with_their_payloads():
    doc = parse_openapi.parse(SPEC)
    by_name = {h["name"]: h for h in doc["webhooks"]}
    hook, cb = by_name["orderShipped"], by_name["onEvent"]
    assert hook["kind"] == "webhook" and hook["method"] == "POST" and hook["responses"] == ["200", "410"]
    assert hook["request_body"]["schema"]["properties"]["status"] == {"type": "string"}
    assert cb["kind"] == "callback" and cb["parent"] == "subscribe" and cb["expression"] == "{$request.body#/callbackUrl}"


def test_no_webhooks_means_no_key_and_endpoints_are_unchanged():
    spec = {k: v for k, v in SPEC.items() if k != "webhooks"}
    spec["paths"] = {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}}
    assert "webhooks" not in parse_openapi.parse(spec)


def test_secrets_in_hook_payloads_are_not_emitted():
    text = json.dumps(parse_openapi.parse(SPEC))
    assert "canary-hook-secret" not in text and "canary-hook-pw" not in text


def test_hooks_are_not_turned_into_tests(tmp_path):
    (tmp_path / "spec.json").write_text(json.dumps(SPEC))
    gen_pytest.generate(parse_openapi.build(str(tmp_path / "spec.json")), tmp_path / "suite", ["all"])
    text = "".join(f.read_text() for f in (tmp_path / "suite").glob("*.py"))
    assert "orderShipped" not in text and "onEvent" not in text


def test_a_hostile_webhook_map_does_not_crash():
    for hooks in ({"a": None}, {"a": {"post": 5}}, {"a": {"$ref": "#/nope"}}, [1, 2], {"a": {"post": {"requestBody": {"$ref": "#/nope"}}}}):
        spec = {**SPEC, "webhooks": hooks}
        parse_openapi.parse(spec)


def test_the_number_of_listed_hooks_is_capped(monkeypatch):
    monkeypatch.setattr(parse_openapi, "MAX_HOOKS", 1)
    doc = parse_openapi.parse(SPEC)
    assert len(doc["webhooks"]) == 1 and any("webhooks and callbacks" in w for w in doc["warnings"])


def test_the_summary_lists_them(tmp_path):
    (tmp_path / "spec.json").write_text(json.dumps(SPEC))
    out = subprocess.run([sys.executable, str(SCRIPTS / "intake.py"), str(tmp_path / "spec.json"), "--summary"], capture_output=True, text=True, check=True).stdout
    assert "Webhooks and callbacks (2" in out and "orderShipped (webhook)" in out and "onEvent (callback of subscribe)" in out


def test_a_whole_file_ref_does_not_leak_the_internal_slot_name(tmp_path):
    (tmp_path / "leaf.yaml").write_text("type: string\n")
    spec = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "paths": {"/a": {"get": {"responses": {"200": {"description": "ok", "content": {
        "application/json": {"schema": {"$ref": "./leaf.yaml"}}}}}}}}}
    (tmp_path / "openapi.json").write_text(json.dumps(spec))
    sch = parse_openapi.build(str(tmp_path / "openapi.json"))["endpoints"][0]["responses"]["200"]["schema"]
    assert sch == {"type": "string"}


def test_wrongly_typed_hook_fields_become_warnings_not_crashes():
    bad_ops = [
        {"post": {"responses": 200}},
        {"post": {"responses": True, "summary": 5}},
        {"post": {"description": 12345}},
        {"post": {"description": ["a"], "requestBody": {"content": {"application/json": 7}}}},
        {"post": {"requestBody": {"content": {"application/json": {"schema": 5, "example": {"a": 1}}}}}},
        {"post": {"requestBody": {"content": []}}},
        {"post": {"requestBody": 3}},
    ]
    for i, op in enumerate(bad_ops):
        spec = {**SPEC, "webhooks": {"w": op}}
        doc = parse_openapi.parse(spec)
        assert [h["name"] for h in doc.get("webhooks", [])].count("onEvent") == 1, i   # the good callback is still there


def test_one_bad_hook_does_not_hide_the_others():
    spec = {**SPEC, "webhooks": {"bad": {"post": {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/nope"}, "example": object}}}}},
                                 "good": SPEC["webhooks"]["orderShipped"]}}
    names = [h["name"] for h in parse_openapi.parse(spec)["webhooks"]]
    assert "good" in names and "onEvent" in names


def test_the_cap_warning_only_fires_when_something_was_dropped(monkeypatch):
    monkeypatch.setattr(parse_openapi, "MAX_HOOKS", 2)
    doc = parse_openapi.parse(SPEC)        # exactly two hooks: nothing dropped
    assert len(doc["webhooks"]) == 2 and not any("webhooks and callbacks" in w for w in doc["warnings"])
    monkeypatch.setattr(parse_openapi, "MAX_HOOKS", 1)
    assert any("1 were not listed" in w for w in parse_openapi.parse(SPEC)["warnings"])


def test_wrongly_typed_paths_and_responses_are_warnings_too():
    base = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"}}
    assert any("'paths'" in w for w in parse_openapi.parse({**base, "paths": 5})["warnings"])
    doc = parse_openapi.parse({**base, "paths": {"/a": {"get": {"responses": True}}}})
    assert any("'responses'" in w for w in doc["warnings"]) and doc["endpoints"][0]["responses"] == {}
