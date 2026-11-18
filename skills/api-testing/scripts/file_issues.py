#!/usr/bin/env python3
"""File GitHub issues for the failures in a report. Opt-in, preview first, stdlib only.

Usage:
  python file_issues.py reports/report.json --repo OWNER/NAME                    # preview only, no network, no token
  python file_issues.py reports/report.json --repo OWNER/NAME --check-existing   # preview, also looks up issues already filed
  python file_issues.py reports/report.json --repo OWNER/NAME --create           # asks you to confirm, then files the issues
  options: --min-severity MEDIUM  --max-issues 20  --label api-test  --no-samples  --refile-closed  --yes  --api-url URL

What it does:
  * reads report.json (written by summarize_results.py) and turns each failing finding into an issue candidate;
  * groups findings that hit the same endpoint with the same kind of failure into one issue (the body lists every occurrence);
  * tags each issue with a hidden fingerprint, so a later run skips anything already filed (open, or closed unless --refile-closed);
  * scrubs request and response samples again before they go anywhere (same scrubber the report uses, plus the values of secret
    APITEST_* variables); --no-samples leaves the samples out and files only title, endpoint and the rerun command;
  * the token comes from APITEST_GITHUB_TOKEN (GITHUB_TOKEN is the fallback). It is never printed, logged or put in an argument.

Nothing is created unless you pass --create AND confirm: either type "yes" at the prompt or pass --yes (only do that when you
already read the preview). Without a terminal and without --yes it refuses. The token is only sent over https (or to a loopback
address, for testing). Issues in a public repository are public: the preview says so.

Exit codes: 0 done (or preview shown), 2 bad input, 3 not confirmed or no token, 4 some issues could not be created.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402

SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
API_URL = "https://api.github.com"
REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.:-]{0,49}$")
MARKER_RE = re.compile(r"<!-- apitest-fingerprint: ([0-9a-f]{16}) -->")
MAX_REPORT_BYTES = 30 * 1024 * 1024
MAX_TITLE = 200
MAX_BODY = 60_000          # GitHub rejects bodies over 65,536 characters
MAX_LIST_PAGES = 10        # up to 1,000 existing issues are checked for duplicates
PAUSE_SECONDS = 1.0        # between creates, to stay clear of GitHub's secondary rate limit
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class FilerError(Exception):
    pass


def secrets_in_env() -> list[str]:
    vals = _common.secret_values_from_env()
    for name in ("APITEST_GITHUB_TOKEN", "GITHUB_TOKEN"):
        v = os.environ.get(name, "")
        if len(v) >= 4:
            vals.append(v)
    return vals


def scrub(text: object, secrets: list[str], limit: int) -> str:
    """Redact first, then cut, so a half-cut secret never slips through."""
    s = _common.redact_text(str(text), secrets)
    s = s.replace("\x00", "")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def fence(text: str) -> str:
    """A code fence longer than any run of backticks inside the text."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    bar = "`" * max(3, longest + 1)
    return f"{bar}\n{text}\n{bar}"


# --------------------------------------------------------------------------- reading the report
def load_report(path: str) -> dict:
    p = Path(path)
    try:
        if not p.is_file():
            raise FilerError(f"{path}: not a file")
        if p.stat().st_size > MAX_REPORT_BYTES:
            raise FilerError(f"{path}: larger than {MAX_REPORT_BYTES // (1024 * 1024)} MB, refusing to read it")
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as e:
        raise FilerError(f"{path}: cannot read as JSON ({type(e).__name__})") from None
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise FilerError(f"{path}: not a report.json written by summarize_results.py (no findings list)")
    if data.get("schema_version") != 1:
        raise FilerError(f"{path}: unsupported schema_version {data.get('schema_version')!r} (expected 1)")
    return data


def _str(v: object) -> str:
    return v if isinstance(v, str) else ""


def _lines(v: object) -> list[str]:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def endpoint_key(endpoint: str) -> str:
    """'GET /pets/{id}  (getPet)' -> 'GET /pets/{id}'; the operation id is only a label."""
    return re.sub(r"\s+\([^)]*\)\s*$", "", endpoint).strip() or "(no endpoint)"


def finding_class(title: str) -> str:
    """The title with the parts that vary between occurrences folded away (array indexes, long ids)."""
    t = re.sub(r"\[\d+\]", "[]", title.lower())
    t = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<uuid>", t)
    t = re.sub(r"\b[0-9a-f]{16,}\b", "<hex>", t)
    t = re.sub(r"\s+", " ", t).strip()
    # the sensitive-field lists name a different field per occurrence, keep only what kind of failure it is
    return re.sub(r"\[('[^']*'(?:, '[^']*')*)\]", "[...]", t)


def fingerprint(endpoint: str, kind: str, title: str) -> str:
    raw = "\x1f".join((endpoint_key(endpoint), kind.lower(), finding_class(title)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_candidates(report: dict, min_severity: str, secrets: list[str], samples: bool) -> list[dict]:
    """One candidate per (endpoint, kind, failure class), most severe first."""
    floor = SEVERITIES.index(min_severity)
    groups: dict[str, dict] = {}
    for f in report["findings"]:
        if not isinstance(f, dict):
            continue
        sev = _str(f.get("severity")).upper()
        if sev not in SEVERITIES or SEVERITIES.index(sev) > floor:
            continue
        endpoint, kind, title = _str(f.get("endpoint")), _str(f.get("type")) or "other", _str(f.get("title"))
        fp = fingerprint(endpoint, kind, title)
        g = groups.setdefault(fp, {"fingerprint": fp, "severity": sev, "endpoint": endpoint_key(endpoint), "kind": kind,
                                   "owasp": _str(f.get("owasp")), "items": []})
        if SEVERITIES.index(sev) < SEVERITIES.index(g["severity"]):
            g["severity"] = sev
        g["items"].append(f)
    out = [render_candidate(g, secrets, samples) for g in groups.values()]
    out.sort(key=lambda c: (SEVERITIES.index(c["severity"]), c["endpoint"], c["title"]))
    return out


def render_candidate(g: dict, secrets: list[str], samples: bool) -> dict:
    first = g["items"][0]
    n = len(g["items"])
    title = scrub(f"[{g['severity']}] {g['endpoint']}: {_str(first.get('title'))}", secrets, MAX_TITLE)
    body = [f"**Severity:** {g['severity']}  \n**Endpoint:** `{scrub(g['endpoint'], secrets, 200).replace('`', '')}`  \n"
            f"**Test type:** {scrub(g['kind'], secrets, 40).replace('`', '')}"
            + (f"  \n**OWASP:** {scrub(g['owasp'], secrets, 20)}" if g["owasp"] else ""),
            "Filed from an API test report. The values below were scrubbed of credentials before filing."]
    if n > 1:
        body.append(f"This failure was seen {n} times in the run:")
        body.append(fence("\n".join(scrub(_str(i.get("title")), secrets, 300) for i in g["items"][:20])
                          + (f"\n... and {n - 20} more" if n > 20 else "")))
    if samples:
        detail = _lines(first.get("detail"))
        if detail:
            body.append("**What happened**\n\n" + fence("\n".join(scrub(x, secrets, 1500) for x in detail[:12])))
    repro = _lines(first.get("reproduce"))
    if not samples:
        repro = [x for x in repro if not x.lstrip().startswith("curl ")]
    if repro:
        body.append("**Reproduce**\n\n" + fence("\n".join(scrub(x, secrets, 600) for x in repro[:4])))
    note = _str(first.get("note"))
    if note:
        body.append("> " + scrub(note, secrets, 700).replace("\n", " "))
    body.append(f"<!-- apitest-fingerprint: {g['fingerprint']} -->")
    text = "\n\n".join(body)
    if len(text) > MAX_BODY:
        text = text[: MAX_BODY - 80] + "\n\n(cut to fit the GitHub limit)\n" + body[-1]
    return {"fingerprint": g["fingerprint"], "severity": g["severity"], "endpoint": g["endpoint"], "kind": g["kind"],
            "title": title, "body": text, "count": n}


# --------------------------------------------------------------------------- talking to GitHub
class GitHub:
    def __init__(self, api_url: str, token: str, repo: str):
        self.base, self.token, self.repo = api_url.rstrip("/"), token, repo

    def _call(self, method: str, path: str, payload: dict | None = None) -> tuple[object, dict]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "api-test-issue-filer",
            **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read(8_000_000) or b"null"), dict(r.headers)
        except urllib.error.HTTPError as e:
            msg = ""
            try:
                msg = _str(json.loads(e.read(20_000)).get("message"))
            except Exception:
                pass
            hint = {401: " (token rejected)", 403: " (no permission or rate limited)", 404: " (repo not found, or the token cannot see it)",
                    410: " (issues are disabled on this repo)", 422: " (GitHub refused the issue)"}.get(e.code, "")
            raise FilerError(f"GitHub answered {e.code}{hint}: {scrub(msg, [self.token], 200)}") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise FilerError(f"cannot reach {self.base}: {type(e).__name__}") from None

    def existing(self, label: str) -> dict[str, str]:
        """fingerprint -> 'open' | 'closed' for issues carrying the label."""
        found: dict[str, str] = {}
        for page in range(1, MAX_LIST_PAGES + 1):
            q = urllib.parse.urlencode({"state": "all", "labels": label, "per_page": 100, "page": page})
            items, _ = self._call("GET", f"/repos/{self.repo}/issues?{q}")
            if not isinstance(items, list):
                break
            for it in items:
                if not isinstance(it, dict) or "pull_request" in it:
                    continue
                m = MARKER_RE.search(_str(it.get("body")))
                if m:
                    # an open copy wins over a closed one
                    if found.get(m.group(1)) != "open":
                        found[m.group(1)] = "open" if it.get("state") == "open" else "closed"
            if len(items) < 100:
                break
        return found

    def create(self, title: str, body: str, labels: list[str]) -> str:
        res, _ = self._call("POST", f"/repos/{self.repo}/issues", {"title": title, "body": body, "labels": labels})
        url = _str(res.get("html_url")) if isinstance(res, dict) else ""
        return url or "(created)"


def check_api_url(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    if u.scheme == "https" and host and not u.username and not u.password:
        return url
    if u.scheme == "http" and host in LOOPBACK:
        return url
    raise FilerError("--api-url must be https (plain http is only accepted for a loopback address); the token is sent there")


# --------------------------------------------------------------------------- command line
def rerun_command(args: argparse.Namespace, create: bool) -> str:
    parts = ["python", "scripts/file_issues.py", args.report, "--repo", args.repo, "--min-severity", args.min_severity,
             "--max-issues", str(args.max_issues), "--label", args.label]
    for flag, on in (("--no-samples", args.no_samples), ("--refile-closed", args.refile_closed)):
        if on:
            parts.append(flag)
    parts.append("--create" if create else "--check-existing")
    return " ".join(shlex.quote(p) for p in parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="File GitHub issues for failed API tests (preview by default).")
    ap.add_argument("report", help="report.json written by summarize_results.py")
    ap.add_argument("--repo", required=True, help="OWNER/NAME of the repository that receives the issues")
    ap.add_argument("--min-severity", default="MEDIUM", type=str.upper, choices=SEVERITIES, help="least severe finding to file (default MEDIUM)")
    ap.add_argument("--max-issues", type=int, default=20, help="most issues one run may create (default 20)")
    ap.add_argument("--label", default="api-test", help="label put on every issue and used to find duplicates (default api-test)")
    ap.add_argument("--no-samples", action="store_true", help="leave request and response samples and the curl line out of the issues")
    ap.add_argument("--check-existing", action="store_true", help="in a preview, also look up issues already filed (needs the token and network)")
    ap.add_argument("--refile-closed", action="store_true", help="file again when the earlier issue was closed (default: skip it)")
    ap.add_argument("--create", action="store_true", help="really create the issues, after confirmation")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt; only after you read the preview")
    ap.add_argument("--api-url", default=API_URL, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        if not REPO_RE.match(args.repo):
            raise FilerError("--repo must look like OWNER/NAME")
        if not LABEL_RE.match(args.label):
            raise FilerError("--label: letters, digits, space, _ . : - only, up to 50 characters")
        if not 1 <= args.max_issues <= 100:
            raise FilerError("--max-issues must be between 1 and 100")
        api_url = check_api_url(args.api_url)
        report = load_report(args.report)
        secrets = secrets_in_env()
        cands = build_candidates(report, args.min_severity, secrets, not args.no_samples)
        token = os.environ.get("APITEST_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        if args.create and not token:
            print("No token. Export APITEST_GITHUB_TOKEN (a token that may create issues on the repo) and run again. Nothing was filed.",
                  file=sys.stderr)
            return 3
        existing: dict[str, str] = {}
        gh = GitHub(api_url, token, args.repo)
        looked_up = False
        if args.create or args.check_existing:
            if not token:
                raise FilerError("--check-existing needs APITEST_GITHUB_TOKEN")
            existing = gh.existing(args.label)
            looked_up = True
    except FilerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    todo, skipped = [], []
    for c in cands:
        state = existing.get(c["fingerprint"])
        if state == "open" or (state == "closed" and not args.refile_closed):
            skipped.append((c, state))
        else:
            todo.append(c)
    over = todo[args.max_issues:]
    todo = todo[:args.max_issues]

    total = report.get("findings_total")
    print(f"Report: {report.get('title', '')!s:.80} ({len(report['findings'])} findings, {len(cands)} issue candidates at {args.min_severity} or worse)")
    if isinstance(total, int) and total > len(report["findings"]):
        print(f"Note: the report lists {len(report['findings'])} of {total} findings; raise --max-findings in summarize_results.py to cover the rest.")
    print(f"Target: {args.repo}  (issues in a public repository are public; samples are scrubbed, use --no-samples to leave them out)")
    if not looked_up:
        print("Duplicates not checked (no network in a plain preview); use --check-existing to look up issues already filed.")
    for c, state in skipped:
        print(f"  skip   [{c['severity']}] {c['endpoint']} ({c['kind']}): already filed, {state}")
    for c in todo:
        print(f"  {'create' if args.create else 'would create'}  {c['title']}  ({c['count']} occurrence{'s' if c['count'] != 1 else ''})")
    if over:
        print(f"  {len(over)} more candidate(s) over --max-issues {args.max_issues}, not filed this run")
    if not todo:
        print("Nothing to file.")
        return 0
    if not args.create:
        print("Preview only, nothing was filed. First issue body:\n")
        print(todo[0]["body"])
        print(f"\nTo file these: {rerun_command(args, True)}")
        return 0

    if not args.yes:
        if not sys.stdin.isatty():
            print("Not confirmed: no terminal to ask on and no --yes. Nothing was filed.", file=sys.stderr)
            return 3
        answer = input(f"Create {len(todo)} issue(s) in {args.repo}? Type yes to continue: ")
        if answer.strip().lower() != "yes":
            print("Not confirmed. Nothing was filed.", file=sys.stderr)
            return 3

    failed = 0
    for i, c in enumerate(todo):
        if i:
            time.sleep(PAUSE_SECONDS)
        labels = [args.label, f"severity-{c['severity'].lower()}"]
        try:
            url = gh.create(c["title"], c["body"], labels)
            print(f"  created {url}")
        except FilerError as e:
            failed += 1
            print(f"  failed  {c['endpoint']}: {e}", file=sys.stderr)
            if "rate limited" in str(e) or "401" in str(e):
                print("Stopping: the next calls would fail the same way.", file=sys.stderr)
                break
    print(f"Done: {len(todo) - failed} created, {failed} failed, {len(skipped)} skipped as already filed.")
    print(f"Rerun later (duplicates are skipped): {rerun_command(args, True)}")
    return 4 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
