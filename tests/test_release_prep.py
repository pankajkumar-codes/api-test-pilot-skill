"""Phase 6 follow-ups from Phase 5: remote target without APITEST_TARGET_ENV, destructive marker, skip warnings."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from conftest import FIXTURES
import gen_pytest
import parse_openapi
import summarize_results as sr
from mock_rest import running as rest_server
from suite_runner import run_suite


@pytest.fixture(scope="module")
def doc():
    return parse_openapi.build(str(FIXTURES / "mockapi.yaml"))


@pytest.fixture(scope="module")
def suite(tmp_path_factory, doc):
    out = tmp_path_factory.mktemp("rp") / "api_tests"
    gen_pytest.generate(doc, out, ["all"])
    return out


def _run(suite, base_url, env=None, *extra):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("APITEST_")}
    clean.update(env or {})
    clean["APITEST_BASE_URL"] = base_url
    return subprocess.run([sys.executable, "-m", "pytest", str(suite), "-p", "no:cacheprovider", "-q", *extra],
                          env=clean, capture_output=True, text=True, timeout=120)


def test_remote_url_without_target_env_stops_the_run_with_a_clear_nonzero_exit(suite):
    proc = _run(suite, "http://api.example.invalid:9/v1")
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0 and proc.returncode == 4, out[-800:]  # pytest usage error
    assert "APITEST_TARGET_ENV is not set" in out and "api.example.invalid" in out
    assert "passed" not in out and "skipped" not in out, "no test may run (or silently skip) before the stop"


def test_remote_url_with_target_env_is_not_stopped_by_the_preflight(suite):
    # an unroutable name: tests fail to connect, which is fine; the point is that the preflight lets the run start
    proc = _run(suite, "http://api.example.invalid:9/v1", {"APITEST_TARGET_ENV": "staging", "APITEST_TIMEOUT": "1"}, "-x", "--co")
    assert proc.returncode == 0, proc.stdout[-500:] + proc.stderr[-500:]


@pytest.mark.parametrize("url,env,expect", [
    ("http://127.0.0.1:8080", {}, None),
    ("http://localhost:3000/api", {}, None),
    ("https://api.example.com", {"APITEST_TARGET_ENV": "staging"}, None),
    ("https://api.example.com", {}, "APITEST_TARGET_ENV"),
    ("https://api.example.com", {"APITEST_TARGET_ENV": "  "}, "APITEST_TARGET_ENV"),
    ("", {}, "APITEST_BASE_URL"),
    ("ftp://x", {}, "plain http"),
])
def test_preflight_cases(suite, monkeypatch, url, env, expect):
    rt = _runtime(suite)
    for k in ("APITEST_TARGET_ENV", "APITEST_AUTHORIZED"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    msg = rt.preflight(url)
    assert (msg is None) if expect is None else (expect in msg)


def _runtime(suite):
    import importlib.util
    spec = importlib.util.spec_from_file_location("apitest_runtime_t", suite / "apitest_runtime.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m  # dataclasses look the module up by name
    spec.loader.exec_module(m)
    return m


def test_destructive_tests_carry_the_marker_and_the_ordering_does_not_use_getsource(suite):
    conf = (suite / "conftest.py").read_text()
    assert "getsource" not in conf and 'get_closest_marker("destructive")' in conf and "xdist" in conf
    sec = (suite / "test_security.py").read_text()
    assert "@pytest.mark.destructive\n" in sec
    # every test that calls require_destructive is marked, and nothing else is
    for path in suite.glob("test_*.py"):
        for block in path.read_text().split("\n\n\n"):
            if "def test_" in block:
                assert ("require_destructive()" in block) == ("@pytest.mark.destructive" in block), block[:200]


def test_destructive_tests_run_last_in_a_real_collection(suite):
    proc = _run(suite, "http://127.0.0.1:1", {}, "--co")
    names = [ln.split("::")[-1] for ln in proc.stdout.splitlines() if "::" in ln]
    marked = subprocess.run([sys.executable, "-m", "pytest", str(suite), "-p", "no:cacheprovider", "-q", "--co", "-m", "destructive"],
                            env={**os.environ, "APITEST_BASE_URL": "http://127.0.0.1:1"}, capture_output=True, text=True)
    dnames = {ln.split("::")[-1] for ln in marked.stdout.splitlines() if "::" in ln}
    assert dnames, "the mock spec has destructive tests"
    first = min(i for i, n in enumerate(names) if n in dnames)
    assert all(n in dnames for n in names[first:]), "no non-destructive test after the first destructive one"


def test_run_where_most_tests_skip_prints_a_warning(suite):
    with rest_server(set()) as url:  # no credentials, ids or gate variables: nearly everything skips
        _, proc = run_suite(suite, url, {}, ["-rs"])
    assert "WARNING: most tests were skipped" in proc.stdout, proc.stdout[-800:]


def test_report_warns_when_over_half_of_the_tests_were_skipped(tmp_path):
    def case(i, status):
        inner = "" if status == "passed" else f'<{status} message="why"/>'
        return f'<testcase classname="api_tests.test_functional" name="test_{i}" time="0.1">{inner}</testcase>'
    xml = ('<testsuites><testsuite name="x" tests="4">' + case(1, "passed") + "".join(case(i, "skipped") for i in (2, 3, 4)) +
           "</testsuite></testsuites>")
    p = tmp_path / "r.xml"
    p.write_text(xml)
    out = tmp_path / "report.md"
    subprocess.run([sys.executable, "-I", str(sr.__file__), "--junit", str(p), "-o", str(out), "--date", "2026-11-12"], check=True,
                   capture_output=True, text=True)
    text = out.read_text()
    assert "Warning: 3 of 4 tests were skipped" in text
    # and no warning when most tests ran
    xml2 = xml.replace('<skipped message="why"/>', "")
    p.write_text(xml2)
    subprocess.run([sys.executable, "-I", str(sr.__file__), "--junit", str(p), "-o", str(out), "--date", "2026-11-12"], check=True,
                   capture_output=True, text=True)
    assert "were skipped (over half)" not in out.read_text()
