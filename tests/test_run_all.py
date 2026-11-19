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


def _cases(path):
    import xml.etree.ElementTree as ET
    return {c.get("name"): ("skipped" if c.find("skipped") is not None else "ran") for c in ET.parse(path).iter("testcase")}


def test_a_paced_run_on_a_local_target_runs_the_rate_limit_tests_in_a_burst_pass_of_their_own(tmp_path, server):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps(SPEC))
    out = tmp_path / "out"
    import os
    env = {**os.environ, "APITEST_BEARERAUTH_TOKEN": "tokA", "APITEST_RPS": "200"}
    env.pop("APITEST_EXPECT_RATE_LIMIT", None)
    p = subprocess.run([sys.executable, str(SCRIPTS / "run_all.py"), str(spec), "--out", str(out), "--base-url", server],
                       capture_output=True, text=True, env=env, timeout=240)
    assert p.returncode in (0, 1), p.stdout[-800:] + p.stderr[-800:]
    assert "Burst pass: the rate-limit tests run now without APITEST_RPS" in p.stdout
    first, burst = _cases(out / "results/pass1.xml"), _cases(out / "results/pass_burst.xml")
    assert not [n for n in first if "rate_limit" in n], "left out of the paced pass, so they are not counted twice"
    assert burst["test_rate_limit_behaviour"] == "ran", "no pacing in the burst pass, so the probe is not skipped"
    assert all("rate_limit" in n for n in burst)
    data = json.loads((out / "report.json").read_text())
    names = [c["name"] for c in data["cases"]]
    assert names.count("test_rate_limit_behaviour") == 1
    assert not [s for s in data.get("skips", []) if "spaces the requests" in json.dumps(s)]


def test_an_unpaced_run_has_no_burst_pass(tmp_path, server):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps(SPEC))
    out = tmp_path / "out"
    import os
    env = {k: v for k, v in os.environ.items() if k not in ("APITEST_RPS", "APITEST_DELAY_MS")}
    env["APITEST_BEARERAUTH_TOKEN"] = "tokA"
    p = subprocess.run([sys.executable, str(SCRIPTS / "run_all.py"), str(spec), "--out", str(out), "--base-url", server],
                       capture_output=True, text=True, env=env, timeout=240)
    assert p.returncode in (0, 1) and "Burst pass" not in p.stdout
    assert not (out / "results/pass_burst.xml").exists()
    assert "test_rate_limit_behaviour" in _cases(out / "results/pass1.xml")


def test_pacing_and_local_target_are_read_the_way_the_suite_and_the_gate_read_them(tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    import run_all
    assert run_all.is_paced({"APITEST_RPS": "5"}) and run_all.is_paced({"APITEST_DELAY_MS": "100"})
    assert not run_all.is_paced({}) and not run_all.is_paced({"APITEST_RPS": "0", "APITEST_DELAY_MS": ""})
    intake = tmp_path / "intake.json"
    intake.write_text(json.dumps({"servers": ["https://staging.example.com"]}))
    assert not run_all.target_is_local({}, intake), "a remote target keeps the user's pacing: no burst pass"
    assert run_all.target_is_local({"APITEST_BASE_URL": "http://localhost:8080"}, intake)
    assert not run_all.target_is_local({"APITEST_BASE_URL": "http://127.0.0.1.nip.io"}, intake)
    intake.write_text(json.dumps({"servers": ["http://127.0.0.1:9"]}))
    assert run_all.target_is_local({}, intake)
    assert not run_all.target_is_local({}, tmp_path / "missing.json")


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


# ---------------------------------------------------------------- --k6 and --chaos: one command for every test type
def _fake_k6(tmp_path):
    """A stand-in k6: answers `version`, writes a clean summary export, and leaves a marker file for each run."""
    path = tmp_path / "fakek6.sh"
    path.write_text('#!/bin/sh\nif [ "$1" = version ]; then echo "k6 v0.0.0 (fake)"; exit 0; fi\n'
                    'while [ $# -gt 0 ]; do [ "$1" = "--summary-export" ] && out="$2"; shift; done\n'
                    f'echo run >> "{tmp_path / "k6-runs.txt"}"\n'
                    'echo \'{"metrics": {"http_reqs": {"count": 10, "rate": 5}, "http_req_failed": {"value": 0}}}\' > "$out"\nexit 0\n')
    path.chmod(0o755)
    return path


def _run_all(tmp_path, *extra, env=None, base=None, timeout=300):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps(SPEC))
    import os
    full = {**os.environ, "APITEST_BEARERAUTH_TOKEN": "tokA", **(env or {})}
    cmd = [sys.executable, str(SCRIPTS / "run_all.py"), str(spec), "--out", str(tmp_path / "out")] + (["--base-url", base] if base else []) + list(extra)
    return subprocess.run(cmd, capture_output=True, text=True, env=full, timeout=timeout)


@pytest.mark.parametrize("extra,needle", [
    (["--k6=load,nope"], "--k6 takes a comma list"),
    (["--k6-docker"], "--k6-docker needs --k6"),
    (["--chaos-experiments", "payload"], "need --chaos"),
    (["--chaos", "--dependency-config", "deps.json"], "also needs --allow-hooks"),
    (["--chaos", "--allow-hooks"], "only applies together with --dependency-config"),
])
def test_k6_and_chaos_options_are_checked_before_anything_runs(tmp_path, extra, needle):
    p = _run_all(tmp_path, *extra)
    assert p.returncode == 2 and needle in p.stderr, p.stdout[-500:] + p.stderr[-500:]
    assert not (tmp_path / "out" / "suite").exists(), "nothing was generated or run"


def test_k6_that_cannot_start_stops_the_driver_before_any_test(tmp_path):
    p = _run_all(tmp_path, "--k6=load", env={"K6_BIN": str(tmp_path / "missing-k6")})
    assert p.returncode == 2 and "k6 cannot start here" in p.stderr
    assert not (tmp_path / "out" / "suite").exists()


def test_driver_runs_k6_and_chaos_after_the_tests_and_reports_them(tmp_path, server):
    fake = _fake_k6(tmp_path)
    p = _run_all(tmp_path, "--k6=load", "--chaos", "--chaos-experiments", "payload", env={"K6_BIN": str(fake)}, base=server)
    assert p.returncode in (0, 1), p.stdout[-1500:] + p.stderr[-1500:]
    out = tmp_path / "out"
    assert (tmp_path / "k6-runs.txt").read_text().count("run") == 1
    assert (out / "k6_tests" / "load.js").exists() and (out / "results" / "load-summary.json").exists()
    assert (out / "results" / "chaos" / "chaos.xml").exists()
    data = json.loads((out / "report.json").read_text())
    assert [k["profile"] for k in data["k6"]] == ["load"]
    assert any(c["type"] == "chaos" for c in data["cases"]), "the chaos results are in the report"
    assert "Next:" not in p.stdout, "nothing is left to run by hand"
    plain = subprocess.run([sys.executable, str(SCRIPTS / "run_all.py"), str(tmp_path / "s.json"), "--out", str(tmp_path / "plain"),
                            "--base-url", server], capture_output=True, text=True, timeout=300,
                           env={**__import__("os").environ, "APITEST_BEARERAUTH_TOKEN": "tokA"})
    assert "Next: k6 load profiles: add --k6" in plain.stdout and "chaos: add --chaos" in plain.stdout, "without the flags the hints stay"


def test_a_refused_gate_stops_k6_and_chaos_and_the_driver_never_sets_the_confirmation(tmp_path):
    fake = _fake_k6(tmp_path)
    # the environment is named but the person did not confirm authorization: the tests run (intrusive ones skip), k6 and chaos refuse
    env = {"K6_BIN": str(fake), "APITEST_UNREACHABLE": "skip", "APITEST_TIMEOUT": "1", "APITEST_TARGET_ENV": "staging", "APITEST_AUTHORIZED": ""}
    p = _run_all(tmp_path, "--k6=load,stress", "--chaos", "--chaos-experiments", "payload", env=env, base="http://staging.example.invalid")
    assert "k6: the safety gate refused this target" in p.stderr and "chaos: the safety gate refused this target" in p.stderr, p.stderr[-1200:]
    assert not (tmp_path / "k6-runs.txt").exists(), "k6 never started"
    assert "APITEST_AUTHORIZED=yes yourself" in p.stderr


def test_results_left_by_an_earlier_run_never_reach_a_report_when_k6_and_chaos_are_refused(tmp_path):
    fake = _fake_k6(tmp_path)
    old = tmp_path / "out" / "results"
    (old / "chaos").mkdir(parents=True)
    (old / "load-summary.json").write_text('{"metrics": {"http_reqs": {"count": 5, "rate": 1}, "http_req_failed": {"value": 0.9}}}')
    (old / "chaos" / "chaos.xml").write_text('<testsuite name="chaos"><testcase classname="chaos" name="old_run"><failure message="old"/></testcase></testsuite>')
    env = {"K6_BIN": str(fake), "APITEST_UNREACHABLE": "skip", "APITEST_TIMEOUT": "1", "APITEST_TARGET_ENV": "staging", "APITEST_AUTHORIZED": ""}
    p = _run_all(tmp_path, "--k6=load", "--chaos", "--chaos-experiments", "payload", env=env, base="http://staging.example.invalid")
    assert "k6: the safety gate refused" in p.stderr, p.stderr[-800:]
    data = json.loads((tmp_path / "out" / "report.json").read_text())
    assert data["k6"] == [], "the old load summary must not be reported"
    assert not any(c["type"] == "chaos" for c in data["cases"]), "the old chaos.xml must not be reported"
    assert not (old / "load-summary.json").exists() and not (old / "chaos" / "chaos.xml").exists()
