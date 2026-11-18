"""file_issues.py: grouping, redaction, duplicate skipping and the confirmation gate, against a local fake GitHub API."""
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import ROOT
import file_issues as fi

SCRIPT = ROOT / "skills" / "api-testing" / "scripts" / "file_issues.py"
SECRET = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def finding(sev="HIGH", title="smoke: expected [200], got 403", endpoint="GET /pets  (listPets)", kind="smoke", **kw):
    f = {"severity": sev, "title": title, "endpoint": endpoint, "type": kind, "rating": "tagged by the test", "owasp": "",
         "detail": ["GET http://127.0.0.1:8080/pets -> 403 (0 ms)", "body: {\"msg\": \"nope\"}"],
         "reproduce": ["curl -sS -i -X GET http://127.0.0.1:8080/pets", "python -m pytest api_tests/test_smoke.py -v"], "note": ""}
    f.update(kw)
    return f


def write_report(tmp_path, findings):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"schema_version": 1, "title": "t", "findings": findings, "findings_total": len(findings)}))
    return str(p)


class FakeGitHub:
    def __init__(self, existing=()):
        self.existing = list(existing)   # (state, body)
        self.created, self.auth, self.lists = [], [], 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                outer.auth.append(self.headers.get("Authorization"))
                outer.lists += 1
                items = [{"state": s, "body": b} for s, b in outer.existing]
                items.append({"state": "open", "body": outer.existing[0][1] if outer.existing else "", "pull_request": {}})
                self._send(200, items)

            def do_POST(self):
                outer.auth.append(self.headers.get("Authorization"))
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.created.append(payload)
                self._send(201, {"html_url": f"https://github.com/o/r/issues/{len(outer.created)}"})

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def gh():
    g = FakeGitHub()
    yield g
    g.close()


def run(report, *args, env=None, stdin=None):
    import os
    e = {k: v for k, v in os.environ.items() if not k.startswith(("APITEST_", "GITHUB_TOKEN"))}
    e.update(env or {})
    return subprocess.run([sys.executable, "-I", str(SCRIPT), report, *args], capture_output=True, text=True, env=e,
                          input=stdin, timeout=60)


def test_groups_by_endpoint_and_failure_class(tmp_path):
    rep = fi.load_report(write_report(tmp_path, [
        finding(title="[API3:2023] exposes sensitive fields: ['$[0].password_hash']"),
        finding(title="[API3:2023] exposes sensitive fields: ['$[3].password_hash', '$[4].password_hash']"),
        finding(endpoint="GET /users  (listUsers)", title="[API3:2023] exposes sensitive fields: ['$[0].password_hash']")]))
    cands = fi.build_candidates(rep, "LOW", [], True)
    assert len(cands) == 2
    assert sorted(c["count"] for c in cands) == [1, 2]


def test_min_severity_filters(tmp_path):
    rep = fi.load_report(write_report(tmp_path, [finding("LOW"), finding("HIGH", endpoint="GET /a")]))
    assert [c["severity"] for c in fi.build_candidates(rep, "MEDIUM", [], True)] == ["HIGH"]


def test_preview_needs_no_token_and_no_network(tmp_path):
    r = run(write_report(tmp_path, [finding()]), "--repo", "o/r")
    assert r.returncode == 0, r.stderr
    assert "would create" in r.stdout and "Preview only" in r.stdout and "Duplicates not checked" in r.stdout


def test_secrets_are_scrubbed_from_title_and_body(tmp_path):
    f = finding(title=f"leak token={SECRET}", detail=[f"Authorization: Bearer {SECRET}", "body: {\"password\": \"hunter2hunter2\"}"],
                reproduce=[f"curl -u admin:{SECRET} http://127.0.0.1/x"])
    r = run(write_report(tmp_path, [f]), "--repo", "o/r", env={"APITEST_MY_TOKEN": "zzz-secret-value-123"})
    assert r.returncode == 0
    for leaked in (SECRET, "hunter2hunter2"):
        assert leaked not in r.stdout + r.stderr
    f2 = finding(detail=["value zzz-secret-value-123 seen"])
    r = run(write_report(tmp_path, [f2]), "--repo", "o/r", env={"APITEST_MY_TOKEN": "zzz-secret-value-123"})
    assert "zzz-secret-value-123" not in r.stdout


def test_no_samples_drops_detail_and_curl(tmp_path):
    r = run(write_report(tmp_path, [finding()]), "--repo", "o/r", "--no-samples")
    assert "nope" not in r.stdout and "curl -sS" not in r.stdout and "pytest" in r.stdout


def test_create_without_token_refuses(tmp_path):
    r = run(write_report(tmp_path, [finding()]), "--repo", "o/r", "--create")
    assert r.returncode == 3 and "Nothing was filed" in r.stderr


def test_create_without_confirmation_refuses(tmp_path, gh):
    r = run(write_report(tmp_path, [finding()]), "--repo", "o/r", "--create", "--api-url", gh.url, env={"APITEST_GITHUB_TOKEN": SECRET})
    assert r.returncode == 3 and not gh.created


def test_create_with_yes_files_issue_and_hides_token(tmp_path, gh):
    r = run(write_report(tmp_path, [finding(), finding("MEDIUM", endpoint="GET /b")]), "--repo", "o/r", "--create", "--yes",
            "--api-url", gh.url, env={"APITEST_GITHUB_TOKEN": SECRET})
    assert r.returncode == 0, r.stderr
    assert len(gh.created) == 2
    assert gh.created[0]["labels"] == ["api-test", "severity-high"]
    assert fi.MARKER_RE.search(gh.created[0]["body"])
    assert SECRET not in r.stdout + r.stderr
    assert gh.auth[0] == f"Bearer {SECRET}"


def test_existing_issue_is_skipped_open_and_closed(tmp_path):
    rep = fi.load_report(write_report(tmp_path, [finding(), finding(endpoint="GET /b")]))
    a, b = fi.build_candidates(rep, "LOW", [], True)
    g = FakeGitHub(existing=[("open", f"x\n<!-- apitest-fingerprint: {a['fingerprint']} -->"),
                             ("closed", f"<!-- apitest-fingerprint: {b['fingerprint']} -->")])
    try:
        path = write_report(tmp_path, [finding(), finding(endpoint="GET /b")])
        env = {"APITEST_GITHUB_TOKEN": SECRET}
        r = run(path, "--repo", "o/r", "--create", "--yes", "--api-url", g.url, env=env)
        assert r.returncode == 0 and not g.created and r.stdout.count("already filed") == 2
        r = run(path, "--repo", "o/r", "--create", "--yes", "--refile-closed", "--api-url", g.url, env=env)
        assert len(g.created) == 1
    finally:
        g.close()


def test_max_issues_caps_the_run(tmp_path, gh):
    fs = [finding(endpoint=f"GET /p{i}") for i in range(5)]
    r = run(write_report(tmp_path, fs), "--repo", "o/r", "--create", "--yes", "--max-issues", "2", "--api-url", gh.url,
            env={"APITEST_GITHUB_TOKEN": SECRET})
    assert len(gh.created) == 2 and "3 more candidate" in r.stdout


@pytest.mark.parametrize("args", [["--repo", "not a repo"], ["--repo", "o/r", "--label", "bad;label"], ["--repo", "o/r", "--max-issues", "0"],
                                  ["--repo", "o/r", "--api-url", "http://example.com"], ["--repo", "o/r", "--api-url", "https://u:p@h.com"]])
def test_bad_arguments_exit_2(tmp_path, args):
    r = run(write_report(tmp_path, [finding()]), *args)
    assert r.returncode == 2


def test_bad_report_exits_2(tmp_path):
    p = tmp_path / "r.json"
    p.write_text("[1,2]")
    assert run(str(p), "--repo", "o/r").returncode == 2
    p.write_text("{" * 100000)
    assert run(str(p), "--repo", "o/r").returncode == 2


def test_fence_survives_backticks_in_samples():
    assert fi.fence("a ``` b").startswith("````")


def test_prompt_answer_other_than_yes_files_nothing(tmp_path, gh, monkeypatch, capsys):
    monkeypatch.setenv("APITEST_GITHUB_TOKEN", SECRET)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = fi.main([write_report(tmp_path, [finding()]), "--repo", "o/r", "--create", "--api-url", gh.url])
    assert rc == 3 and not gh.created
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    monkeypatch.setattr(fi, "PAUSE_SECONDS", 0)
    assert fi.main([write_report(tmp_path, [finding()]), "--repo", "o/r", "--create", "--api-url", gh.url]) == 0
    assert len(gh.created) == 1
