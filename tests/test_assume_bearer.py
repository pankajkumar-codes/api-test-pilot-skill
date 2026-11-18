"""Specs with no security scheme: a warning, and --assume-bearer / --public-paths."""
from __future__ import annotations

import json
import subprocess
import sys

from conftest import ROOT

SPEC = {"openapi": "3.0.0", "info": {"title": "n", "version": "1"},
        "paths": {"/health": {"get": {"responses": {"200": {"description": "ok"}}}},
                  "/docs/a": {"get": {"responses": {"200": {"description": "ok"}}}},
                  "/things": {"get": {"responses": {"200": {"description": "ok"}}}}}}


def intake(tmp_path, *extra):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps(SPEC))
    out = tmp_path / "i.json"
    subprocess.run([sys.executable, str(ROOT / "skills/api-testing/scripts/intake.py"), str(spec), "-o", str(out), *extra],
                   check=True, capture_output=True, text=True)
    return json.loads(out.read_text())


def test_warns_when_no_scheme_is_declared(tmp_path):
    doc = intake(tmp_path)
    assert any("declares no security scheme" in w and "--assume-bearer" in w for w in doc["warnings"])
    assert not any(e["auth_required"] for e in doc["endpoints"])


def test_assume_bearer_protects_everything_except_public_paths(tmp_path):
    doc = intake(tmp_path, "--assume-bearer", "--public-paths", "/health,/docs*")
    by = {e["path"]: e for e in doc["endpoints"]}
    assert by["/things"]["auth_required"] and by["/things"]["security"] == [["bearerAuth"]]
    assert not by["/health"]["auth_required"] and not by["/docs/a"]["auth_required"]
    assert doc["security_schemes"]["bearerAuth"]["env"] == ["APITEST_BEARERAUTH_TOKEN"]
    assert any("--assume-bearer" in w for w in doc["warnings"])
