"""Generator behaviour found by the evals, and the eval driver itself (mock mode, offline).

The driver (`evals/run_evals.py`) runs intake -> generate -> pytest -> report against local stand-ins for the eval-apis.md
targets. These tests keep the cheap, high-signal parts in CI: the clean variants stay clean, a few seeded flaws must be found,
and the generator fixes the evals led to stay in place.
"""
import importlib.util
import json
import re
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
    # VAmPI answers a wrong password with 200 and {"status": "fail"}: a refusal, so only the enumeration check may fire
    fail_a = '{"status": "fail", "message": "Password is not correct for the given username."}'
    fail_b = '{"status": "fail", "message": "Username does not exist"}'
    with pytest.raises(BaseException) as ei:
        rt.expect_same_login_failure(R(200, fail_a, "bob"), R(200, fail_b, "zz"), "bob")
    assert "enumeration" in str(ei.value) and "succeeded" not in str(ei.value)
    rt.expect_same_login_failure(R(200, '{"status": "fail", "message": "bad"}', "bob"), R(200, '{"status": "fail", "message": "bad"}', "zz"), "bob")
    with pytest.raises(BaseException) as ei:
        rt.expect_same_login_failure(R(200, '{"auth_token": "eyJ.x.y"}', "bob"), R(400, "x", "zz"), "bob")
    assert "succeeded" in str(ei.value)
    with pytest.raises(BaseException) as ei:  # a body that is not JSON gives no way to tell, so a 2xx still counts
        rt.expect_same_login_failure(R(200, "Logged in user session: 42", "bob"), R(400, "x", "zz"), "bob")
    assert "succeeded" in str(ei.value)
    # boolean SQL injection: the tautology finds a record that an unknown value does not
    class FakeApi:
        def __init__(self, status):
            self.status = status

        @staticmethod
        def missing_id(kind="string", fmt=None):
            return "does-not-exist-1"

        def send(self, ep, **kw):
            return R(self.status, "{}", "x")

    ep = types.SimpleNamespace(path_params={"username": {"type": "string"}})
    hit = R(200, '{"username": "name1"}', "x")
    hit.request.method = "GET"
    with pytest.raises(BaseException) as ei:
        rt.expect_injection_safe(hit, "path_username[3]", FakeApi(404), ep)
    assert "boolean SQL injection" in str(ei.value)
    rt.expect_injection_safe(hit, "path_username[3]", FakeApi(200), ep)   # the API answers 200 for anything: no signal
    rt.expect_injection_safe(hit, "path_username[0]", FakeApi(404), ep)   # traversal payloads are not tautologies
    # an injection 5xx says nothing when the endpoint's normal request fails too
    rt._VALID_INPUT_5XX.clear()
    crash = R(500, "{}", "x")
    ep_post = types.SimpleNamespace(method="POST", path="/pet", path_params={})
    with pytest.raises(pytest.skip.Exception):
        rt.expect_injection_safe(crash, "body_name[0]", FakeApi(500), ep_post)
    rt._VALID_INPUT_5XX.clear()
    with pytest.raises(BaseException) as ei:
        rt.expect_injection_safe(crash, "body_name[0]", FakeApi(200), ep_post)
    assert "server error" in str(ei.value)

    # BOLA on a write: HIGH only when the victim's object visibly changed
    class SeqApi:
        def __init__(self, *answers):
            self.answers = list(answers)

        def send(self, ep, **kw):
            status, text = self.answers.pop(0)
            r = R(status, text, "x")
            r._sent_body = {"email": "new@example.com"}
            return r

    write = types.SimpleNamespace(method="PUT", path="/users/{u}/email", body={"email": "new@example.com"})
    read = types.SimpleNamespace(method="GET", path="/users/{u}")
    rt.expect_victim_write_denied(SeqApi((200, '{"email": "v@x"}'), (204, ""), (200, '{"email": "v@x"}')), write, read)
    with pytest.raises(BaseException) as ei:
        rt.expect_victim_write_denied(SeqApi((200, '{"email": "v@x"}'), (204, ""), (200, '{"email": "new@example.com"}')), write, read)
    assert "[HIGH]" in str(ei.value) and "changed the victim's" in str(ei.value)
    with pytest.raises(BaseException) as ei:
        rt.expect_victim_write_denied(SeqApi((204, "")), write, None)
    assert "[MEDIUM]" in str(ei.value) and "check by hand" in str(ei.value)
    rt.expect_victim_write_denied(SeqApi((200, '{"email": "v@x"}'), (403, "no")), write, read)

    # a handled 400 that names the target type is not a leak; a Java exception or stack frame is
    assert not rt.DB_ERROR_RE.search("Input error: couldn't convert `1 OR 1=1` to type `class java.lang.Long`")
    assert rt.DB_ERROR_RE.search("java.lang.NumberFormatException: For input string")
    assert rt.DB_ERROR_RE.search("at com.example.Foo.bar(Foo.java:42)")


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


def test_real_mode_prints_counts_and_the_report_path(tmp_path, monkeypatch, capsys):
    # a finished real run used to print only "[petstore]", which looked like nothing had happened
    monkeypatch.setattr(ev, "OUT", tmp_path)
    for k in ("APITEST_TARGET_ENV", "APITEST_AUTHORIZED"):
        monkeypatch.delenv(k, raising=False)
    with petstore_mock.running(set()) as base:
        monkeypatch.setattr(sys, "argv", ["run_evals.py", "--target", "petstore", "--real", "--base-url", base])
        assert ev.main() == 0
    err = capsys.readouterr().err
    assert re.search(r"petstore/real: \d+ passed, \d+ failed, \d+ skipped of \d+ generated tests", err)
    assert "Result: " in err
    assert "report: " in err and "report.md" in err


def test_reset_style_endpoints_are_recognised():
    import gen_common as gc
    assert gc.resets_state({"method": "GET", "path": "/createdb", "id": "populate_db"})
    assert gc.resets_state({"method": "POST", "path": "/admin/reset-db", "id": "x"})
    assert gc.resets_state({"method": "GET", "path": "/init", "id": "x", "summary": "Creates and populates the database with dummy data"})
    for path in ("/users/reset-password", "/pets", "/database/stats", "/books/create"):
        assert not gc.resets_state({"method": "GET", "path": path, "id": "x", "summary": "Returns things"}), path
