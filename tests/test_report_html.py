"""HTML and JSON report output of summarize_results.py: structure, offline use, escaping, redaction."""
import json
import re

from conftest import ROOT
from test_reporting import junit, k6_doc, summarize

CANARY = "sk_live_CANARY0123456789abcdef"
XSS = '<script>alert(1)</script><img src=x onerror=alert(2)>'


def build(tmp_path, *extra, cases=None, name="report.html", env=None):
    cases = cases or [
        ("api_tests.test_smoke", "test_smoke_ok", "pass", "", ""),
        ("api_tests.test_security", "test_api1_bola_getPet", "fail", "x",
         "[HIGH][API1:2023 BOLA] other user's object returned\nGET /pets/2 -> 200\n"
         f"repro: curl -sS -i -X GET http://127.0.0.1:9/pets/2 -H 'Authorization: Bearer {CANARY}'"),
        ("api_tests.test_edge", "test_edge_skip", "skip", "needs an id", ""),
    ]
    out = tmp_path / name
    summarize("--junit", junit(tmp_path, cases), "--base-url", "http://127.0.0.1:9", "-o", out, "--date", "2026-11-14", *extra, env=env)
    return out


def test_default_output_is_html_with_json_next_to_it(tmp_path):
    out = build(tmp_path)
    text = out.read_text(encoding="utf-8")
    assert text.startswith("<!doctype html>") and "<title>Test report: API</title>" in text
    data = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["result"]["status"] == "FAIL" and data["totals"]["total"] == 3
    assert data["findings"][0]["severity"] == "HIGH" and data["by_type"]["security"]["failed"] == 1
    assert "BOLA" in text and "FAIL" in text and "Results by test type" in text


def test_page_is_self_contained(tmp_path):
    text = build(tmp_path, "--k6", _k6(tmp_path)).read_text(encoding="utf-8")
    assert "Content-Security-Policy" in text and "default-src 'none'" in text
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", text)
    assert "@import" not in text and "url(http" not in text
    assert "Load and performance (k6)" in text and "p95" in text


def _k6(tmp_path):
    p = tmp_path / "load-summary.json"
    p.write_text(json.dumps(k6_doc()), encoding="utf-8")
    return p


def test_secrets_are_redacted_in_html_and_json(tmp_path):
    out = build(tmp_path, env={"APITEST_BEARERAUTH_TOKEN": CANARY})
    for f in (out, out.with_suffix(".json")):
        assert CANARY not in f.read_text(encoding="utf-8")


def test_hostile_text_is_escaped(tmp_path):
    cases = [("api_tests.test_security", "test_" + XSS, "fail", "x", f"[HIGH] {XSS}\nGET /pets -> 200 {XSS}")]
    text = build(tmp_path, cases=cases).read_text(encoding="utf-8")
    assert "<script>alert" not in text and "<img src=x" not in text
    assert "&lt;script&gt;" in text


def test_pass_and_inconclusive_status(tmp_path):
    ok = build(tmp_path, cases=[("api_tests.test_smoke", "test_a", "pass", "", "")], name="ok.html")
    assert json.loads(ok.with_suffix(".json").read_text())["result"]["status"] == "PASS"
    none = build(tmp_path, cases=[("api_tests.test_smoke", "test_a", "skip", "why", "")], name="none.html")
    assert json.loads(none.with_suffix(".json").read_text())["result"]["status"] == "INCONCLUSIVE"


def test_markdown_still_available_and_no_json_flag(tmp_path):
    md = build(tmp_path, name="r.md")
    assert md.read_text(encoding="utf-8").startswith("# Test report") and not md.with_suffix(".json").exists()
    h = build(tmp_path, "--no-json", name="n.html")
    assert h.exists() and not h.with_suffix(".json").exists()
    proc = summarize("--junit", junit(tmp_path, [("a.test_x", "test_y", "pass", "", "")], name="q.xml"), "-o", "-")
    assert proc.stdout.startswith("# Test report")


def test_checked_in_samples_are_current_html():
    for rel in ("examples/sample-report.html", "examples/spring-boot-demo/report.html"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert text.startswith("<!doctype html>") and "Content-Security-Policy" in text
        json.loads((ROOT / rel).with_suffix(".json").read_text(encoding="utf-8"))


def test_each_test_type_lists_its_cases_with_status_and_reason(tmp_path):
    out = build(tmp_path)
    text = out.read_text(encoding="utf-8")
    data = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    by = {(c["type"], c["name"]): c for c in data["cases"]}
    assert by[("smoke", "test_smoke_ok")]["status"] == "passed"
    assert by[("edge", "test_edge_skip")]["reason"] == "needs an id"
    assert "BOLA" in by[("security", "test_api1_bola_getPet")]["reason"]
    for name in ("test_smoke_ok", "test_api1_bola_getPet", "test_edge_skip", "needs an id"):
        assert name in text
    assert '<details class="type has-bad">' in text and 'data-st="skipped"' in text


def test_k6_strings_are_scrubbed_in_html_and_json(tmp_path):
    doc = k6_doc()
    doc["metrics"][f"http_req_duration{{stage:{CANARY}}}"] = {"p(95)": 4.0, "thresholds": {f"p(95)<500 {CANARY}": True}}
    doc["metrics"][f"http_req_failed{{endpoint:{CANARY}}}"] = {"value": 0.0}
    p = tmp_path / f"{CANARY}-summary.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    out = build(tmp_path, "--k6", p, env={"APITEST_BEARERAUTH_TOKEN": CANARY})
    for f in (out, out.with_suffix(".json")):
        assert CANARY not in f.read_text(encoding="utf-8"), f.name


def test_html_cannot_overwrite_itself_with_json(tmp_path):
    out = tmp_path / "r.json"
    proc = summarize("--junit", junit(tmp_path, [("a.test_x", "test_y", "pass", "", "")]), "-o", out, "--format", "html", expect=2)
    assert "overwrite" in proc.stderr and not out.exists()


# ---- request and response bodies per test
import gen_pytest  # noqa: E402
import parse_openapi  # noqa: E402
from conftest import FIXTURES  # noqa: E402
from mock_rest import running as rest_server  # noqa: E402
from suite_runner import REST_ENV, run_suite  # noqa: E402


def test_generated_suite_records_exchanges_and_report_shows_them_redacted(tmp_path):
    doc = parse_openapi.build(str(FIXTURES / "mockapi.yaml"))
    suite = tmp_path / "api_tests"
    gen_pytest.generate(doc, suite, ["smoke", "positive", "negative"])
    intake = tmp_path / "intake.json"
    intake.write_text(json.dumps(doc), encoding="utf-8")
    junit_file = tmp_path / "j.xml"
    env = dict(REST_ENV, APITEST_BEARERAUTH_TOKEN=CANARY)
    with rest_server(set()) as url:
        run_suite(suite, url, env, junit=junit_file)
    xml = junit_file.read_text(encoding="utf-8")
    assert "apitest.http" in xml and CANARY not in xml
    out = tmp_path / "r.html"
    summarize("--junit", junit_file, "--intake", intake, "-o", out, "--date", "2026-11-14", env={"APITEST_BEARERAUTH_TOKEN": CANARY})
    html_text = out.read_text(encoding="utf-8")
    data = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    with_http = [c for c in data["cases"] if c["http"]["items"]]
    assert len(with_http) > 5
    first = with_http[0]["http"]["items"][0]
    assert first["method"] in ("GET", "POST", "PUT", "DELETE", "PATCH") and first["status"] and first["url"].startswith("http://127.0.0.1")
    assert any(x["response_body"] for c in with_http for x in c["http"]["items"])
    assert any(x["request_headers"].get("Authorization") == "<redacted>" for c in with_http for x in c["http"]["items"])
    assert "Request and response" in html_text and CANARY not in html_text and CANARY not in json.dumps(data)


def test_hostile_exchange_property_is_escaped_scrubbed_and_bounded(tmp_path):
    evil = {"total": 99, "items": [
        {"method": "GET", "url": f"http://127.0.0.1/x?token={CANARY}", "status": 500, "ms": 3,
         "request_headers": {"Authorization": f"Bearer {CANARY}"}, "request_body": XSS * 60,
         "response_headers": {"X": 5}, "response_body": f'{{"password": "{CANARY}"}} {XSS}'},
        "junk", {"method": "<script>", "status": "x"}, {}, {}]}
    from xml.sax.saxutils import quoteattr
    xml = ('<testsuites><testsuite name="p"><testcase classname="api_tests.test_edge" name="test_a" time="0.1">'
           f'<properties><property name="apitest.http" value={quoteattr(json.dumps(evil))}/></properties></testcase></testsuite></testsuites>')
    p = tmp_path / "e.xml"
    p.write_text(xml, encoding="utf-8")
    out = tmp_path / "e.html"
    summarize("--junit", p, "-o", out, env={"APITEST_BEARERAUTH_TOKEN": CANARY})
    text = out.read_text(encoding="utf-8")
    data = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    items = data["cases"][0]["http"]["items"]
    assert len(items) <= 3 and items[0]["status"] == 500 and items[1:] and items[1]["method"] == ""
    assert len(items[0]["request_body"]) <= 2000 and "script" in items[0]["request_body"]
    assert CANARY not in text and "<script>alert" not in text and "<img src=x" not in text
