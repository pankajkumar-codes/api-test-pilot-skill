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


ROLE_SKIP = 'no role matrix: set APITEST_ROLE_MATRIX to a JSON file ({"createFiling": {"allow": ["owner"]}}) and APITEST_TOKEN_<ROLE> per role'


def test_role_matrix_gap_is_stated_when_it_did_not_run(tmp_path):
    cases = [("api_t.test_security", "test_api5_role_matrix[--deny]", "skip", ROLE_SKIP, ""), ("api_t.test_smoke", "test_ok", "pass", "", "")]
    out, data = build(tmp_path, cases)
    assert data["role_matrix_run"] is False and "APITEST_TOKEN_<ROLE>" in data["role_matrix_note"]
    assert "Role (BFLA) matrix not run" in out.read_text(encoding="utf-8")
    md = tmp_path / "r.md"
    summarize("--junit", junit(tmp_path, cases), "--base-url", "http://127.0.0.1:9", "-o", md, "--format", "md", "--date", "2026-11-18")
    assert "Role (BFLA) matrix not run" in md.read_text(encoding="utf-8")


def test_no_role_matrix_line_when_it_ran(tmp_path):
    cases = [("api_t.test_security", "test_api5_role_matrix[x-owner-allow]", "pass", "", "")]
    out, data = build(tmp_path, cases)
    assert data["role_matrix_run"] is True and "Role (BFLA) matrix" not in out.read_text(encoding="utf-8")


def test_unrecognised_extension_and_k6_dir_hints(tmp_path):
    k6 = tmp_path / "res.json"
    k6.write_text(json.dumps({"metrics": {"http_reqs": {"count": 10, "rate": 5}, "http_req_failed": {"value": 0}}}))
    plain_out = tmp_path / "report"
    err = summarize("--junit", junit(tmp_path, [("api_t.test_smoke", "test_ok", "pass", "", "")]), "--k6", k6, "-o", plain_out,
                    "--date", "2026-11-18").stderr
    assert "written as Markdown" in err and "--format html" in err
    assert "no manifest.json in --k6-dir" in err


def test_skips_are_grouped_by_who_can_fix_them(tmp_path):
    skip = lambda name, msg: ("api_t.test_x", name, "skip", msg, "")
    cases = [skip("a1", "create refused as a duplicate (409): the baseline body already exists on the target; use a fresh target"),
             skip("a2", "create refused as a duplicate (409): the baseline body already exists on the target; use a fresh target"),
             skip("b1", "no existing id for 'x': set APITEST_VICTIM_X (or run the flows)"),
             skip("b2", "credentials not configured: set APITEST_BEARERAUTH_TOKEN"),
             skip("c1", "destructive call: set APITEST_ALLOW_DESTRUCTIVE=yes to run it against data you can lose"),
             skip("c2", "tenant matrix covers reads only: set APITEST_TENANT_WRITES=yes to also call write operations in the other tenant"),
             skip("d1", "throttled (429): the call was rate limited, so this check has no verdict; slow the run down"),
             ("api_t.test_smoke", "ok", "pass", "", "")]
    out, data = build(tmp_path, cases)
    groups = {g["key"]: g for g in data["skip_groups"]}
    assert [g["key"] for g in data["skip_groups"]] == ["skill", "you", "safety", "target"]
    assert groups["skill"]["count"] == 2 and groups["you"]["count"] == 2 and groups["safety"]["count"] == 2 and groups["target"]["count"] == 1
    assert data["skips"]   # the flat list stays in report.json
    html = out.read_text(encoding="utf-8")
    assert "The skill could have done this itself: 2" in html and "You need to supply something: 2" in html
    md = tmp_path / "r.md"
    summarize("--junit", junit(tmp_path, cases), "--base-url", "http://127.0.0.1:9", "-o", md, "--format", "md", "--date", "2026-11-18")
    text = md.read_text(encoding="utf-8")
    assert "Skipped tests, by who can fix it:" in text and "- A safety default is off on purpose: 2" in text
