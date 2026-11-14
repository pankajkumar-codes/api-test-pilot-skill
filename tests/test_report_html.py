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



# ---- redaction at capture (runtime) and in the raw JUnit file
import base64  # noqa: E402
import datetime  # noqa: E402
import importlib.util  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

import pytest  # noqa: E402
import requests  # noqa: E402


@pytest.fixture(scope="module")
def generated_suite(tmp_path_factory):
    doc = parse_openapi.build(str(FIXTURES / "mockapi.yaml"))
    suite = tmp_path_factory.mktemp("cap") / "api_tests"
    gen_pytest.generate(doc, suite, ["smoke"])
    return suite


def load_runtime(suite):
    spec = importlib.util.spec_from_file_location("apitest_runtime_under_test", suite / "apitest_runtime.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod      # dataclasses look their module up here
    spec.loader.exec_module(mod)
    return mod


def fake_exchange(method="POST", url="http://127.0.0.1:9/pets", resp_body=b"", resp_ctype="application/json", status=200, **kw):
    req = requests.Request(method, url, **kw).prepare()
    resp = requests.Response()
    resp.status_code = status
    resp._content = resp_body
    resp.headers["Content-Type"] = resp_ctype
    resp.request = req
    resp.elapsed = datetime.timedelta(milliseconds=7)
    return resp


def captured(rt, resp):
    rt.reset_exchanges()
    rt.record_exchange(resp)
    return rt.take_exchanges()


def test_capture_removes_secrets_in_every_body_shape(generated_suite, monkeypatch):
    monkeypatch.setenv("APITEST_BEARERAUTH_TOKEN", CANARY)
    monkeypatch.setenv("APITEST_TENANT_KEY", "tenantsecretvalue")
    rt = load_runtime(generated_suite)
    rt.AUTH_NAMES.add("x-tenant-key")
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    cases = {
        "form": fake_exchange(headers=form, data=f"username=bob&password=S3CRETPW&note={CANARY}"),
        "multipart": fake_exchange(files={"f": ("a.txt", f"file with {CANARY}".encode())}),
        "text echo": fake_exchange(resp_body=f"Your reset link: http://h/reset/{CANARY} ok".encode(), resp_ctype="text/plain"),
        "html echo": fake_exchange(resp_body=f"<input name=csrf value={CANARY}>".encode(), resp_ctype="text/html"),
        "json echo, plain key": fake_exchange(resp_body=json.dumps({"data": {"access": CANARY, "note": jwt}}).encode()),
        "custom header": fake_exchange(headers={"X-Tenant-Key": "tenantsecretvalue", "X-Other": f"Bearer {CANARY}abc"}),
        "header by value shape": fake_exchange(headers={"X-Whatever": "Bearer abcdef123456"}),
        "query": fake_exchange(url=f"http://127.0.0.1:9/pets?x=1&tenant={CANARY}"),
        "path (configured credential)": fake_exchange(url=f"http://127.0.0.1:9/reset/{CANARY}"),
        "base64": fake_exchange(headers={"X-Basic": base64.b64encode(CANARY.encode()).decode()}),
    }
    for name, resp in cases.items():
        out = captured(rt, resp)
        for secret in (CANARY, "tenantsecretvalue", "S3CRETPW", "abcdef123456", jwt, base64.b64encode(CANARY.encode()).decode()):
            assert secret not in out, f"{name}: {secret} leaked: {out[:300]}"


def test_capture_placeholders_and_documented_limit(generated_suite):
    rt = load_runtime(generated_suite)
    out = json.loads(captured(rt, fake_exchange(files={"f": ("a.bin", b"\x00\x01abc")}, resp_body=b"\x89PNG\r\n\x1a\n" + bytes(300),
                                                resp_ctype="image/png")))
    item = out["items"][0]
    assert item["request_body"].startswith("<body omitted: multipart/form-data")
    assert item["response_body"].startswith("<body omitted: image/png, ")
    assert "�" not in json.dumps(out)
    assert json.loads(captured(rt, fake_exchange(data=iter([b"a", b"b"]))))["items"][0]["request_body"] == "<body omitted: streamed body>"
    form = json.loads(captured(rt, fake_exchange(headers={"Content-Type": "application/x-www-form-urlencoded"}, data="a=1&password=x&b=2")))
    assert "a=1" in form["items"][0]["request_body"] and "password=x" not in form["items"][0]["request_body"]
    # documented limit: a one-off token in a path that is not a configured credential is not recognised
    assert "reset/oneoff-token-in-path" in captured(rt, fake_exchange(url="http://127.0.0.1:9/reset/oneoff-token-in-path"))


def test_clipping_does_not_cut_a_redaction_marker(generated_suite):
    rt = load_runtime(generated_suite)
    for pad in range(1470, 1495):
        body = json.dumps({"a": "x" * pad, "password": "hunter2hunter2"})
        out = json.loads(captured(rt, fake_exchange(resp_body=body.encode())))["items"][0]["response_body"]
        assert "<reda" not in out.replace("<redacted>", ""), pad


def test_raw_junit_xml_never_contains_the_canary(generated_suite, tmp_path):
    suite = tmp_path / "s"
    suite.mkdir()
    for name in ("conftest.py", "apitest_runtime.py"):
        (suite / name).write_text((generated_suite / name).read_text(encoding="utf-8"), encoding="utf-8")
    (suite / "test_echo.py").write_text('''
import json, datetime, requests
import apitest_runtime as rt

def _resp(url, ctype, body, **kw):
    req = requests.Request("POST", url, **kw).prepare()
    r = requests.Response(); r.status_code = 200; r._content = body; r.headers["Content-Type"] = ctype
    r.request = req; r.elapsed = datetime.timedelta(milliseconds=1)
    return r

def test_echo():
    c = "CANARY"
    rt.record_exchange(_resp("http://127.0.0.1:9/x/" + c, "text/plain", ("echo " + c).encode(),
                             headers={"X-Custom": c, "Content-Type": "application/x-www-form-urlencoded"}, data="password=" + c))
    rt.record_exchange(_resp("http://127.0.0.1:9/y", "application/json", json.dumps({"data": {"v": c}}).encode(), json={"v": c}))
'''.replace("CANARY", CANARY), encoding="utf-8")
    junit_file = tmp_path / "echo.xml"
    env = {k: v for k, v in os.environ.items() if not k.startswith("APITEST_")}
    env.update({"APITEST_BASE_URL": "http://127.0.0.1:9", "APITEST_BEARERAUTH_TOKEN": CANARY})
    proc = subprocess.run([sys.executable, "-m", "pytest", str(suite), "-p", "no:cacheprovider", "-q", f"--junitxml={junit_file}"],
                          env=env, capture_output=True, text=True, timeout=120)
    xml = junit_file.read_text(encoding="utf-8")
    assert "apitest.http" in xml, proc.stdout[-500:]
    assert CANARY not in xml


def test_exchange_volume_is_budgeted_failures_first():
    import report_html
    cases = []
    for i in range(6):
        item = {"method": "GET", "url": "http://x", "status": 200, "ms": 1, "request_headers": {}, "request_body": "", "response_headers": {},
                "response_body": "b" * 900}
        cases.append({"type": "t", "name": f"n{i}", "status": "failed" if i == 3 else "passed", "http": {"total": 1, "items": [item]}})
    keep = report_html.allocate_http(cases, 3000)
    assert 3 in keep and len(keep) < 6
