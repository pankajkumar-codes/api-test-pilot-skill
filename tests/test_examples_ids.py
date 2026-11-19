"""Example values (x-example, examples, patterns, APITEST_EXAMPLES_FILE) and ids (APITEST_IDS_FILE, the "set these" list)."""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import gen_common
import gen_pytest
import parse_openapi
from suite_runner import run_suite

SPEC = {"openapi": "3.1.0", "info": {"title": "e", "version": "1"}, "servers": [{"url": "http://127.0.0.1:1"}],
        "paths": {"/b/{business_id}/period": {"get": {
            "operationId": "getPeriod",
            "parameters": [{"name": "business_id", "in": "path", "required": True, "schema": {"type": "string"}},
                           {"name": "start", "in": "query", "required": True, "schema": {"type": "string"}},
                           {"name": "gstin", "in": "query", "required": True,
                            "schema": {"type": "string", "pattern": "^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]$"}},
                           {"name": "region", "in": "query", "required": True, "schema": {"type": "string", "x-example": "south"}}],
            "responses": {"200": {"description": "ok"}}}}}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        q = dict(p.split("=", 1) for p in self.path.split("?", 1)[1].split("&"))
        ok = (q.get("start") == "2024-04-01" and re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z]", q.get("gstin", ""))
              and q.get("region") == "south")
        data = b"{}"
        self.send_response(200 if ok else 422)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("ex")
    (d / "spec.json").write_text(json.dumps(SPEC))
    gen_pytest.generate(parse_openapi.build(str(d / "spec.json")), d / "suite", ["smoke"])
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield d, f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.mark.parametrize("pattern", ["^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]$", "^\\d{4}-\\d{2}-01$", "^(IN|US)-[a-z]+$",
                                     "^[A-Za-z0-9_.-]+\\.(pdf|png)$", "^\\+?[0-9]{10,13}$", "^[^@]+@[^@]+$"])
def test_pattern_generator_matches_its_pattern(pattern):
    s = gen_common.regex_sample(pattern)
    assert s is not None and re.search(pattern, s)


def test_pattern_generator_gives_up_instead_of_guessing():
    assert gen_common.regex_sample("^(?=.*[A-Z])(?=.*\\d).{8,}$") is None
    assert gen_common.regex_sample("([") is None


def test_vendor_and_list_examples_reach_the_intake():
    doc = parse_openapi.parse(SPEC)
    region = next(p for p in doc["endpoints"][0]["parameters"] if p["name"] == "region")
    assert region["schema"]["example"] == "south"
    assert gen_common.param_value(region) == "south"


def test_patterns_are_no_longer_treated_as_invented_values():
    doc = parse_openapi.parse(SPEC)
    ep = doc["endpoints"][0]
    assert gen_common.guessed_query(ep) == ["start"]


def test_invented_start_is_could_not_verify_until_an_examples_file_supplies_it(env, tmp_path):
    d, url = env
    base = {"APITEST_PATH_BUSINESS_ID": "b1"}
    res, _ = run_suite(d / "suite", url, base)
    status, msg = next(iter(v for k, v in res.items() if "smoke" in k))
    assert status == "skipped" and "could not verify" in msg and "invented value" in msg
    ex = tmp_path / "examples.json"
    ex.write_text(json.dumps({"getPeriod.start": "2024-04-01"}))
    res, _ = run_suite(d / "suite", url, {**base, "APITEST_EXAMPLES_FILE": str(ex)})
    assert next(iter(v for k, v in res.items() if "smoke" in k))[0] == "passed"


def test_ids_file_and_the_unskip_list(env, tmp_path):
    d, url = env
    res, proc = run_suite(d / "suite", url, {})
    assert next(iter(v for k, v in res.items() if "smoke" in k))[0] == "skipped"
    assert "1 tests need APITEST_PATH_BUSINESS_ID" in proc.stdout and "APITEST_IDS_FILE" in proc.stdout
    ids = tmp_path / "ids.json"
    ids.write_text(json.dumps({"path": {"business_id": "b1"}}))
    ex = tmp_path / "examples.json"
    ex.write_text(json.dumps({"*.start": "2024-04-01"}))
    res, proc = run_suite(d / "suite", url, {"APITEST_IDS_FILE": str(ids), "APITEST_EXAMPLES_FILE": str(ex)})
    assert next(iter(v for k, v in res.items() if "smoke" in k))[0] == "passed" and "tests need" not in proc.stdout


def test_a_broken_ids_file_stops_with_a_clear_message(env, tmp_path):
    d, url = env
    bad = tmp_path / "bad.json"
    bad.write_text("[1,2")
    _, proc = run_suite(d / "suite", url, {"APITEST_IDS_FILE": str(bad)})
    assert "APITEST_IDS_FILE must name a readable JSON file" in proc.stdout + proc.stderr


def test_report_says_which_fields_need_an_example(env, tmp_path):
    from test_reporting import summarize
    d, url = env
    run_suite(d / "suite", url, {"APITEST_PATH_BUSINESS_ID": "b1"}, junit=tmp_path / "j.xml")
    out = tmp_path / "r.md"
    summarize("--junit", tmp_path / "j.xml", "--base-url", url, "-o", out, "--format", "md", "--date", "2026-11-18")
    text = out.read_text(encoding="utf-8")
    assert "Give an example" in text and "start" in text
    data_out = tmp_path / "r.html"
    summarize("--junit", tmp_path / "j.xml", "--base-url", url, "-o", data_out, "--date", "2026-11-18")
    assert json.loads(data_out.with_suffix(".json").read_text())["needs_example"]


def test_report_lists_the_variables_to_set_and_what_they_unskip(env, tmp_path):
    from test_reporting import summarize
    d, url = env
    run_suite(d / "suite", url, {}, junit=tmp_path / "j.xml")
    md = tmp_path / "r.md"
    summarize("--junit", tmp_path / "j.xml", "--base-url", url, "-o", md, "--format", "md", "--date", "2026-11-18")
    text = md.read_text(encoding="utf-8")
    assert "Set these to run the skipped tests" in text and "`APITEST_PATH_BUSINESS_ID`" in text
    html = tmp_path / "r.html"
    summarize("--junit", tmp_path / "j.xml", "--base-url", url, "-o", html, "--date", "2026-11-18")
    unskip = json.loads(html.with_suffix(".json").read_text())["unskip"]
    assert unskip and unskip[0]["variable"] == "APITEST_PATH_BUSINESS_ID" and unskip[0]["tests"] >= 1
    assert "Set these to run the skipped tests" in html.read_text(encoding="utf-8")


def test_nested_counts_do_not_blow_up_the_pattern_generator():
    assert gen_common.regex_sample("((a{5000}){5000}){5000}") is None
    assert re.fullmatch("[0-9]{2}", gen_common.regex_sample("^[0-9]{2}$"))


def test_an_empty_id_variable_falls_through_to_the_ids_file_then_the_spec_default(env, monkeypatch, tmp_path):
    import sys
    d, _url = env
    sys.path.insert(0, str(d / "suite"))
    sys.modules.pop("apitest_runtime", None)
    try:
        import apitest_runtime as rt
        f = tmp_path / "ids.json"
        f.write_text(json.dumps({"path": {"thing_id": "from-file"}}))
        monkeypatch.setenv("APITEST_PATH_THING_ID", "")
        monkeypatch.setenv("APITEST_IDS_FILE", str(f))
        assert rt.Api.pid(object(), "thing_id", "dflt") == "from-file"
        monkeypatch.delenv("APITEST_IDS_FILE")
        assert rt.Api.pid(object(), "thing_id", "dflt") == "dflt"
    finally:
        sys.path.remove(str(d / "suite"))
        sys.modules.pop("apitest_runtime", None)
