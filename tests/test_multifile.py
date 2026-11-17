"""Specs split over several files: relative $refs are read, anything outside the spec folder is not."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import parse_openapi
SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "api-testing" / "scripts"


def _write(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root / "openapi.yaml"


ROOT = """openapi: 3.0.0
info: {title: mf, version: "1"}
servers: [{url: "http://127.0.0.1:1"}]
paths:
  /pets:
    $ref: ./paths/pets.yaml
components:
  schemas:
    Id: {type: integer, readOnly: true}
"""
PETS = """post:
  operationId: createPet
  requestBody:
    required: true
    content:
      application/json:
        schema: {$ref: ../schemas/pet.yaml#/Pet}
  responses:
    "201":
      description: ok
      content:
        application/json:
          schema: {$ref: ../schemas/pet.yaml#/Pet}
"""
PET = """Pet:
  type: object
  required: [name]
  properties:
    id: {$ref: ../openapi.yaml#/components/schemas/Id}
    name: {type: string}
    friend: {$ref: "#/Pet"}
"""


def test_path_items_and_schemas_in_other_files_are_resolved(tmp_path):
    spec = _write(tmp_path, {"openapi.yaml": ROOT, "paths/pets.yaml": PETS, "schemas/pet.yaml": PET})
    doc = parse_openapi.build(str(spec))
    ep = doc["endpoints"][0]
    props = ep["request_body"]["schema"]["properties"]
    assert ep["id"] == "createPet" and props["name"] == {"type": "string"}
    assert props["id"]["readOnly"] is True          # a ref from a copied file back to the root file
    assert props["friend"] == {"$circular": "Pet"}   # a local ref inside a copied file points at that file
    assert not [w for w in doc["warnings"] if "$ref" in w]


def test_intake_cli_reads_a_split_spec(tmp_path):
    spec = _write(tmp_path, {"openapi.yaml": ROOT, "paths/pets.yaml": PETS, "schemas/pet.yaml": PET})
    out = tmp_path / "intake.json"
    subprocess.run([sys.executable, str(SCRIPTS / "intake.py"), str(spec), "-o", str(out)], check=True, capture_output=True)
    assert json.loads(out.read_text())["endpoints"][0]["id"] == "createPet"


def test_files_outside_the_spec_folder_are_never_read(tmp_path):
    (tmp_path / "outside.yaml").write_text("Leak:\n  type: string\n  example: canary-outside-value\n")
    inner = tmp_path / "api"
    spec = _write(inner, {"openapi.yaml": ROOT.replace("./paths/pets.yaml", "./pets.yaml"),
                          "pets.yaml": PETS.replace("../schemas/pet.yaml#/Pet", "../outside.yaml#/Leak")})
    doc = parse_openapi.build(str(spec))
    assert "canary-outside-value" not in json.dumps(doc)
    assert any("outside the spec folder" in w for w in doc["warnings"])


def test_absolute_paths_urls_and_missing_files_are_refused_with_a_warning(tmp_path):
    for ref in ("/etc/hostname", "https://example.com/x.yaml#/A", "./nope.yaml#/A"):
        spec = _write(tmp_path, {"openapi.yaml": ROOT.replace("./paths/pets.yaml", "./pets.yaml"),
                                 "pets.yaml": PETS.replace("../schemas/pet.yaml#/Pet", ref)})
        doc = parse_openapi.build(str(spec))
        assert doc["warnings"] and doc["endpoints"][0]["request_body"]["schema"] == {"$unresolved": ref}, ref


def test_a_symlink_out_of_the_folder_is_refused(tmp_path):
    (tmp_path / "secret.yaml").write_text("S:\n  type: string\n  example: canary-linked-value\n")
    inner = tmp_path / "api"
    spec = _write(inner, {"openapi.yaml": ROOT.replace("./paths/pets.yaml", "./pets.yaml"),
                          "pets.yaml": PETS.replace("../schemas/pet.yaml#/Pet", "./link.yaml#/S")})
    (inner / "link.yaml").symlink_to(tmp_path / "secret.yaml")
    doc = parse_openapi.build(str(spec))
    assert "canary-linked-value" not in json.dumps(doc) and any("outside the spec folder" in w for w in doc["warnings"])


def test_two_files_that_point_at_each_other_do_not_loop(tmp_path):
    spec = _write(tmp_path, {"openapi.yaml": ROOT.replace("./paths/pets.yaml", "./a.yaml"),
                             "a.yaml": "post:\n  operationId: x\n  responses:\n    '200':\n      description: ok\n      content:\n        application/json:\n          schema: {$ref: ./b.yaml#/B}\n",
                             "b.yaml": "B:\n  type: object\n  properties:\n    back: {$ref: ./b2.yaml#/C}\n",
                             "b2.yaml": "C:\n  type: object\n  properties:\n    again: {$ref: ./b.yaml#/B}\n"})
    doc = parse_openapi.build(str(spec))
    assert doc["endpoints"][0]["responses"]["200"]["schema"]["type"] == "object"


def test_too_many_or_too_large_files_are_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(parse_openapi, "MAX_FILES", 1)
    spec = _write(tmp_path, {"openapi.yaml": ROOT, "paths/pets.yaml": PETS, "schemas/pet.yaml": PET})
    assert any("more than 1 referenced files" in w for w in parse_openapi.build(str(spec))["warnings"])
    monkeypatch.setattr(parse_openapi, "MAX_FILES", 50)
    monkeypatch.setattr(parse_openapi, "MAX_FILE_BYTES", 10)
    assert any("larger than" in w for w in parse_openapi.build(str(spec))["warnings"])


def test_an_in_memory_spec_still_leaves_file_refs_unresolved():
    doc = parse_openapi.parse({"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "paths": {"/a": {"get": {
        "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "./x.yaml#/A"}}}}}}}}})
    assert any("External $ref" in w for w in doc["warnings"])


def _bomb(levels: int = 9) -> str:
    lines = ["x-b0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    for i in range(1, levels):
        lines.append(f"x-b{i}: &a{i} [" + ", ".join([f"*a{i - 1}"] * 10) + "]")
    return "\n".join(lines) + "\n"


def _finishes(fn, seconds: float = 10.0) -> bool:
    import threading
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    t.join(seconds)
    return not t.is_alive()


def test_a_yaml_alias_bomb_is_not_expanded(tmp_path):
    single = _write(tmp_path / "single", {"openapi.yaml": ROOT.replace("    $ref: ./paths/pets.yaml\n", "    get: {responses: {'200': {description: ok}}}\n") + _bomb()})
    split = _write(tmp_path / "split", {"openapi.yaml": ROOT, "paths/pets.yaml": PETS, "schemas/pet.yaml": PET + _bomb()})
    assert _finishes(lambda: parse_openapi.build(str(single)))
    assert _finishes(lambda: parse_openapi.build(str(split)))


def test_a_spec_without_file_refs_is_left_untouched():
    doc = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "paths": {}}
    out, warnings = parse_openapi.bundle(doc, "spec.yaml")
    assert out is doc and warnings == []


def test_hostile_ref_strings_become_warnings_not_crashes(tmp_path):
    for ref in ("./a\x00b.yaml#/A", "./" + "n" * 5000 + ".yaml#/A"):
        spec = _write(tmp_path, {"openapi.yaml": ROOT.replace("./paths/pets.yaml", "./pets.yaml"),
                                 "pets.yaml": PETS.replace("../schemas/pet.yaml#/Pet", ref)})
        doc = parse_openapi.build(str(spec))
        assert doc["warnings"], ref[:20]


def test_a_deeply_nested_referenced_file_is_skipped_with_a_warning(tmp_path):
    spec = _write(tmp_path, {"openapi.yaml": ROOT.replace("./paths/pets.yaml", "./pets.yaml"),
                             "pets.yaml": PETS.replace("../schemas/pet.yaml#/Pet", "./deep.json#/A")})
    (tmp_path / "deep.json").write_text('{"A": ' + "[" * 100_000 + "]" * 100_000 + "}")
    doc = parse_openapi.build(str(spec))
    assert any("deep.json" in w for w in doc["warnings"])


def test_warnings_for_a_broken_file_do_not_quote_it(tmp_path):
    spec = _write(tmp_path, {"openapi.yaml": ROOT.replace("./paths/pets.yaml", "./pets.yaml"),
                             "pets.yaml": PETS.replace("../schemas/pet.yaml#/Pet", "./bad.yaml#/A"),
                             "bad.yaml": "password: canary-in-broken-file\n  : [unclosed\n"})
    text = json.dumps(parse_openapi.build(str(spec))["warnings"])
    assert "bad.yaml" in text and "canary-in-broken-file" not in text


def test_only_spec_file_types_are_loaded(tmp_path):
    spec = _write(tmp_path, {"openapi.yaml": ROOT.replace("./paths/pets.yaml", "./pets.yaml"),
                             "pets.yaml": PETS.replace("../schemas/pet.yaml#/Pet", "./.env#/KEY"),
                             ".env": "KEY: canary-env\n"})
    doc = parse_openapi.build(str(spec))
    assert "canary-env" not in json.dumps(doc) and any("not a .yaml" in w for w in doc["warnings"])


def test_a_spec_using_the_bundle_key_is_refused_loudly(tmp_path):
    import pytest
    spec = _write(tmp_path, {"openapi.yaml": ROOT + f"{parse_openapi.BUNDLE_KEY}: mine\n", "paths/pets.yaml": PETS, "schemas/pet.yaml": PET})
    with pytest.raises(parse_openapi.IntakeError):
        parse_openapi.build(str(spec))


def test_a_ref_string_inside_an_example_is_data(tmp_path):
    pets = PETS.replace("schema: {$ref: ../schemas/pet.yaml#/Pet}\n  responses", "schema: {$ref: ../schemas/pet.yaml#/Pet}\n        example: {note: {$ref: ./missing.yaml}}\n  responses", 1)
    spec = _write(tmp_path, {"openapi.yaml": ROOT, "paths/pets.yaml": pets, "schemas/pet.yaml": PET})
    assert not any("missing.yaml" in w for w in parse_openapi.build(str(spec))["warnings"])


def test_property_and_response_names_that_look_like_keywords_still_resolve(tmp_path):
    pets = PETS.replace("../schemas/pet.yaml#/Pet", "../schemas/pet.yaml#/Wrap", 1)
    wrap = PET + """Wrap:
  type: object
  properties:
    default: {$ref: "#/Pet"}
    example: {$ref: "#/Pet"}
    enum: {$ref: ./pet.yaml#/Pet}
"""
    spec = _write(tmp_path, {"openapi.yaml": ROOT, "paths/pets.yaml": pets, "schemas/pet.yaml": wrap})
    doc = parse_openapi.build(str(spec))
    props = doc["endpoints"][0]["request_body"]["schema"]["properties"]
    assert props["enum"]["type"] == "object" and props["default"]["type"] == "object" and props["example"]["type"] == "object"
    assert not [w for w in doc["warnings"] if "$ref" in w]
    split = _write(tmp_path / "r", {"openapi.yaml": ROOT.replace("    $ref: ./paths/pets.yaml\n", "    get:\n      responses:\n        default:\n          $ref: ./err.yaml\n"),
                                    "err.yaml": "description: failed\ncontent:\n  application/json:\n    schema: {type: object}\n"})
    assert parse_openapi.build(str(split))["endpoints"][0]["responses"]["default"]["schema"] == {"type": "object"}
