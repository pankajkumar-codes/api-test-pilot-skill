"""run_all.py (one command from spec to report), check_env.py and `--types all,fuzz`."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import gen_pytest
import parse_openapi
from conftest import ROOT

SCRIPTS = ROOT / "skills/api-testing/scripts"
OK = {"200": {"description": "ok"}}
SPEC = {"openapi": "3.0.0", "info": {"title": "d", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
        "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}}, "security": [{"bearerAuth": []}],
        "paths": {"/things": {"get": {"responses": OK}}, "/logout": {"post": {"responses": OK}}}}
STATE = {"revoked": False}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _go(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        good = self.headers.get("Authorization") == "Bearer tokA" and not STATE["revoked"]
        if good and self.path == "/logout":
            STATE["revoked"] = True
        self.send_response(200 if good else 401)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    do_GET = do_POST = _go


@pytest.fixture
def server():
    STATE["revoked"] = False
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_driver_runs_everything_and_runs_the_sign_out_last(tmp_path, server):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps(SPEC))
    out = tmp_path / "out"
    import os
    env = {**os.environ, "APITEST_BEARERAUTH_TOKEN": "tokA"}
    p = subprocess.run([sys.executable, str(SCRIPTS / "run_all.py"), str(spec), "--out", str(out), "--base-url", server],
                       capture_output=True, text=True, env=env, timeout=240)
    assert p.returncode in (0, 1), p.stdout[-800:] + p.stderr[-800:]
    data = json.loads((out / "report.json").read_text())
    assert data["totals"]["passed"] > 0
    cases = {c["name"]: c["status"] for c in data["cases"]}
    assert cases["test_smoke_GET_things"] == "passed"      # ran before the sign-out
    assert (out / "results/pass1.xml").exists() and (out / "results/pass2.xml").exists()
    assert "Report:" in p.stdout


def test_check_env_reports_missing_pieces_and_the_target(tmp_path):
    p = subprocess.run([sys.executable, str(SCRIPTS / "check_env.py"), "--needs", "k6", "--base-url", "https://api.example.com"],
                       capture_output=True, text=True, env={"PATH": str(tmp_path), "HOME": str(tmp_path)})
    assert "OK        python" in p.stdout
    assert "k6" in p.stdout and "not local" in p.stdout
    assert p.returncode == 1 and "MISSING   k6" in p.stdout
    q = subprocess.run([sys.executable, str(SCRIPTS / "check_env.py"), "--needs", "nothing"], capture_output=True, text=True)
    assert q.returncode == 2


def test_types_all_plus_fuzz(tmp_path):
    (tmp_path / "s.json").write_text(json.dumps(SPEC))
    doc = parse_openapi.build(str(tmp_path / "s.json"))
    result = gen_pytest.generate(doc, tmp_path / "a", ["all", "fuzz"])
    assert "fuzz" in result["counts"] and "security" in result["counts"]
    plain = gen_pytest.generate(doc, tmp_path / "b", ["all"])
    assert "fuzz" not in plain["counts"]
