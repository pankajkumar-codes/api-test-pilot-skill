"""Fuzz: the run budget is shared between endpoints, hostile inputs go first, local targets run faster."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import gen_pytest
import parse_openapi
from suite_runner import run_suite

N = 30
BODY = {"required": True, "content": {"application/json": {"schema": {
    "type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}}}}
SPEC = {"openapi": "3.0.0", "info": {"title": "f", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
        "paths": {f"/r{i}": {"post": {"requestBody": BODY, "responses": {"201": {"description": "ok"}}}} for i in range(N)}}
SPEC["paths"]["/read"] = {"get": {"responses": {"200": {"description": "ok"}}}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        code = 500 if b"\\u0000" in raw else 201     # the server chokes on a NUL byte in any string
        self.send_response(code)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    d = tmp_path_factory.mktemp("fz")
    (d / "spec.json").write_text(json.dumps(SPEC))
    gen_pytest.generate(parse_openapi.build(str(d / "spec.json")), d / "suite", ["fuzz"])
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    res, proc = run_suite(d / "suite", f"http://127.0.0.1:{srv.server_port}",
                          {"APITEST_FUZZ_MAX_REQUESTS": "520"}, ["-p", "no:randomly"])
    srv.shutdown()
    return res, proc


def test_every_endpoint_gets_cases_and_the_nul_crash_is_found_on_all(run):
    res, _ = run
    posts = {k: v for k, v in res.items() if "r" in k.split("[")[-1] and "read" not in k}
    assert len(posts) == N
    assert all(s == "failed" and "server error 500" in m for s, m in posts.values()), \
        sorted({(s, m[:80]) for s, m in posts.values() if s != "failed"})
    assert not any("budget" in m for _, m in res.values())


@pytest.fixture()
def fz(tmp_path, monkeypatch):
    """The generated fuzz runtime, imported from a suite generated for a tiny spec."""
    import sys
    spec = {"openapi": "3.0.0", "info": {"title": "f", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
            "paths": {"/x": {"get": {"responses": {"200": {"description": "ok"}}}}}}
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    gen_pytest.generate(parse_openapi.build(str(tmp_path / "spec.json")), tmp_path / "suite", ["fuzz"])
    for k in [k for k in __import__("os").environ if k.startswith("APITEST_")]:
        monkeypatch.delenv(k)
    monkeypatch.syspath_prepend(str(tmp_path / "suite"))
    sys.modules.pop("fuzz_runtime", None)
    sys.modules.pop("apitest_runtime", None)
    import fuzz_runtime
    yield fuzz_runtime
    sys.modules.pop("fuzz_runtime", None)
    sys.modules.pop("apitest_runtime", None)


def test_default_rate_is_higher_for_a_loopback_target(fz):
    assert fz.config(local=True).rps == 50 and fz.config(local=False).rps == 5


def test_a_lower_case_count_is_honored(fz, monkeypatch):
    monkeypatch.setenv("APITEST_FUZZ_CASES", "5")
    assert fz.share_of_budget(fz.config(), 10).cases == 5
    monkeypatch.setenv("APITEST_FUZZ_CASES", "40")
    assert fz.share_of_budget(fz.config(), 10).cases == 40


def test_the_budget_is_split_and_a_small_one_warns_once(fz, monkeypatch):
    import warnings
    monkeypatch.setenv("APITEST_FUZZ_MAX_REQUESTS", "500")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert fz.share_of_budget(fz.config(), 100).cases == 5
        assert fz.share_of_budget(fz.config(), 100).cases == 5
    msgs = [str(w.message) for w in caught]
    assert len(msgs) == 1 and "APITEST_FUZZ_MAX_REQUESTS to 2000" in msgs[0]


def test_the_hostile_pass_fits_in_the_minimum_share_with_six_fields_and_three_query_parameters(fz):
    body = {f"f{i}": "v" for i in range(6)}
    spec: dict = {}
    cfg = fz.config()
    assert fz.MIN_SHARE >= 9 + len(fz.HOSTILE) - 1
    class E:
        query = {"a": "1", "b": "1", "c": "1"}
    cases = fz._hostile_cases(E(), spec, cfg, body, ["a", "b", "c"], "x" * 50)
    assert len(cases) <= fz.MIN_SHARE
    kinds = {c["label"].rsplit("=", 1)[1] for c in cases}
    assert kinds >= {name for name, _ in fz.HOSTILE}


def test_loopback_run_stays_fast(run):
    _, proc = run
    # 30 endpoints at the 5/s non-local default would take minutes; this loose bound is only a sanity check
    import re
    assert float(re.search(r"in ([\d.]+)s", proc.stdout).group(1)) < 90


def test_an_endpoint_that_keeps_hanging_is_not_sent_the_rest_of_its_cases(fz):
    import requests
    sent = []

    def send(case):
        sent.append(case["label"])
        raise requests.exceptions.ReadTimeout("read timed out")

    cases = [{"label": f"c{i}", "send": {}} for i in range(40)]
    with pytest.raises(pytest.fail.Exception) as exc:
        fz.run_cases("query hang", cases, send, fz.config(local=True))
    assert len(sent) == fz.MAX_TIMEOUTS_IN_A_ROW
    assert f"stopped after {fz.MAX_TIMEOUTS_IN_A_ROW} requests in a row timed out or lost the connection; 37 case(s) not sent" in str(exc.value)


def test_a_timeout_between_answers_does_not_stop_the_endpoint(fz):
    import requests
    sent = []

    import datetime

    def send(case):
        sent.append(case["label"])
        if len(sent) % 2:
            raise requests.exceptions.ReadTimeout("read timed out")
        r = requests.Response()
        r.status_code, r._content, r.elapsed = 400, b"{}", datetime.timedelta(milliseconds=3)
        r.request = requests.Request("POST", "http://127.0.0.1:1/graphql").prepare()
        return r

    cases = [{"label": f"c{i}", "send": {}} for i in range(8)]
    with pytest.raises(pytest.fail.Exception):
        fz.run_cases("query flaky", cases, send, fz.config(local=True))
    assert len(sent) == 8
