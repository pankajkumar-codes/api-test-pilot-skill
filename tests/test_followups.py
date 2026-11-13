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

    resp = Resp()
    resp._guessed = ("url",)
    with pytest.raises(pytest.fail.Exception) as exc:
        rt.fail(resp, "expected 200")
    assert "invented value" in str(exc.value) and "url" in str(exc.value)
    with pytest.raises(pytest.fail.Exception) as exc:
        rt.fail(Resp(), "expected 200")
    assert "invented value" not in str(exc.value)


# ---------------------------------------------------------------- unreachable vs dropped
def test_refused_connection_skips_by_default_and_fails_when_asked(rt, monkeypatch):
    api = rt.Api("http://127.0.0.1:1", {}, timeout=1, retries=0)
    with pytest.raises(pytest.skip.Exception) as exc:
        api.get("/x")
    assert "target unreachable" in str(exc.value) and api.unreachable == 1
    monkeypatch.setenv("APITEST_UNREACHABLE", "fail")
    with pytest.raises(pytest.fail.Exception) as exc:
        api.get("/x")
    assert "target unreachable" in str(exc.value)
    rt.set_unreachable_mode("fail")  # what the generated conftest does for smoke tests
    monkeypatch.delenv("APITEST_UNREACHABLE")
    try:
        with pytest.raises(pytest.fail.Exception):
            api.get("/x")
    finally:
        rt.set_unreachable_mode(None)
    monkeypatch.setenv("APITEST_UNREACHABLE", "maybe")
    with pytest.raises(pytest.UsageError):
        rt.unreachable_mode()


def test_connect_failures_are_retried_on_safe_methods_only(rt, monkeypatch):
    monkeypatch.setattr(rt.time, "sleep", lambda s: None)
    api = rt.Api("http://127.0.0.1:1", {}, timeout=1, retries=2)
    calls = []
    real = api.session.request
    monkeypatch.setattr(api.session, "request", lambda m, u, **kw: (calls.append(m), real(m, u, **kw))[1])
    with pytest.raises(pytest.skip.Exception):
        api.get("/x")
    assert calls == ["GET"] * 3
    calls.clear()
    with pytest.raises(pytest.skip.Exception):
        api.raw_post("http://127.0.0.1:1/x")
    assert calls == ["POST"]


def test_classification_of_errors(rt):
    import requests
    from urllib3 import exceptions as u3
    refused = requests.exceptions.ConnectionError(u3.MaxRetryError(None, "/", u3.NewConnectionError(None, "refused")))
    dropped = requests.exceptions.ConnectionError(("Connection aborted.", ConnectionResetError(104, "reset")))
    dropped2 = requests.exceptions.ConnectionError(u3.ProtocolError("Connection aborted.", ConnectionResetError()))
    assert rt.is_connect_failure(refused)
    assert rt.is_connect_failure(requests.exceptions.ConnectTimeout())
    assert rt.is_connect_failure(requests.exceptions.ProxyError(u3.MaxRetryError(None, "/", u3.ProxyError("tunnel", OSError()))))
    assert not rt.is_connect_failure(dropped) and not rt.is_connect_failure(dropped2)
    assert not rt.is_connect_failure(requests.exceptions.ReadTimeout())
    assert not rt.is_connect_failure(requests.exceptions.ChunkedEncodingError())
    assert not rt.is_connect_failure(requests.exceptions.SSLError())


def _dropping_server():
    """Accepts a connection and closes it without answering (a server that crashes on the request)."""
    import socket
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            c.recv(65536)
            c.close()
    threading.Thread(target=loop, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.getsockname()[1]}"


def test_a_drop_after_connecting_fails_for_every_method_instead_of_skipping(rt):
    srv, url = _dropping_server()
    try:
        api = rt.Api(url, {}, timeout=2, retries=0)
        for call in (lambda: api.get("/x"), lambda: api.raw_post(url + "/x", data="{broken")):
            with pytest.raises(pytest.fail.Exception) as exc:
                call()
            assert "dropped, reset or hung" in str(exc.value) and "unreachable" not in str(exc.value)
        assert api.unreachable == 0
    finally:
        srv.close()


def test_transient_raise_mode_and_tls_errors_are_not_swallowed(rt, monkeypatch):
    import requests
    api = rt.Api("http://127.0.0.1:1", {}, retries=0)
    with pytest.raises(requests.exceptions.ConnectionError):
        api._request("GET", "http://127.0.0.1:1/", transient="raise", timeout=1)
    monkeypatch.setattr(api.session, "request", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.SSLError("bad cert")))
    with pytest.raises(requests.exceptions.SSLError):
        api.get("/x")


def test_dead_target_fails_the_smoke_tests_and_exits_nonzero(mock_suite):
    proc = _run(mock_suite, "http://127.0.0.1:1", {"APITEST_RETRIES": "0", "APITEST_BEARERAUTH_TOKEN": "t", **{
        k: v for k, v in REST_ENV.items()}}, "-m", "smoke", "-x")
    assert proc.returncode == 1 and "failed" in proc.stdout and "target unreachable" in proc.stdout, proc.stdout[-600:]


def test_a_server_that_drops_on_a_bad_body_fails_the_negative_test_not_skips_it(tmp_path):
    srv, url = _dropping_server()
    try:
        out = tmp_path / "s"
        gen_pytest.generate(parse_openapi.build(str(FIXTURES / "mockapi.yaml")), out, ["negative"])
        proc = _run(out, url, {**REST_ENV, "APITEST_RETRIES": "0"}, "-rs", "-k", "malformed")
    finally:
        srv.close()
    assert "failed" in proc.stdout and "dropped, reset or hung" in proc.stdout, proc.stdout[-800:]
    assert "target unreachable" not in proc.stdout


def test_rate_limit_probe_skips_when_a_throttle_is_set(rt):
    api = rt.Api("http://127.0.0.1:1", {}, delay_ms=10)
    with pytest.raises(pytest.skip.Exception) as exc:
        rt.skip_if_paced(api)
    assert "rate-limit probe" in str(exc.value)
    rt.skip_if_paced(rt.Api("http://127.0.0.1:1", {}))


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


def test_gate_skip_for_a_remote_target_without_authorization_is_one_line_too(mock_suite):
    proc = _run(mock_suite, "http://api.example.invalid:9", {"APITEST_TARGET_ENV": "staging"}, "-rs", "-m", "intrusive")
    gate_lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("SKIPPED") and "safety gate:" in ln]
    assert len(gate_lines) == 1 and "APITEST_AUTHORIZED" in gate_lines[0], proc.stdout[-600:]
    assert "failed" not in proc.stdout and "error" not in proc.stdout.lower().replace("errors", "")


# ---------------------------------------------------------------- sample values that real validators accept
@pytest.mark.parametrize("name,kind,expected", [
    ("email", "string", "apitest@example.com"), ("owner_email", "string", "apitest@example.com"),
    ("default_status", "integer", 200), ("status_code", "integer", 200), ("statusCode", "integer", 200),
    ("callback_url", "string", "https://example.com/apitest"), ("homepage", "string", "https://example.com/apitest"),
    ("port", "integer", 8080), ("name", "string", None), ("status_text", "string", None), ("page", "integer", None)])
def test_name_hints(name, kind, expected):
    assert gen_common.name_hint(name, kind) == expected


def test_sample_uses_name_hints_only_when_the_spec_gives_nothing_better():
    S = gen_common.sample
    assert S({"type": "integer"}, "default_status") == 200
    assert S({"type": "string"}, "email") == "apitest@example.com"
    assert S({"type": "integer", "minimum": 100, "maximum": 599}, "default_status") == 100   # a range wins
    assert S({"type": "integer", "default": 404}, "default_status") == 404
    assert S({"type": "string", "pattern": "^x+$"}, "email") == "sample-email"                # a pattern: do not override
    assert S({"type": "string"}, "label") == "sample-label"


def test_hinted_values_are_still_marked_as_guesses():
    assert gen_common.is_guessed({"name": "email", "schema": {"type": "string"}})


def _flow_spec(param="itemId", with_id_in_create=False):
    create = {"responses": {"200": {"description": "ok"}}, "requestBody": {"content": {"application/json": {"schema": {
        "type": "object", "required": ["n"], "properties": {"n": {"type": "string"}}}}}}}
    if with_id_in_create:
        create["responses"] = {"201": {"description": "ok", "content": {"application/json": {"schema": {
            "type": "object", "properties": {param: {"type": "string"}}}}}}}
    return {"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}], "paths": {
        "/things": {"post": create},
        "/things/{%s}" % param: {"get": {"parameters": [{"name": param, "in": "path", "required": True, "schema": {"type": "string"}}],
                                         "responses": {"200": {"description": "ok"}}}}}}


def _integration(tmp_path, spec):
    f = tmp_path / "s.json"
    f.write_text(json.dumps(spec))
    out = tmp_path / "o"
    res = gen_pytest.generate(parse_openapi.build(str(f)), out, ["integration"])
    return out, res["notes"]


def test_no_create_then_read_flow_for_a_value_the_server_echoes(tmp_path):
    out, notes = _integration(tmp_path, _flow_spec("anything"))
    assert not (out / "test_integration.py").exists() or "lifecycle" not in (out / "test_integration.py").read_text()
    assert any("echoes or computes" in n for n in notes)


def test_flow_kept_for_id_named_params_and_when_the_create_response_documents_the_param(tmp_path):
    (tmp_path / "a").mkdir()
    out, _ = _integration(tmp_path / "a", _flow_spec("itemId"))
    assert "test_flow_things_lifecycle" in (out / "test_integration.py").read_text()
    (tmp_path / "b").mkdir()
    out, _ = _integration(tmp_path / "b", _flow_spec("code", with_id_in_create=True))
    assert "test_flow_things_lifecycle" in (out / "test_integration.py").read_text()


def test_skip_message_says_when_the_auth_scheme_was_inferred(tmp_path):
    spec = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}], "paths": {
        "/bearer": {"get": {"parameters": [{"name": "Authorization", "in": "header", "schema": {"type": "string"}}],
                            "responses": {"200": {"description": "ok"}, "401": {"description": "no"}}}}}}
    f = tmp_path / "s.json"
    f.write_text(json.dumps(spec))
    out = tmp_path / "o"
    gen_pytest.generate(parse_openapi.build(str(f)), out, ["positive"])
    assert "'inferred': True" in (out / "conftest.py").read_text()
    env = {k: v for k, v in os.environ.items() if not k.startswith("APITEST_")}
    r = subprocess.run([sys.executable, "-m", "pytest", str(out), "-rs", "-q", "-p", "no:cacheprovider"], env={**env, "APITEST_BASE_URL": "http://127.0.0.1:1"},
                       capture_output=True, text=True, cwd=tmp_path)
    assert "credentials not configured: set APITEST_INFERRED_AUTHORIZATION_TOKEN" in r.stdout and "scheme is inferred" in r.stdout, r.stdout
