"""Regression tests for hostile, deeply nested input (found on Python 3.11, where the recursion limit bites sooner)."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import requests

import gen_pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "api-testing" / "scripts"
DEPTH = 100_000


def _runtime(tmp_path):
    (tmp_path / "apitest_runtime.py").write_text(gen_pytest.runtime_source(), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("hardening_rt", tmp_path / "apitest_runtime.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hardening_rt"] = mod
    spec.loader.exec_module(mod)
    return mod


def _response(body: bytes) -> requests.Response:
    req = requests.Request("POST", "http://127.0.0.1:9/x", data=body, headers={"Content-Type": "application/json"}).prepare()
    resp = requests.Response()
    resp.request, resp.status_code, resp._content = req, 200, body
    return resp


def test_repro_and_describe_survive_deeply_nested_json(tmp_path):
    rt = _runtime(tmp_path)
    nested = b"[" * DEPTH + b"]" * DEPTH
    resp = _response(nested)
    assert rt.curl_of(resp).startswith("curl ")  # request body nested past the recursion limit: printed as text, not parsed
    assert "-> 200" in rt.describe(resp)           # same for the response body
    assert rt.json_of(resp) is rt.UNSET            # and it is treated as "not JSON"


def test_intake_reports_over_nested_input_with_exit_code_2(tmp_path):
    schema = "null"
    for _ in range(3000):
        schema = '{"type":"object","properties":{"a":' + schema + "}}"
    spec = ('{"openapi":"3.0.0","info":{"title":"x","version":"1"},"paths":{"/a":{"post":{"requestBody":{"content":'
            '{"application/json":{"schema":' + schema + '}}},"responses":{"200":{"description":"ok"}}}}}}')
    f = tmp_path / "deep.json"
    f.write_text(spec)
    p = subprocess.run([sys.executable, str(SCRIPTS / "intake.py"), str(f), "--summary"], capture_output=True, text=True, timeout=120)
    assert p.returncode == 2 and "nested too deeply" in p.stderr, (p.returncode, p.stderr[-500:])
    assert "Traceback" not in p.stderr


def test_real_eval_run_against_unconfirmed_remote_target_exits_2_without_traceback(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("APITEST_")}
    p = subprocess.run([sys.executable, str(ROOT / "evals" / "run_evals.py"), "--target", "petstore", "--real",
                        "--base-url", "http://203.0.113.5:8080/api/v3"], capture_output=True, text=True, env=env, timeout=300)
    assert p.returncode == 2
    assert "Traceback" not in p.stderr
    assert "APITEST_TARGET_ENV" in p.stderr and "Nothing was sent to the target" in p.stderr


def test_generators_reject_non_intake_files_with_exit_code_2(tmp_path):
    bad = {"not_json.json": b"\x8d\xff\x00binary", "no_endpoints.json": b"{}", "list.json": b"[]"}
    for name, data in bad.items():
        (tmp_path / name).write_bytes(data)
    for script, extra in (("gen_pytest.py", ["-o", str(tmp_path / "o")]), ("gen_k6.py", ["-o", str(tmp_path / "k")]),
                          ("gen_chaos_plan.py", ["-o", str(tmp_path / "c.md"), "--env", "staging"])):
        for name in bad:
            p = subprocess.run([sys.executable, str(SCRIPTS / script), str(tmp_path / name), *extra], capture_output=True, text=True, timeout=60)
            assert p.returncode == 2 and "Traceback" not in p.stderr and p.stderr.startswith("error:"), (script, name, p.stderr[-300:])
    p = subprocess.run([sys.executable, str(SCRIPTS / "spec_diff.py"), str(tmp_path / "list.json"), str(tmp_path / "no_endpoints.json")],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 2 and "Traceback" not in p.stderr
    p = subprocess.run([sys.executable, str(SCRIPTS / "intake.py"), str(tmp_path / "not_json.json")], capture_output=True, text=True, timeout=60)
    assert p.returncode == 2 and "Traceback" not in p.stderr
