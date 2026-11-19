# Changelog

All notable changes are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/). Nothing has been tagged or released yet.

## [0.1.0] - unreleased

First release candidate, built in phases (see PLAN.md).

### Added (plugin icon)
- `.claude-plugin/icon.png` (1024 x 1024, a white check mark on teal) and `"icon"` in `plugin.json`, so `claude plugin validate` no longer warns about a missing icon. It is plain geometry drawn by `tools/make_icon.py` (standard library only), not a third party's logo; replace the file if you have a design of your own.

### Added (`run_all.py --k6-writes`)
- `run_all.py` had no way to make k6 send writes, although `gen_k6.py` has `--include-writes`. `--k6-writes` (needs `--k6`) passes it through. The requests still only run when you set `APITEST_ALLOW_WRITES=yes` yourself; the driver never sets it and says so when it is unset.

### Changed (report: how many tests ran)
- Every report (Markdown, HTML, `report.json` as `result.ran`) now carries one line under the verdict: "Ran 171 of 173 tests (99%): 171 passed, 0 failed; 2 skipped and 0 errored did not run. A skipped test is neither a pass nor a failure." It is shown for every run, not only when over half of the tests skipped, so a run that mostly skipped is not read as a clean one. The over-half warning stays.

### Fixed (README example)
- The example run in the README and `examples/sample-report.*` were rerun with the shipped zip's scripts: 134 functions, 240 passed, 9 failed, 20 skipped of 269 tests (the tenant isolation matrix added two more BOLA findings on the seeded demo). The demo server itself needs this repository checkout (it reuses `tests/mock_rest.py`); the README now says so.

### Fixed (invented values in optional body fields)
- When a JSON body schema requires no field, the baseline request still sends its first three properties. Those optional fields are now tracked like required ones: a 400, 404, 409, 410, 413, 415 or 422 on a request that sent an invented value for one of them skips as "could not verify" and the report lists the field to give an example for, instead of failing the test (seen on `POST /token` of Webhook.site, where an invented `default_content_type` drew a 422). The skip and failure notes now say "parameter(s) or field(s)" without "required"; the report still reads results written with the old wording.

### Changed (rate-limit tests in a paced run)
- `run_all.py`: when the run is paced (`APITEST_RPS` or `APITEST_DELAY_MS`) and the target is this machine, the rate-limit tests no longer end as "skipped". They are left out of the paced passes and run in a burst pass of their own, without the pacing, after pass one and before the session-ending tests (`results/pass_burst.xml`), with a 2 second pause before those so they do not start inside a throttled window. Pacing given as `--apitest-rps` or `--apitest-delay-ms` after `--` is recognised too and is not passed to the burst pass, and the one report counts each test once. A target that is not local keeps your pacing: the tests skip as before and the driver prints the command for the burst pass. New `--expect-rate-limit` sets `APITEST_EXPECT_RATE_LIMIT=yes` for the run. The rate-limit tests carry a new `rate_limit` marker (`pytest -m rate_limit`).

### Fixed (fuzzed DELETE order, `--quick` with doubling stress steps)
- In a suite generated with `--types all,fuzz` (what `run_all.py --types all,fuzz` builds), the fuzzed DELETE ran before the read tests and removed the record named by `APITEST_PATH_<NAME>` or the ids file, so smoke and positive tests on that id failed with 404. That fuzz case is now marked `destructive` and runs after every other test, session-ending ones aside.
- `gen_k6.py --quick` against a local target stopped with "stage step4_ramp ramps 6.7 req/s per second; the limit is 5" since stress steps double by default there. A shortened stage now keeps the length its change of rate needs, so every profile generates; holds stay at the short `--quick` length.

### Added (setup stage, issue 46)
- `seed_ids.py`, an opt-in setup stage: one create request per resource that has a create and an item endpoint, ids written to an ids file (`APITEST_IDS_FILE` format) with the records listed under `created`; `--dry-run` previews, `--cleanup` deletes what it created. Same gate as the intrusive runs, ids you already set are kept, credentials from the environment, no bodies or tokens in the output. Nothing runs it automatically.

### Changed (duplicate creates, issue 84)
- A create refused as a duplicate (409, or 400/422 naming a duplicate) is retried once with a new tag when the body has tagged fields, and only skips if the retry is refused too. Tagged fields now also include any string the spec marks unique (`x-unique`, `unique: true`, or a description such as "must be unique", except for fields named like an id, ref or key) and a few more whole names (`serial_number`, `external_id`, `invoice_number`, `order_number`, `registration_number`, `account_number`). `APITEST_UNIQUE_BODIES=no` still turns tagging and the retry off.

### Changed (chaos payload endpoints, issue 88)
- `chaos_run.py --payload-endpoints` takes a number N or `all` as well as operation ids (default stays the first 3 write endpoints). The size, burst and run-time ceilings are unchanged, at most 500 endpoints are tried, and a skipped `test_chaos_payload_not_tried` result (shown in the report) and a stderr line say how many were not.

### Changed (skips grouped by cause, issue 90)
- The report lists skipped tests under four headings with a count each: the skill could have done it itself (duplicate creates, a tenant control call it got wrong), you need to supply something, a safety default is off on purpose, and the target cannot provide it (also where no cause is recognised). Only the first is a defect of the skill. `report.json` keeps the flat `skips` and adds `skip_groups`. No test changes.

### Changed (tenant control call, issue 85)
- The tenant matrix control call (your own tenant, writes enabled) retries once with the record's current ETag, read first, when the server answers 412 or 428, even if the spec does not declare If-Match. When it is still refused, the skip names the likely cause: an invented value to give an example for, or a record in a state the call refuses. The victim-tenant call is still sent as is, except that an operation whose spec declares a required If-Match header reads the record first (issue 89).

### Changed (could not verify, issue 89)
- A 4xx for a request value the skill invented (the spec had no example, default or enum) no longer fails the test: it skips as "could not verify", counts in the "could not run" figure, and the report lists the values that need an example. A 412 for a stale If-Match skips the same way, and an operation with a required If-Match header now reads the record first and sends its current ETag. Values from the spec, 5xx and a wrong 2xx stay failures.

### Changed (report hints, issues 86, 87)
- The report says "Role (BFLA) matrix not run: set APITEST_ROLE_MATRIX and APITEST_TOKEN_<ROLE>" in its limitations (HTML, Markdown and `role_matrix_note` in report.json) whenever that test skipped for lack of a matrix.
- `summarize_results.py` notes on stderr which format an `-o` path without `.html` or `.md` was written as, and when `--k6-dir` has no `manifest.json` (it is the folder the k6 scripts were generated into, not the results folder).

### Changed (fuzz budget, rate and hostile inputs, issue 49)
- The fuzz run budget (`APITEST_FUZZ_MAX_REQUESTS`, hard cap unchanged) is split between endpoints: each gets `min(APITEST_FUZZ_CASES, budget / endpoints)` cases (a lower `APITEST_FUZZ_CASES` is honored), so endpoints late in the list are no longer starved. When the budget leaves fewer than 20 cases per endpoint (the full hostile pass is up to 19) the pass is cut at its tail and a warning names the `APITEST_FUZZ_MAX_REQUESTS` that would fit it; the hard cap of 5000 covers about 250 endpoints. Write operations are fuzzed before reads.
- Every endpoint starts with a fixed hostile-input pass (NUL byte in each body field and query parameter, then very long, lone surrogate, control characters, emoji, right-to-left, negative and huge numbers, empty array and object, null). This covers the "no 5xx on hostile strings" check without a separate suite.
- A loopback target defaults to 50 requests per second (cap 100) instead of 5 (cap 20); other targets keep 5 and 20.

### Added (tenant isolation and role matrix, issues 47, 48)
- `test_api1_tenant_isolation_matrix`: for tenant-scoped URLs every protected operation is called with user A's token against another tenant's id and must be refused, after a control call with A's own tenant succeeds. The tenant parameter is detected (first two path segments of 60% of protected paths, at least three) or set with `gen_pytest.py --tenant-param`. Reads only unless `APITEST_TENANT_WRITES=yes`. Needs `APITEST_PATH_<TENANT>` and `APITEST_VICTIM_<TENANT>`.
- `test_api5_role_matrix`: `APITEST_ROLE_MATRIX` names a JSON file of operationId to allowed and denied roles; each role's token is `APITEST_TOKEN_<ROLE>`. Both tests are intrusive (safety gate) and skip when their variables are missing.

### Added (tokens, sign-in and cookies, issues 44, 45)
- Generated tests that call an endpoint that probably ends the caller's session (sign-out, revoke, password change or reset, session end or replace) carry a `session_end` marker and run after every other test, so they cannot invalidate the token the rest of the run uses.
- `APITEST_TOKEN_CMD` (and `_B`): a command that prints a fresh token, run without a shell when a credentialed call gets 401 (rate-limited to once per 5 s, 100 per run); the call is then repeated once. The token is never printed.
- `APITEST_LOGIN_EMAIL`, `_USERNAME`, `_PASSWORD`, `_OTP` fill the matching body fields of sign-in style operations; `APITEST_COOKIES` sends cookies such as an HttpOnly refresh token. These values, and each cookie value, are scrubbed from failure messages and reports.

### Added (empty optional body, issue 50)
- When a JSON body has no required property, the edge tests also send `{}` and require a clean answer (no 5xx). When some properties are required the baseline body already carries only those, so a separate "required fields only" case would repeat it.

### Added (driver, environment check, local Postgres chaos example, issues 54, 55)
- `scripts/run_all.py <spec> --out DIR`: intake, generation, a first pass without session-ending tests, a second pass with only those, the report, and the variables to set to run the skipped tests. It never sets the safety-gate variables and does not start chaos or k6 runs (their commands are printed). Exit code 0 unless the verdict is FAIL (an INCOMPLETE or INCONCLUSIVE run still exits 0, so read the verdict), 1 findings, 2 a step could not run.
- `gen_pytest.py --types all,fuzz` runs everything plus the opt-in fuzz suite; plain `all` still leaves fuzz out. The gate for fuzz is unchanged.
- `scripts/check_env.py` (also `run_all.py --check-env`) lists what is missing before a run: Python 3.11+, pytest, requests, PyYAML, k6, grpcurl, with install hints, and says whether the target needs the gate variables. `--needs k6,grpcurl` makes optional tools required.
- `references/chaos.md` has an example for a local PostgreSQL cluster (`su postgres -c ...`, SIGSTOP and SIGCONT for the slow case) and explains why to probe an endpoint that reads the database.

### Changed (report polish, issue 53)
- The result line counts findings in cause groups ("239 finding(s) in 6 cause group(s)") and gives a "could not run" figure (skipped plus errored) beside passed and failed. The variables that would unskip the most tests are named on the first screen ("To run more tests set: APITEST_VICTIM_X (31), ...").
- A **Root causes** table (Markdown and HTML, and `cause_groups` in report.json) comes before the individual findings when something repeats. The cause is the test name with the operation id removed.
- A run whose only findings are INFO now has the verdict NOTES instead of FAIL.
- A throttled (429) skip names the setting to change: `APITEST_DELAY_MS=250` or `APITEST_RPS=4`.

### Fixed (review follow-ups on the tenant PR)
- The documented-401 contract test (`*_error_contract_401`) uses the same probe as the no-credentials test: a real id for reads, an invented one for writes, and it skips (naming `APITEST_PATH_<NAME>`) when an invented id draws a 404 before the credentials are checked, instead of failing.
- The structured-field name matcher also recognises prefix and compound names such as `emailAddress`, `phoneNumber`, `dob` and `api_key`, but not plain words like `address`, `codename` or `company`.

### Added (examples, ids file, no-scheme specs, issues 46, 51, 52)
- The report (HTML, `report.json` field `unskip`, Markdown) carries the same "set these to run the skipped tests" list as the terminal summary.
- The pattern generator gives up early on nested counts such as `((a{5000}){5000}){5000}` instead of building a huge string. An empty `APITEST_PATH_*` variable counts as unset and falls through to the ids file and the spec example.
- Intake reads `x-example`, the OpenAPI 3.1 `examples` list and the first named `examples` entry as the example. A simple `pattern` (classes, ranges, counts, groups, first alternative) gets a generated matching value; patterns it cannot build, such as lookaheads, stay flagged as invented.
- `APITEST_EXAMPLES_FILE` (`operationId.field` or `*.field` to a value) sets body fields and query parameters at run time; the report lists the fields whose invented value drew a 4xx ("give an example for these").
- `APITEST_IDS_FILE` holds path and victim ids in one JSON file (an environment variable still wins). Every run ends with "Set these to run the skipped tests": each variable and how many tests it would unskip.
- Intake warns when a spec declares no security scheme; `--assume-bearer` (with `--public-paths` globs) makes the other operations need a bearer token. Not done from issue 46: the setup stage that creates the objects itself, tracked as a follow-up.

### Added (k6 launcher in sandboxed environments, issues 67, 70)
- `run_k6.py --check` prints whether k6 can start (its version) and exits 5 when it cannot, so a blocked binary is found before generation.
- `--k6` accepts a command with arguments (split safely, run without a shell). `--docker` and `--docker-image` run the `grafana/k6` image with the results folder mounted and environment variables passed by name only.
- When k6 exits 99 the launcher prints a last line naming which thresholds were crossed (error rate, latency, checks). The exit code stays 99.

### Added (optional headers the server enforces, issue 42)
- A write that answers 400, 412, 422 or 428 with a body naming a header the spec declares optional (`idempotency_key_required`, `if_match_required`) is reported as spec drift (LOW): "the server seems to require header X; the spec says optional", with the setting that fixes it.
- `APITEST_SEND_OPTIONAL_HEADERS=Idempotency-Key,If-Match` sends those headers on every operation that declares them. A bare `Idempotency-Key` gets a fresh unique value per call, any other name gets `*`, and `Name=value` sets the value.

### Added (k6 report: stress limit, peak reached, stage and endpoint rows, report folders; issues 62, 68, 69)
- The stress section names the last good step and the first bad step (p95 over `--p95-ms`, or a failed rate over `--error-rate` and clearly above the first step). When no step breaks it says "no limit found up to X req/s (hard cap 100 req/s)" and that this is not proof of capacity.
- Every k6 section prints the peak request rate and virtual users actually reached next to the planned peak and the hard caps. The generator writes the stage plan, p95 limit and error limit into `manifest.json` and asks k6 for request counts per holding stage; the report reads the manifest from `--k6-dir`.
- Load and soak now produce stage rows (steady state, one row per soak window). Per-endpoint latency and failed-rate rows are produced for up to 100 endpoints (was 20, latency only), and endpoints with failures are listed in the report.
- `summarize_results.py --root DIR`: `--tests-dir` and `--k6-dir` may be relative or absolute and may use `..` as long as they stay inside the root (default: the current folder), so results in a sibling folder work.

### Added (k6 value files, issue 65)
- k6 scripts read `APITEST_IDS_FILE` (`{"path": {"id_name": "..."}}`) and `APITEST_EXAMPLES_FILE` (`{"operationId.field": value, "*.field": value}`) with the same format and rules as the pytest suite: example values replace required query values and top-level body fields, ids fill path parameters that have no `APITEST_PATH_<NAME>`. An invalid or unreadable file stops the run before any request, without echoing the file.
- The generator notes how many endpoints still send an invented value for a required field and names the first few, with the key to put in the examples file. With `--docker`, `run_k6.py` mounts both files read-only and points the variables at the mounted copies.

### Changed (report wording)
- The coverage sentence now says the baseline probe request of a dropped endpoint is still counted in k6's overall failed-request figure.

### Changed (k6 launcher exit codes, issue 70)
- When the baseline probe leaves nothing to test, k6 aborts with its own code 108; the launcher now says so ("every endpoint failed the baseline probe") and names `APITEST_K6_BASELINE=off`. Real-k6 tests that expect a threshold failure against a dead target now switch the probe off, so the optional k6 CI job passes again.
- `docs/evals.md` describes the k6 failure runs with exit 91 instead of 99, and the circuit-breaker test no longer depends on k6 printing the threshold expression (older k6 versions do not).
- `run_k6.py` exits 91 when an error-rate or checks threshold is crossed (also when latency crossed too, since failures come first), 92 when only latency is crossed, and keeps 99 when the summary cannot say which kind. The last line still names the kinds. Callers that tested for 99 must now accept 91 and 92 (a nonzero code is still a failed run).

### Added (token refresh for long k6 runs, issue 64)
- `APITEST_TOKEN_CMD` (the same variable the pytest suite uses: a command that prints a fresh token; split like a shell would, run without a shell) keeps long k6 runs authorized. `run_k6.py` runs it once before the start, puts the token in the bearer variable(s) the script reads (`tokenEnv` in `manifest.json`, or `APITEST_TOKEN_ENV`), and serves fresh tokens on a loopback-only URL behind a random path. Each VU fetches a token before its first request, after a 401 (at most one refetch per VU every 5 s) and every `APITEST_TOKEN_REFRESH_S` seconds when set. The command output is never printed or logged; a failing command or one that prints more than a single token stops the run with a message that does not include the output. The fetches count as a few extra local requests in k6's totals.

### Changed (k6 error rate counts only the load, issue 92)
- Load requests carry the tag `phase:load` and the error-rate threshold and the 50% circuit breaker now read `http_req_failed{phase:load}`. The baseline probe (one request per endpoint) and the token fetches are no longer part of the verdict: a probe that fails at baseline, which the run already leaves out, can no longer push a healthy run over the 1% limit and exit 91. The report shows the failed rate of the load, and k6's overall `http_req_failed` still includes the probe requests.

### Changed (stress ramp climbs to the cap on local targets, issue 93)
- When the base URL given to `gen_k6.py` is loopback and neither `--rate` nor `--steps` is set, the stress steps double (5, 10, 20, 40, 80, 100 req/s, up to 100 VUs) instead of stopping at 20 req/s. New `--ramp auto|linear|double`: `linear` keeps the old plan, `double` doubles from any rate. Remote targets and explicit `--rate` or `--steps` keep the linear steps. The hard caps and the safety gate are unchanged. The generator says when it chose doubling.

### Changed (k6 token refresh runs one sign-in per renewal, issue 91)
- The launcher's token server numbers the tokens and each VU says which one it holds. A VU that is behind gets the current token without a new sign-in; only a rejection of the current token (or the optional timed refresh) runs `APITEST_TOKEN_CMD`, at most once per `APITEST_TOKEN_MIN_GAP_S` (default 30 s, was 2 s) and at most `APITEST_TOKEN_MAX_REFRESHES` times per run (default 200). This stops VUs from invalidating each other on APIs with one live session per user, and from hitting a sign-in rate limit. The run ends with "token refreshed N time(s)". A token command for such an API should return a young cached token.

### Added (run_all runs k6 and chaos, issue 94)
- `run_all.py --k6 [PROFILES]` (default load, stress, spike; soak is named explicitly) generates and runs the k6 profiles after the tests, `--k6-docker` uses a container, and `--chaos [--chaos-experiments LIST] [--dependency-config FILE --allow-hooks]` runs chaos_run.py. Their summaries go into the same report and verdict. Each part goes through its own script and safety gate: the driver never sets the confirmation variables, says so when a gate refuses and skips that part, and checks that k6 can start before running any test. The dependency commands need `--allow-hooks` as well, never implied by the file. Without the flags the output ends with the same "Next:" hint.

### Added (fuzz budget report and endpoint list, issue 66)
- The fuzz run ends with a "Fuzz budget" section: requests sent, how many endpoints got any, min, median and max per endpoint, the endpoints that got no request because the run budget was used up, and those cut off part-way (with the settings that help). It appears for GraphQL suites too.
- `APITEST_FUZZ_ENDPOINTS=id1,id2` fuzzes only the named operations (REST ids, GraphQL operation names); the rest skip with that reason. The budget is split among the named operations only, duplicates count once, and an id that no fuzz test has is named in the summary (a typo no longer skips everything silently). GraphQL fuzzing now also shares the budget fairly between operations.

### Added (k6 baseline probe and coverage, issues 61, 63)
- Before the load starts, each selected endpoint is sent once (paced under the rate cap). An endpoint that already fails with its generated request (invented value rejected, another role's endpoint, a server error) is left out of the run and listed as "not load tested: fails at baseline with status N", so the error-rate threshold judges load, not fixtures. `APITEST_K6_BASELINE=off` keeps them all. If every endpoint fails, the run aborts with a clear message.
- Stages that never ran (the circuit breaker stopped the run) show "not run" instead of 0, are not counted as good steps in the stress result, and the report says the run stopped early.
- `run_k6.py` keeps the coverage line the script prints and writes `results/<profile>-coverage.json` (tested endpoints, skipped endpoints with the variable to set, baseline failures). The HTML, Markdown and JSON report show them in the k6 section and say how to read the error rate; without the file the report says coverage was not recorded.

### Fixed (report evidence and report.json, issues 41, 43)
- A failure that is a 400, 404, 409, 410, 413, 415 or 422 on a request that used an invented value (no `example` or default in the spec) is now tagged INFO and titled "needs an example", not HIGH or MEDIUM. 401, 403, 429 and 5xx keep their normal rating.
- `report.json` now lists every finding (up to 5000) instead of the first 100. New fields: `findings_shown` (how many the page and Markdown list) and `findings_truncated`.

### Fixed (tenant-scoped APIs, issues 38, 39, 40)
- Integration flows no longer fail with `KeyError` for resources under a tenant path such as `/b/{business_id}/...`. Path parameters that no create step in the flow produces are read from `APITEST_PATH_<NAME>`; when one is not set the flow skips and names the variable.
- The "request without credentials must be rejected" test uses a configured `APITEST_PATH_<NAME>` id (or a spec default) when there is one, for read operations; write operations always use a made-up id so a wrongly accepted unauthenticated write cannot change real data. With a made-up id it also accepts 404, because many APIs resolve the path before they check who is asking, and says so in the test message.
- The `minLength` and `maxLength` edge cases for fields whose name implies a structure (email, password, code, otp, token, url, date, phone, file name and similar) no longer demand success from a run of `a`; they only require a clean answer (2xx or 4xx, never 5xx).
### Added (GitHub issues for failed tests, 2026-11-18)
- `scripts/file_issues.py`: files GitHub issues for the findings in `report.json`. Preview by default (offline, no token); `--create` shows the preview and asks for `yes` (or takes `--yes`), and refuses without a terminal or a token. Findings on the same endpoint with the same kind of failure become one issue; a hidden fingerprint makes reruns skip issues already filed (closed ones too, unless `--refile-closed`). Samples are scrubbed again, `--no-samples` leaves them out, the token comes from `APITEST_GITHUB_TOKEN` and is only sent over https. Limits: `--min-severity`, `--max-issues`. Reference: `references/issues.md`; tests: `tests/test_file_issues.py`, run against a local fake GitHub API.

### Checked (chaos rerun on the fixed demo, 2026-11-17)
- Chaos rerun against spring-boot-demo main 0acc5b5 (JDBC socket timeout fix) on a local PostgreSQL 16, with the skill at f790ae6: proxy-latency, proxy-5xx, proxy-drop, payload, dep-outage and dep-slow, 16 checks, 16 passed, 0 failed. `dep-slow` (database frozen with SIGSTOP for 6 s): requests during the freeze failed with p95 4.0 s and the API recovered 0.8 s after SIGCONT; `dep-outage` recovered in 3.2 s. The earlier MEDIUM finding is gone. `dep-blackhole` was not rerun, the example report was not regenerated, and capacity was not measured.

### Fixed (rate-limit burst setting, 2026-11-17)
- `APITEST_RATE_BURST` is read through one runtime helper. A value that is not a whole number from 1 to 1000 now fails the test with one clear message instead of a `ValueError` traceback in the generated suite. An unset value still uses the per-test default.
- The four review notes from the chaos and sample-value PR were already fixed in the 2026-11-14 batch below; nothing was left open there.
- README lists what is still unverified: ghz load tests, the AWS emulator run, the stand-in status of the gateway and GraphQL apps, and the VAmPI mass-assignment gap.

### Checked (final full pass over v2, 2026-11-17)
- Full suite on main: 616 pass and 23 skip with the example-app packages installed (605 and 34 with only the dev requirements). `ruff check`, `tools/validate_skill.py` and the zip build are clean. The gRPC suites also pass against real grpcurl 1.9.1 (32 tests in `test_grpc.py` and `test_grpc_real.py`).
- Eval tables re-run on main: Petstore mock 325 pass, VAmPI mock 232 pass and 1 known failure, seeded FastAPI app 91 pass, GraphQL mock 102 pass; every seeded flaw is found except the documented VAmPI mass-assignment gap. `docs/evals.md` had stale clean-variant counts (326, 230 and 85) and now matches these runs.
- Not run: k6 load tests (k6 is not installed here; CI runs them), gRPC load through ghz (not generated yet) and the AWS emulator.

### Fixed (issue 22, 2026-11-17)
- A spec built from nested YAML aliases (an alias bomb) no longer hangs intake or writes gigabytes of JSON. Resolving stops after 50,000 nodes in one schema and 1,000,000 across all schemas, the examples and defaults an author wrote are cut at 10,000 nodes each, and the finished intake at 500,000 nodes. Each cut adds a warning, and the schema one names the schema. Ordinary large schemas are unaffected. Test counts were measured on this branch merged with main at f4f31b0: 613 pass and 34 skip with only the dev requirements.

### Added (local gateway and GraphQL examples, 2026-11-17)
- `examples/aws-local/`: a FastAPI app that behaves like an API Gateway REST API in front of a function (stage prefix, `x-api-key`, usage-plan throttling, request validation errors, `502` for a failing function) and a strawberry GraphQL app with an API key. `tests/test_aws_local.py` runs the generated suites against both as real servers, clean and with seeded flaws (skipped unless fastapi, uvicorn and strawberry-graphql are installed).
- GraphQL intake: `intake.py --api-key-header NAME` sends the credential in that header (from `APITEST_APIKEY_KEY`) instead of a bearer token. The runtime uses whichever scheme the intake declares.
- Fixed: a GraphQL argument that is non-null but has a default (`first: Int! = 20`) is optional, but its variable was still declared `Int!` and left out, so every server rejected the request. The variable is now declared nullable. Found by running the suite against the real strawberry server. The k6 GraphQL generator got the same fix.
- SECURITY.md notes that a bearer token is visible in grpcurl's process arguments.
- A clean run of the whole suite is now 605 pass and 34 skip with only the dev requirements (616 pass and 23 skip with the example-app packages installed).
- The AWS emulator was not run: there is no Docker daemon in the build environment and the Debian package could not be located. These apps are stand-ins, not the emulator.

### Added (gRPC unary tests, 2026-11-17)
- `intake.py` reads `.proto` files (proto2 and proto3, nested messages, `oneof`, `map`, imports below the proto's folder) and `gen_pytest.py` generates smoke, positive, negative, edge, contract and security suites for unary methods. They call the optional `grpcurl` tool, so nothing else is needed; without it every test skips.
- Reports and repro lines show a gRPC call as an HTTP-shaped exchange with the auth header redacted. Streaming methods are listed and skipped.
- Tested two ways with seeded flaws (missing auth, invalid input accepted, open reflection, wrong response shape, server fault on injection, leaked internals): against a stand-in `grpcurl` (always runs), and against a real `grpcio` server through real grpcurl 1.9.1 (`tests/test_grpc_real.py`, skipped unless grpcio, grpcio-tools and grpcurl are installed). Clean run: 73 tests pass, and every seeded flaw is found. Load tests through `ghz` come next. Hostile `.proto` files (non-integer or out-of-range field numbers, very deep nesting including `oneof`, non-UTF-8 bytes) end in a one-line error. A clean run of the whole suite is now 591 pass and 24 skip.

### Fixed (issue 28, 2026-11-17)
- A GraphQL SDL number literal that is too large (an integer over Python's 4300-digit limit, or a float such as `1e999` that overflows to infinity) now stops intake with `GraphQL parse error: number too large` (exit code 2) instead of a traceback or an invalid `Infinity` in the JSON.

### Added (GraphQL resilience tests, 2026-11-17)
- `--types resilience` now works for GraphQL (and is part of `all`), so every pytest type is available for GraphQL. Repeatable reads, a response budget, page-size and cursor handling for list queries, an `intrusive` rate-limit burst (a 429 needs a valid `Retry-After`) and a repeated delete of an object the test created itself.
- The GraphQL fuzzer skips the `long_name` case when the byte limit leaves no room for it, instead of always repeating the name twice.
- The GraphQL mock gains `ignorefirst`, `slowpets` and `throttle` flaws (unit-test only).
- A clean run of the whole suite is now 565 pass and 18 skip.

### Added (GraphQL data-validation tests, 2026-11-17)
- `--types data-validation` now works for GraphQL (and is part of `all`): one violation per case on a valid baseline, rejection expected. Covers missing required input fields, wrong types and unknown enum values inside input objects, unknown input fields, and breaches of `@constraint` limits (`minLength`, `maxLength`, `min`, `max`, `format`).
- The SDL parser now reads `@constraint` limits into the intake (`constraints` on arguments and input fields); values of the wrong kind are ignored. Introspection JSON has no directives, so constraint cases need the SDL.
- The GraphQL mock enforces `@constraint` and rejects unknown input fields, and has a `novalidate` flaw that does neither. `stale`, `undeleted` and `novalidate` are unit-test flaws only: the eval table still lists the original 10 seeded GraphQL flaws.
- Hardening: `@constraint` limits must be finite numbers within 10^12 (or strings of at most 200 characters), and a `minLength` above 10000 gets no too-short case, so a hostile schema cannot make the generator build a huge string or write an unimportable suite.
- The GraphQL clean eval variant now runs 85 tests, all 10 seeded GraphQL flaws are still found, and a clean run of the whole suite is 557 pass and 18 skip.

### Added (GraphQL integration flows, 2026-11-17)
- `--types integration` now works for GraphQL (and is part of `all`): per object type with a create, a read by id and a delete mutation, one flow runs create -> read -> list -> update -> read -> delete -> read (gone). It uses only the object it creates, cleans it up even when a step fails, and skips writes on production. A schema with no such type gets a generator note.
- Fuzz follow-ups: `huge_whitespace` and `long_name` cases respect the byte limit, the opt-in test asserts the `deletePet` entry directly, and a test shows an unconfirmed remote target is refused before any request.
- The GraphQL mock gains an `updatePet` mutation and two seeded flaws (`stale`: an update that is not stored, `undeleted`: a delete that does nothing). The GraphQL clean variant now runs 72 tests, and a clean run of the whole suite is 549 pass and 18 skip.

### Added (GraphQL fuzz tests, 2026-11-17)
- `--types fuzz` now works for GraphQL (opt-in, intrusive, not part of `all`): one seeded, bounded test per query and mutation sends hostile variable values, missing and extra variables, malformed envelopes, damaged query text and other content types, and reports 5xx, hangs, stack traces and slow answers. The REST fuzzer's send-and-judge loop moved into a shared `run_cases`. `delete*` mutations also need `APITEST_ALLOW_DESTRUCTIVE`.
- Documented: an `Int` answered as `1.0` fails the contract check.

### Added (GraphQL contract tests, 2026-11-17)
- `--types contract` now works for GraphQL (and is part of `all`): each query that returns an object is run with its default selection and the answer is checked against the schema: selected fields present, scalar JSON types, enum values, nulls only where the type allows them, list types as arrays, nested lists (`[[Item!]!]!`) level by level. Mismatches are MEDIUM findings. The generated `operations.py` carries the type table the check needs.
- A tenth seeded GraphQL flaw (`badshape`: a number for a String, an enum value the schema lacks) joins the eval table: 27 of 28 seeded flaws are now found (the gap is still VAmPI mass assignment), and the GraphQL clean variant runs 66 tests.
- Fixed in the same change: the README test count (a clean run with only the dev requirements is 534 pass and 18 skip).

### Added (webhooks and callbacks, 2026-11-17)
- OpenAPI 3.1 `webhooks` and operation `callbacks` are read into a `webhooks` list in the intake (name, method, payload schema and example, response codes; callbacks also the parent endpoint and expression). `intake.py --summary` lists them. They are calls the API makes to you, so no tests are generated for them. Secret-looking payload values are dropped, and at most 200 are listed.
- A whole-file `$ref` no longer shows an internal name (`f4`) as the schema's `$name`.
- Wrongly typed fields in a webhook or callback, a non-map `paths` and a non-map `responses` become warnings instead of crashing the intake.

### Added (multi-file specs, 2026-11-17)
- OpenAPI specs split over several files are read: relative file `$ref`s to schemas, path items and parameters, and refs between the files, are resolved offline. Only `.yaml`, `.yml` and `.json` files inside the spec's folder are read (at most 50 files of 5 MB each). Absolute paths, URLs, `..` escapes, symlinks pointing out of the folder, unreadable or over-nested files are skipped with a warning. YAML aliases stay shared, so an alias-heavy spec does not blow up, and a spec without file refs is parsed exactly as before.

### Added (mass-assignment read-back, 2026-11-17)
- The API3 mass-assignment probe reads the created record back when the spec has a matching read endpoint, and fails if an injected privileged field (`is_admin`, `role`, ...) was stored even though the create response did not echo it. The probe record is removed afterwards when the spec has a delete endpoint. The probe body now gets the same run tag as other create bodies.
- When no id can be taken from the create response, the read-back skips with a reason. Still not covered: creates whose read endpoint sits under another path or documents no such field (the VAmPI register case), and nested resources.

### Fixed (unique create bodies review, 2026-11-16)
- Unique-field detection matches whole field names only. `country_code`, `currency_code`, `postal_code`, `hostname`, `surname`, `postcode` and `reference` are no longer tagged; fields with a `const` or `default`, or an example that looks like a fixed code (`US`, `AB12`), are skipped. The intake now keeps `const` in simplified schemas (dropped for secret-named fields like `example` and `enum`).
- `scrub()` in the generator also drops `const` for secret-named properties (defense in depth, with a canary test).
- Under pytest-xdist each worker adds its number to the tag; `POST /authors` is no longer mistaken for an auth endpoint.
- Docs: a pinned `APITEST_RUN_ID` repeats values on a rerun; multipart bodies and the k6 scripts are not tagged.

### Added (unique create bodies, 2026-11-14)
- Baseline create bodies get a run tag on unique-looking string fields (`email`, `username`, `slug`, `sku`, `name` ...), so repeated creates on a target that keeps data no longer collide and skip. Enum, pattern, secret and read-only fields are never touched, nor values where the tag would not fit `maxLength`. Echo and read-back checks compare against the body that was actually sent. `APITEST_UNIQUE_BODIES=no` opts out, `APITEST_RUN_ID` pins the tag.

### Fixed (review nits, 2026-11-14)
- Name-based sample values: a hinted email, url or phone number cut by `maxLength` falls back to `sample-<name>` instead of becoming invalid; the hint patterns are narrower (`email_template`, `phone_type`, `order_status` get no hint; `homepageURL` does).
- `chaos_run.py`: a second signal during cleanup no longer skips writing the results; the run still exits 4. `references/chaos.md` reworded.

### Changed (demo fix, 2026-11-14)
- Docs: the chaos `dep-slow` finding is fixed in spring-boot-demo (PR #5, JDBC socket timeout, 503 in 2 to 3 s); README and `docs/evals.md` say so. The chaos suite was rerun on 2026-11-17, see below.

### Changed (naming and README audit, 2026-11-14)
- Plugin, marketplace and display name are now `api-test-pilot-skill` / "API Test Pilot Skill" (README, plugin.json, marketplace.json, listing copy, release checklist). The k6 template header is `X-Load-Test: api-test-pilot-skill` and its user agent `api-test-pilot-skill-k6`. The skill folder stays `api-testing`.
- README: scope wording (gRPC planned for v2; accessibility, mobile and hosted service outside the skill), status line, results tables match the final runs, exit codes, install notes.
- Samples: the spring-boot-demo report is the final run (189 tests); the sample report title marks the demo API as deliberately flawed; new `examples/public-sandbox/` with the four read-only public API reports and specs.
- Fixed: `docs/RELEASE.md` said `claude plugin marketplace add .`, which Claude Code rejects; it is `./`.

### Added (request and response in reports, 2026-11-14)
- Generated suites record the first three requests of each test (method, URL, status, time, headers, bodies; secrets redacted, bodies clipped) into the JUnit file; the HTML and JSON reports show them under each test case.

### Changed (reports and docs, 2026-11-14)
- `summarize_results.py` now writes a self-contained HTML report (inline CSS, JS and SVG charts, no external assets, light and dark theme) and a `report.json` next to it. Default output is `reports/report.html`; `-o report.md` still writes Markdown, `--no-json` skips the JSON.
- README: results section (real Spring Boot run, public sandbox run, seeded flaws), install and usage for every test type, group or single test, safety gate and env var table.
- Code comments rewritten to be shorter and plainer; generated files no longer carry the project name in their header comments.
- Samples: `examples/sample-report.html` and `examples/spring-boot-demo/` (inputs, HTML and JSON report).

### Fixed (eval follow-ups, 2026-11-13)
- Invented sample values from the field name when the spec gives none: `email` gets a valid address, `*_status`/`status_code` 200, `*_url`/`link`/`webhook` an https URL, `port` 8080 (a range, default, enum or pattern still wins). They stay marked as guesses.
- No create-then-read integration flow for a path parameter the server echoes or computes (`/anything/{anything}`), unless the create response documents it.
- A skipped test for an inferred auth scheme says the scheme was inferred from the parameter name.
- `chaos_run.py` exits 4 when a signal cuts the run short (partial results, faults undone); `references/chaos.md` notes that a secret in the target URL path would still be echoed.

### Added
- Skill `api-testing`: `SKILL.md` router plus 16 reference files loaded on demand.
- Intake parsers: OpenAPI 3.x and Swagger 2, Postman collections, curl commands, GraphQL SDL and introspection JSON, all
  normalized to one intake JSON (`intake.py`).
- Generators for pytest suites: smoke, positive, negative, edge, data-validation, contract, integration, resilience and
  security (OWASP API Top 10), for REST and GraphQL, with a runtime that enforces the safety gate and redacts secrets.
- Breaking-change report between two spec versions (`spec_diff.py`).
- k6 scripts for load, stress, spike and soak (`gen_k6.py`, `run_k6.py`), hard caps and a circuit breaker.
- Seeded, rate-limited fuzzing for REST (`--types fuzz`) and fault-injection plans (`gen_chaos_plan.py`).
- Executable chaos testing (`chaos_run.py`, `chaos_proxy.py`): latency, error, drop, truncate and blackhole faults in a local proxy, TCP faults between the API and a dependency, dependency start/stop commands the user supplies, malformed and oversized payload bursts, steady load with recovery-time measurement; JUnit and JSON output that `summarize_results.py` reports. Same safety gate, hard caps, loopback-only proxies.
- `APITEST_READ_ONLY=yes` (or `--apitest-read-only`): public-sandbox mode that sends only GET/HEAD/OPTIONS and no active probes (pytest suites, k6 scripts and `chaos_run.py`).
- Safety gate for every non-functional flow (`safety_gate.py`, a copy inside each k6 script, the pytest runtime).
- Markdown report from pytest JUnit XML and k6 summaries (`summarize_results.py`), with a repro command per finding.
- Eval driver, mock targets and a real local seeded target (`evals/`); results in `docs/evals.md`.
- Packaging: Claude Code plugin and marketplace manifests, `tools/validate_skill.py`, `tools/build_skill_zip.py`,
  LICENSE (MIT), CONTRIBUTING, release checklist and marketplace listing copy (`docs/`).
- Repository hygiene: SECURITY.md (reporting and responsible use), issue and pull request templates, GitHub Actions CI
  (tests on Python 3.11 to 3.13, validator, ruff, optional k6 job), `ruff.toml`.
- `examples/`: a demo API with seedable flaws, a sample spec and a real generated report.

### Fixed
- Required request-body fields whose sample value is invented are flagged in the failure text of a 4xx (as query parameters already were); integration flows skip with a reason when a create response has no id (Location header or a single id-like field are used first); a credential parameter with no declared security scheme (`X-API-Key`, `Authorization`, `api_key`) is treated as auth with a warning instead of being sent without credentials.
- False positives found by running the suite on four public APIs: the "unknown id returns 404" test now runs only for path
  parameters that name a resource (`id`, `petId`, `uuid`, `slug`, `username`, or the only parameter of an endpoint that documents a 404),
  not for `/redirect/{n}` or `/anything/{anything}`; optional query parameters with no spec example, default, enum, range or format are
  no longer sent with an invented value (`?status=1`), and a failure caused by an invented required value says so; a failure to
  connect (refused, DNS, connect timeout, proxy tunnel) is retried for GET/HEAD/OPTIONS and then skipped as "target unreachable" (smoke tests
  and `APITEST_UNREACHABLE=fail` fail instead, so a dead target still exits non-zero). An error after connecting (drop, reset, hang) fails
  the test with a clear message for every method, because it is what a crashing server does.
- The safety-gate skip is reported as one `-rs` line plus one summary line, not one line per test. The gate itself is unchanged.
- Redaction fails closed: values nested deeper than 64 levels, or too deep to parse, are replaced by a placeholder and never printed
  raw (a secret hidden deep in a response can no longer reach a failure message or the JUnit file).
- Deeply nested JSON (a hostile spec, a fuzz body or a server response) no longer crashes redaction, repro lines or
  response parsing: `RecursionError` is handled like "not JSON" (this made five fuzz tests fail on Python 3.11); `intake.py`
  reports an over-nested input with exit code 2.
- `evals/run_evals.py --real` against a remote target without `APITEST_TARGET_ENV` prints one clear message and exits 2
  instead of a traceback.

### Changed
- Request throttle: `APITEST_DELAY_MS` / `APITEST_RPS` (or `--apitest-delay-ms` / `--apitest-rps`) space every request the generated
  suite sends; `APITEST_RETRIES` (default 2) sets the retries after a failed connect. Rate-limit probes skip while a throttle is set.
- A remote target without `APITEST_TARGET_ENV` now stops the whole pytest run with exit code 4 and one clear message,
  instead of erroring test by test (generated `conftest.py`, `preflight()` in the runtime).
- Destructive tests carry a `destructive` pytest marker; the run-last ordering uses it instead of reading test source.
- Runs where more than half of the tests skip print a warning in pytest and add one to the report.

### Known gaps
- Real Petstore, VAmPI and DVGA runs were not possible from the build sandbox; those evals ran against labelled mocks.
- VAmPI mass assignment is not detected (documented in `docs/evals.md`).
- GraphQL fuzzing, mass-assignment read-back and uniquified create bodies are deferred to a later version.
