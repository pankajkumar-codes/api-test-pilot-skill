"""Follow-ups from the multi-API eval: request throttle, fewer false positives, one line for the safety-gate skips."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time

import pytest

from conftest import FIXTURES
import gen_common
import gen_pytest
import parse_openapi
from mock_rest import LAST_LOG, running as rest_server
from suite_runner import REST_ENV

SPEC = {
    "openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
    "paths": {
        "/items/{itemId}": {"get": {"parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                                    "responses": {"200": {"description": "ok"}, "404": {"description": "nope"}}}},
        "/redirect/{n}": {"get": {"parameters": [{"name": "n", "in": "path", "required": True, "schema": {"type": "integer"}}],
                                  "responses": {"200": {"description": "ok"}}}},
        "/anything/{anything}": {"get": {"parameters": [{"name": "anything", "in": "path", "required": True, "schema": {"type": "string"}}],
                                         "responses": {"200": {"description": "ok"}}}},
        "/html": {"get": {"parameters": [
            {"name": "status", "in": "query", "schema": {"type": "integer"}},
            {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 5}},
            {"name": "mode", "in": "query", "schema": {"type": "string", "default": "fast"}}],
            "responses": {"200": {"description": "ok"}}}},
        "/go": {"get": {"parameters": [{"name": "url", "in": "query", "required": True, "schema": {"type": "string"}}],
                        "responses": {"200": {"description": "ok"}}}},
    },
}


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    d = tmp_path_factory.mktemp("fu")
    spec = d / "spec.json"
    spec.write_text(json.dumps(SPEC))
    out = d / "suite"
    gen_pytest.generate(parse_openapi.build(str(spec)), out, ["smoke", "positive", "negative", "edge"])
    return out


@pytest.fixture(scope="module")
def rt(generated):
    spec = importlib.util.spec_from_file_location("gen_apitest_runtime", generated / "apitest_runtime.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


def _src(generated, name):
    return (generated / name).read_text()


# ---------------------------------------------------------------- false positives
@pytest.mark.parametrize("name,expected", [
    ("id", True), ("itemId", True), ("user_id", True), ("userID", True), ("uuid", True), ("slug", True), ("username", True),
    ("n", False), ("value", False), ("anything", False), ("etag", False), ("qop", False), ("codes", False), ("valid", False), ("paid", False),
])
def test_id_like_path_params_are_recognised_by_name(name, expected):
    ep = {"responses": {"200": {}}, "parameters": []}
    assert gen_common.is_id_param({"name": name, "schema": {"type": "string"}}, ep) is expected


def test_a_documented_404_makes_a_single_path_param_a_lookup():
    ep = {"responses": {"404": {}}, "parameters": [{"name": "code", "in": "path"}]}
    assert gen_common.is_id_param({"name": "code", "in": "path"}, ep)


def test_unknown_id_test_only_for_resource_ids(generated):
    src = _src(generated, "test_negative.py")
    assert "_unknown_id_not_found" in src
    unknown = [ln for ln in src.splitlines() if "def test_" in ln and "unknown_id" in ln]
    assert len(unknown) == 1 and "items" in unknown[0], unknown


def test_optional_params_without_spec_values_are_not_invented(generated):
    src = _src(generated, "test_positive.py")
    assert "'status'" not in src and '"status"' not in src, "optional integer without example/range must be omitted"
    assert "'limit': 5" in src or '"limit": 5' in src, "a value backed by `minimum` is kept"
    assert "fast" in src, "a spec default is kept"


def test_required_guessed_param_is_flagged_in_endpoints_and_in_the_failure(generated, rt):
    assert "guessed=('url',)" in _src(generated, "endpoints.py")

    class Resp:
        status_code, text, elapsed = 400, "bad url", type("E", (), {"total_seconds": lambda self: 0.0})()
        headers: dict = {}
        request = type("R", (), {"method": "GET", "url": "http://x/go?url=sample-url", "headers": {}, "body": None})()

        def json(self):
            raise ValueError

    rt._LAST_GUESSED[:] = ["url"]
    with pytest.raises(pytest.fail.Exception) as exc:
        rt.fail(Resp(), "expected 200")
    assert "invented value" in str(exc.value) and "url" in str(exc.value)
    rt._LAST_GUESSED.clear()
    with pytest.raises(pytest.fail.Exception) as exc:
        rt.fail(Resp(), "expected 200")
    assert "invented value" not in str(exc.value)


# ---------------------------------------------------------------- transient connection errors
def test_unreachable_target_skips_instead_of_failing(rt):
    api = rt.Api("http://127.0.0.1:1", {}, timeout=1, retries=0)
    with pytest.raises(pytest.skip.Exception) as exc:
        api.get("/x")
    assert "target unreachable" in str(exc.value) and api.unreachable == 1


def test_safe_methods_are_retried_and_writes_are_not(rt, monkeypatch):
    import requests
    monkeypatch.setattr(rt.time, "sleep", lambda s: None)
    api = rt.Api("http://127.0.0.1:1", {}, retries=2)
    calls = []

    def boom(method, url, **kw):
        calls.append(method)
        raise requests.exceptions.ConnectionError("reset")
    monkeypatch.setattr(api.session, "request", boom)
    with pytest.raises(pytest.skip.Exception):
        api.get("/x")
    assert calls == ["GET"] * 3
    calls.clear()
    with pytest.raises(pytest.skip.Exception):
        api.raw_post("http://127.0.0.1:1/x")
    assert calls == ["POST"], "a lost reply to a write may mean it was applied: never retried"


def test_transient_raise_mode_and_tls_errors_are_not_swallowed(rt, monkeypatch):
    import requests
    api = rt.Api("http://127.0.0.1:1", {}, retries=0)
    with pytest.raises(requests.exceptions.ConnectionError):
        api._request("GET", "http://127.0.0.1:1/", transient="raise", timeout=1)
    monkeypatch.setattr(api.session, "request", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.SSLError("bad cert")))
    with pytest.raises(requests.exceptions.SSLError):
        api.get("/x")


# ---------------------------------------------------------------- throttle
def test_throttle_spaces_requests(rt):
    with rest_server(set()) as url:
        api = rt.Api(url, {}, delay_ms=150)
        t0 = time.monotonic()
        for _ in range(4):
            api.get("/pets")
        assert time.monotonic() - t0 >= 3 * 0.15 - 0.02


def test_rps_and_delay_combine_to_the_slower_limit(rt):
    assert rt.min_interval_seconds(delay_ms=0, rps=0) == 0
    assert rt.min_interval_seconds(delay_ms=100, rps=0) == pytest.approx(0.1)
    assert rt.min_interval_seconds(delay_ms=100, rps=2) == pytest.approx(0.5)
    assert rt.min_interval_seconds(delay_ms=900, rps=2) == pytest.approx(0.9)


def test_env_throttle_and_bad_values(rt, monkeypatch):
    monkeypatch.setenv("APITEST_RPS", "4")
    assert rt.min_interval_seconds() == pytest.approx(0.25)
    for bad in ("abc", "-5", "nan", "inf"):
        monkeypatch.setenv("APITEST_DELAY_MS", bad)
        with pytest.raises(pytest.UsageError):
            rt.min_interval_seconds()


def _run(suite, url, env, *args):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("APITEST_")}
    clean.update(env)
    clean["APITEST_BASE_URL"] = url
    return subprocess.run([sys.executable, "-m", "pytest", str(suite), "-p", "no:cacheprovider", "-q", *args],
                          env=clean, capture_output=True, text=True, timeout=300)


@pytest.fixture(scope="module")
def mock_suite(tmp_path_factory):
    out = tmp_path_factory.mktemp("fm") / "api_tests"
    gen_pytest.generate(parse_openapi.build(str(FIXTURES / "mockapi.yaml")), out, ["all"])
    return out


def test_cli_option_throttles_a_real_run(mock_suite):
    with rest_server(set()) as url:
        t0 = time.monotonic()
        proc = _run(mock_suite, url, REST_ENV, "-m", "smoke", "--apitest-delay-ms=200")
    n = len(LAST_LOG)
    assert n >= 3, proc.stdout[-500:]
    assert time.monotonic() - t0 >= (n - 1) * 0.2, (n, proc.stdout[-300:])


def test_cli_option_overrides_env_and_rejects_nonsense(mock_suite):
    with rest_server(set()) as url:
        proc = _run(mock_suite, url, {"APITEST_DELAY_MS": "0"}, "-m", "smoke", "--apitest-delay-ms=oops")
    assert proc.returncode != 0 and "APITEST_DELAY_MS" in proc.stdout + proc.stderr


# ---------------------------------------------------------------- one line for the gate
def test_gate_skips_collapse_to_one_line_and_the_gate_still_holds(mock_suite):
    with rest_server(set()) as url:
        proc = _run(mock_suite, url, {"APITEST_TARGET_ENV": "production", "APITEST_BEARERAUTH_TOKEN": "t"}, "-rs", "-m", "intrusive")
    out = proc.stdout
    gate_lines = [ln for ln in out.splitlines() if ln.startswith("SKIPPED") and "safety gate:" in ln]
    assert len(gate_lines) == 1, gate_lines
    assert "SKIPPED [" in gate_lines[0] and "conftest.py" in gate_lines[0]
    assert "safety gate:" in out and "intrusive test(s) not run" in out
    assert not [ln for ln in out.splitlines() if "PASSED" in ln] and len(LAST_LOG) == 0, "nothing intrusive may reach the target"
