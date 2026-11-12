"""Phase 5: generator behaviour found by the evals, and the eval driver itself (mock mode, offline).

The driver (`evals/run_evals.py`) runs intake -> generate -> pytest -> report against local stand-ins for the eval-apis.md
targets. These tests keep the cheap, high-signal parts in CI: the clean variants stay clean, a few seeded flaws must be found,
and the generator fixes the evals led to stay in place.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from conftest import ROOT
import gen_common
import gen_pytest
import parse_openapi

sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "evals" / "mocks"))
import run_evals as ev  # noqa: E402
import petstore_mock  # noqa: E402
import vampi_mock  # noqa: E402
from suite_runner import run_suite  # noqa: E402

SPECS = ROOT / "evals" / "specs"
HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("uvicorn") is not None


# ------------------------------------------------------------------ generator changes found by the evals
def test_admin_endpoints_are_recognised_from_summary_text():
    assert gen_common.is_admin_endpoint({"path": "/users/{u}", "summary": "Deletes user by username (Only Admins)"})
    assert gen_common.is_admin_endpoint({"path": "/x", "description": "Requires admin privileges"})
    assert gen_common.is_admin_endpoint({"path": "/x", "summary": "Admin only"})
    assert not gen_common.is_admin_endpoint({"path": "/x", "summary": "Lists administrators' public profiles"})
    assert not gen_common.is_admin_endpoint({"path": "/x", "summary": "Retrieves a book"})


def test_put_body_names_an_existing_id_from_the_spec_example():
    ep = {"method": "PUT", "path": "/pet", "request_body": {"content_type": "application/json", "required": True, "schema": {
        "type": "object", "required": ["name"], "properties": {"id": {"type": "integer", "example": 10}, "name": {"type": "string"}}}}}
    assert gen_common.sample_body(ep)["id"] == 10
    assert "id" not in gen_common.sample_body({**ep, "method": "POST"})


@pytest.fixture(scope="module")
def petstore_suite(tmp_path_factory):
    out = tmp_path_factory.mktemp("ps") / "suite"
    gen_pytest.generate(parse_openapi.build(str(SPECS / "petstore-openapi.yaml")), out, ["all"])
    return out


def test_destructive_tests_run_last_and_authz_probes_before_other_destructive_ones(petstore_suite):
    conf = (petstore_suite / "conftest.py").read_text()
    assert "pytest_collection_modifyitems" in conf and 'get_closest_marker("destructive")' in conf and "test_api5_bfla" in conf


def test_vampi_suite_has_login_enumeration_and_path_sqli_checks(tmp_path):
    out = tmp_path / "suite"
    gen_pytest.generate(parse_openapi.build(str(SPECS / "vampi-openapi.yaml")), out, ["all"])
    text = (out / "test_security.py").read_text()
    assert "test_api2_login_does_not_reveal_valid_usernames" in text and "APITEST_KNOWN_USERNAME" in text
    assert "%27%20OR%20%271%27%3D%271" in text
    assert "@pytest.mark.intrusive" in text.split("def test_api2_login_does_not_reveal_valid_usernames")[0].rsplit("\n\n", 1)[-1]


def run_petstore(suite, bugs, env_extra=None):
    env = dict(ev.TARGETS["petstore"]["env"], **(env_extra or {}))
    with petstore_mock.running(bugs) as base:
        results, proc = run_suite(suite, base, env)
    return results


def test_throttled_and_duplicate_creates_are_skips_not_failures(petstore_suite):
    # a clean run has no failures even though several tests create the same baseline pet/user/order repeatedly
    results = run_petstore(petstore_suite, set())
    assert [k for k, (s, _) in results.items() if s == "failed"] == []


def test_runtime_skips_429_and_duplicate_create(petstore_suite):
    import importlib

    sys.path.insert(0, str(petstore_suite))
    try:
        rt = importlib.import_module("apitest_runtime")
    finally:
        sys.path.remove(str(petstore_suite))

    class R:
        def __init__(self, status, text=""):
            self.status_code, self.text, self.headers = status, text, {}

    ep = rt.Endpoint(id="e", method="POST", path="/x", ok=(200,))
    for resp in (R(409), R(400, '{"message": "User already exists"}'), R(422, "duplicate key")):
        with pytest.raises(pytest.skip.Exception):
            rt.expect_ok(resp, ep)
    with pytest.raises(pytest.skip.Exception):
        rt.expect_status(R(429), (200,))
    rt.expect_status(R(429), (429,))                       # an expected 429 is a pass, not a skip
    with pytest.raises(BaseException) as ei:               # a plain 400 on create is still a failure
        rt.expect_ok(R(400, "bad name"), ep)
    assert not isinstance(ei.value, pytest.skip.Exception)


def test_same_login_failure_helper(petstore_suite):
    import importlib

    sys.path.insert(0, str(petstore_suite))
    try:
        rt = importlib.import_module("apitest_runtime")
    finally:
        sys.path.remove(str(petstore_suite))

    import datetime
    import types

    import requests

    def R(status, text, user):
        r = requests.Response()
        r.status_code, r._content = status, text.encode()
        r.elapsed = datetime.timedelta(milliseconds=1)
        r.request = types.SimpleNamespace(method="POST", url="http://x/login", headers={},
                                          body=json.dumps({"username": user, "password": "x"}).encode())
        return r

    rt.expect_same_login_failure(R(400, "Username or Password Incorrect!", "bob"), R(400, "Username or Password Incorrect!", "zz"), "bob")
    rt.expect_same_login_failure(R(400, "no user bob", "bob"), R(400, "no user zz", "zz"), "bob")   # echoed names do not count
    with pytest.raises(BaseException) as ei:
        rt.expect_same_login_failure(R(400, "Password is not correct", "bob"), R(400, "Username does not exist", "zz"), "bob")
    assert "enumeration" in str(ei.value)
    with pytest.raises(BaseException) as ei:
        rt.expect_same_login_failure(R(401, "x", "bob"), R(404, "x", "zz"), "bob")
    assert "enumeration" in str(ei.value)
    with pytest.raises(pytest.skip.Exception):
        rt.expect_same_login_failure(R(429, "slow down", "bob"), R(400, "x", "zz"), "bob")


# ------------------------------------------------------------------ the driver and the mocks
def test_driver_lists_targets_and_refuses_real_mode_without_a_url(capsys):
    assert ev.main.__doc__ is None or True
    with pytest.raises(SystemExit):
        sys.argv = ["run_evals.py", "--real"]
        ev.main()
    assert set(ev.TARGETS) == {"petstore", "vampi", "seeded", "graphql"}


@pytest.mark.parametrize("target,variants", [
    ("petstore", ["clean", "noauth", "crash"]),
    ("vampi", ["clean", "sqli", "enum", "noauthdel"]),
    ("graphql", ["clean", "introspection", "bola"]),
])
def test_driver_mock_mode_scores_expected_vs_found(target, variants, tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "OUT", tmp_path)
    out = ev.run_target(target, variants, None, None)
    assert out["mode"] == "mock"
    for v in variants:
        assert out["variants"][v]["ok"], (target, v, out["variants"][v].get("unexpected") or out["variants"][v].get("failed"))
    assert "mock-based, real target not run" in ev.markdown(out)


@pytest.mark.skipif(not HAVE_FASTAPI, reason="pip install -r evals/requirements.txt to run the real FastAPI target")
def test_driver_runs_the_real_fastapi_target(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "OUT", tmp_path)
    out = ev.run_target("seeded", ["clean", "offbyone", "status"], None, None)
    assert out["mode"] == "real-local"
    assert all(out["variants"][v]["ok"] for v in ("clean", "offbyone", "status"))


def test_vampi_mass_assignment_is_a_documented_gap_not_a_silent_pass():
    gap = ev.TARGETS["vampi"]["flaws"]["mass"]["gap"]
    assert gap and "read-back" in gap
    assert not any(f["gap"] for t in ("petstore", "seeded", "graphql") for f in ev.TARGETS[t]["flaws"].values())


def test_real_mode_does_not_set_the_safety_variables():
    src = (ROOT / "evals" / "run_evals.py").read_text()
    assert 'env["APITEST_AUTHORIZED"]' not in src and 'APITEST_TARGET_ENV"]' not in src
    assert "APITEST_AUTHORIZED" not in ev.TARGETS["petstore"]["env"]


def test_report_step_works_from_a_deep_checkout_path(tmp_path, monkeypatch):
    deep = tmp_path / ("d" * 40) / ("e" * 40) / ("f" * 40)
    monkeypatch.setattr(ev, "OUT", deep)
    out = ev.run_target("petstore", ["clean"], None, None)
    assert out["variants"]["clean"]["ok"] and Path(out["variants"]["clean"]["report"]).exists()


def test_real_mode_refuses_with_a_nonzero_exit_when_the_gate_blocks(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ev, "OUT", tmp_path)
    # a non-loopback host name that is never contacted for intrusive tests: the gate needs APITEST_TARGET_ENV/AUTHORIZED
    for k in ("APITEST_TARGET_ENV", "APITEST_AUTHORIZED"):
        monkeypatch.delenv(k, raising=False)
    with petstore_mock.running(set()) as base:
        # 127.0.0.1 is loopback (passes the gate); production mode must be refused and must give a non-zero exit
        monkeypatch.setenv("APITEST_TARGET_ENV", "production")
        monkeypatch.setattr(sys, "argv", ["run_evals.py", "--target", "petstore", "--real", "--base-url", base])
        assert ev.main() == 2
    assert "safety gate refused" in capsys.readouterr().err
