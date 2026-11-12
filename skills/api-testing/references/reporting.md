# Reporting

Step 6 of the router. One command turns the results of a run into `reports/report.md` (layout: `templates/report.md`).

## Produce the inputs
- pytest: `python -m pytest api_tests -p no:cacheprovider --junitxml=results/pytest.xml` (any generated suite; REST, GraphQL, fuzz)
- k6: `python scripts/run_k6.py k6_tests/load.js` writes `results/load-summary.json` (`--summary-export`); same for stress, spike, soak

## Build the report
```
python scripts/summarize_results.py --junit results/pytest.xml --k6 results/load-summary.json \
    --intake intake.json --base-url "$APITEST_BASE_URL" -o reports/report.md
```
`--junit` and `--k6` repeat. `--intake` names endpoints and lists endpoints that have no result. `--tests-dir` and `--k6-dir`
(defaults `api_tests`, `k6_tests`) are used to build the rerun commands. `-o -` prints to stdout. The script never runs a test,
never contacts the target and prints the command that regenerates the report.

## What the report contains
- Summary: result line (PASS / FAIL / INCOMPLETE) and counts per test type.
- Coverage per endpoint: types run, pass, fail, skipped, k6 p95. Without `--intake` only endpoints named in a failing request appear.
- k6: overall numbers, every threshold with its observed value, per-stage and per-endpoint tables.
- Findings, most severe first (CRITICAL, HIGH, MEDIUM, LOW, INFO), each with endpoint, what happened vs expected and **Reproduce**:
  the redacted `curl` line the runtime recorded (when there is one) plus the exact `python -m pytest <file>::<test>` command, or the
  `run_k6.py` command for k6 findings. Gated types (security, fuzz, resilience, k6) add the safety-gate note: the user exports
  `APITEST_TARGET_ENV` and `APITEST_AUTHORIZED=yes`; the report never sets them.
- Curl lines: `repro:` lines in a result file are untrusted. Only the exact shape the runtime writes is accepted
  (`curl -sS -i -X METHOD URL [-H 'K: V']... [--data-raw BODY]`), parsed with shlex and never run; the URL's scheme, host and port must
  equal `--base-url` (or `APITEST_BASE_URL` when the report is built), and the command is rebuilt with shell quoting. Anything else is
  dropped and the finding says so. Without a target no curl line is shown; the pytest command still is.
- Duplicates: the same classname + test name in one or several JUnit files (reruns, merged runs) counts once; the worst outcome wins.
- Pytest commands: a test name that was truncated (over 200 characters) or scrubbed is not a valid node id, so the command runs the
  whole test file instead of one test.
- Result line: FAIL (any finding), INCOMPLETE (errors, no findings), INCONCLUSIVE (nothing executed: empty or all-skipped suite, k6
  without requests), PASS (at least one executed passing test or a k6 run with requests, and no findings).
- Run problems: tests that errored before running (fixtures, setup). They are not findings and never count as a pass.
- Not tested / limitations: skip reasons with counts, intake endpoints with no result, what was not supplied.

## Severity
- pytest: the `[HIGH]`, `[MEDIUM]`, `[LOW]` tag the test put in its failure message. Untagged failures get a default for their test
  type (smoke, security, fuzz HIGH; edge, resilience LOW; everything else MEDIUM) and the finding says "default for this test type".
- k6: a crossed error-rate or checks threshold is HIGH, a crossed latency threshold MEDIUM; the stress profile is capped at MEDIUM
  because crossing a limit is its purpose. A failing k6 check is MEDIUM. In `--summary-export`, `true` means the threshold was crossed.

## Safety of the report itself
Result files are untrusted. The script does not execute or import anything from them, refuses XML with DOCTYPE or entities, bounds
size, depth and counts, scrubs secrets (shared `_common.redact_text` plus the values of secret `APITEST_*` variables) before
truncating, escapes Markdown and puts messages and commands in fenced blocks. Do not paste result files into prompts unreviewed, and
do not widen what it prints.
