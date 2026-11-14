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
