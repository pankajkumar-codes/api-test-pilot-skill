"""Report: root-cause groups, "could not run" figure, the variables that would unskip tests, and the NOTES verdict."""
import json

from test_reporting import junit, summarize

MSG = "request without credentials must be rejected: expected [401, 403], got 404\nGET /b/does-not-exist-1/x -> 404"


def build(tmp_path, cases, *extra):
    out = tmp_path / "r.html"
    summarize("--junit", junit(tmp_path, cases), "--base-url", "http://127.0.0.1:9", "-o", out, "--date", "2026-11-18", *extra)
    return out, json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))


def test_repeated_findings_are_grouped_by_cause(tmp_path):
    cases = [("api_t.test_negative", f"test_op{i}_rejects_missing_credentials", "fail", "x", f"[MEDIUM] {MSG}") for i in range(5)]
    cases += [("api_t.test_security", "test_api2_x", "fail", "x", "[HIGH] other hole\nGET /a -> 200")]
    out, data = build(tmp_path, cases)
    groups = data["cause_groups"]
    assert groups[0]["severity"] == "HIGH" and groups[0]["count"] == 1
    assert any(g["count"] == 5 for g in groups) and len(groups) == 2
    assert "FAIL" == data["result"]["status"] and "2 cause group(s)" in data["result"]["summary"]
    assert "Root causes" in out.read_text(encoding="utf-8")
    md = tmp_path / "r.md"
    summarize("--junit", junit(tmp_path, cases), "--base-url", "http://127.0.0.1:9", "-o", md, "--format", "md", "--date", "2026-11-18")
    assert "### Root causes" in md.read_text(encoding="utf-8")


def test_could_not_run_and_the_variables_to_set(tmp_path):
    cases = [("api_t.test_security", f"test_bola_{i}", "skip", "no existing id for 'x': set APITEST_VICTIM_X (or run the flows)", "")
             for i in range(4)]
    cases += [("api_t.test_smoke", "test_ok", "pass", "", ""),
              ("api_t.test_smoke", "test_cred", "skip", "credentials not configured: set APITEST_BEARERAUTH_TOKEN", "")]
    _, data = build(tmp_path, cases)
    assert "5 could not run" in data["result"]["summary"]
    assert "APITEST_VICTIM_X (4)" in data["result"]["summary"]
    assert {"variable": "APITEST_VICTIM_X", "tests": 4} in data["unskip"]


def test_only_info_findings_give_a_notes_verdict(tmp_path):
    cases = [("api_t.test_smoke", "test_a", "fail", "x", "[INFO] needs an example: smoke: expected [200], got 422\nGET /p -> 422"),
             ("api_t.test_smoke", "test_b", "pass", "", "")]
    _, data = build(tmp_path, cases)
    assert data["result"]["status"] == "NOTES" and "INFO" in data["result"]["summary"]
