# Filing GitHub issues for failures

Optional step 7 of the router. `scripts/file_issues.py` turns the findings in `reports/report.json` into GitHub issues. It never runs a test and never files anything unless the user confirms.

## Preview (default, offline)
```
python scripts/file_issues.py reports/report.json --repo OWNER/NAME
```
Prints which issues it would create and the first issue body. No token and no network are needed. Add `--check-existing` to also look up issues already filed (needs the token).

## Create
```
export APITEST_GITHUB_TOKEN=...          # the user sets this; a token that may create issues on the repo
python scripts/file_issues.py reports/report.json --repo OWNER/NAME --create
```
It shows the preview, then asks for `yes`. `--yes` skips the prompt; use it only after the user has read the preview and said go ahead. Without a terminal and without `--yes` it refuses. Ask the user before the first `--create`; never export the token yourself.

## How findings become issues
- Grouping: findings with the same endpoint (method and path), the same test type and the same kind of failure become one issue that lists every occurrence. Array indexes, ids and lists of field names are folded away when comparing titles.
- Severity: `--min-severity` (default MEDIUM) drops the rest. `--max-issues` (default 20, at most 100) caps one run; the rest are listed as not filed.
- Duplicates: each issue carries a hidden `apitest-fingerprint` comment. A later run skips a fingerprint that already has an open issue, or a closed one unless `--refile-closed`. Only issues with the `--label` label (default `api-test`) are looked at. A fingerprint comes from endpoint, type and failure class, so a rerun of the same report never files twice.
- Labels: `api-test` and `severity-<level>`. GitHub creates labels that do not exist when the token can push.
- Redaction: every title and sample goes through the report scrubber again, plus the values of secret `APITEST_*` variables and the token. `--no-samples` also leaves out the response sample and the curl line, which is the right choice for a public repository.
- Token: `APITEST_GITHUB_TOKEN`, else `GITHUB_TOKEN`. Sent only over https (loopback http is accepted for testing); a redirect to another host is refused. Never printed.
- Text from the report cannot forge a fingerprint: `<!--` and `-->` are neutralised, and the last marker in a body is the one that counts. Only the newest 1,000 labelled issues are checked; a warning is printed when that cap is hit.

## Exit codes
0 done or preview, 2 bad input, 3 not confirmed or no token, 4 some issues could not be created.

## Limits
Issues are created one at a time with a one second pause. A 401 or rate limit stops the run; rerun later and the issues already filed are skipped. It files issues only; it does not close, edit or comment on existing ones.
