# Load testing (k6)

**Safety gate first.** Load, stress, spike and soak runs send real traffic. Before generating or running anything the user must
confirm (a) the target is non-production and (b) they own it or are authorized to test it. If either is missing, stop.
Production is never a target, even with the variables set. Never set the gate variables yourself: ask, then have the user export
`APITEST_TARGET_ENV=<staging|dev|qa|...>` and `APITEST_AUTHORIZED=yes` (loopback addresses such as `localhost` pass without them).

Shared by `stress.md`, `spike.md` and `soak.md`. One generator, one launcher, four profiles.

## Workflow
```
python scripts/intake.py spec.yaml -o intake.json           # REST or GraphQL (same shape)
python scripts/gen_k6.py intake.json -o k6_tests --profiles load --rate 5 --base-url http://localhost:8080
APITEST_TARGET_ENV=staging APITEST_AUTHORIZED=yes APITEST_BEARERAUTH_TOKEN=... \
  python scripts/run_k6.py k6_tests/load.js --base-url https://staging.example.com
```
- `run_k6.py` checks the gate **before** k6 starts, prints the exact rerun command (never a secret) and writes
  `results/<profile>-summary.json` (k6 `--summary-export`; input for the report step). Exit codes: 3 gate refused,
  4 k6 missing (it prints the install hint and the command; say plainly that nothing was run), 5 k6 cannot start (`--check`), 91 error rate or checks threshold failed, 92 only a latency threshold failed, 99 a threshold failed and the kind is unknown (the last line names which: error rate, latency, checks).
- Each generated script enforces the same gate again at start-up, so `k6 run k6_tests/load.js` directly is gated too.
- `--quick` shrinks every stage 10x: use it to check the plumbing (ids, credentials, thresholds) before a real run. It says nothing about capacity.
- Never pass `--http-debug` to k6: it prints the Authorization header.

## What is generated
- Open-model load (`ramping-arrival-rate`): the request **rate** is fixed, not the user count, so a slow server cannot reduce the pressure.
- Endpoints: **GET/HEAD only by default**. Writes need `--include-writes` at generation **and** `APITEST_ALLOW_WRITES=yes` at run time.
  DELETE and GraphQL mutations are never generated. Admin endpoints are skipped unless named in `--endpoints`.
- Path ids come from `APITEST_PATH_<PARAM>` (or the spec example); GraphQL ids from `APITEST_GQL_<OP>_<ARG>` / `APITEST_GQL_<ARG>`.
  Credentials come from the env vars the intake lists. Endpoints missing an id or credential are skipped and named in a warning
  (names only, never values); if nothing is left the run aborts before sending a request.
- GraphQL: query operations only, selections without secret-looking fields; HTTP 200 with a non-empty `errors` array counts as a failure.
- Requests: 10 s timeout, no redirects (a redirect cannot lead the run to another host), header `X-Load-Test: api-test-pilot`.
- Every request is tagged `endpoint:<id>` and `stage:<label>`, so the summary shows per-endpoint and per-stage numbers.

## Caps and limits (generator and script)
| Limit | Default | Hard cap |
|-------|---------|----------|
| Rate (req/s) | load 5, stress 5, spike 5, soak 2 | 100 (any stage) |
| Virtual users | load 20, stress 50, spike 50, soak 10 | 200 |
| Total duration | load 4 min, soak 30 min | 4 h |
| Steady ramp | at most 5 req/s per second | same (spike: ramps of at least 5 s) |
Caps can be lowered at run time (`APITEST_MAX_RPS`, `APITEST_MAX_VUS`), never raised. A **circuit breaker** aborts any run when more than
50 % of requests fail (after 20 s): the target is down, more load teaches nothing. If k6 runs out of VUs it reports `dropped_iterations`:
the server cannot keep up with the planned rate (a finding for load and soak, expected for stress).

## Load profile
Ramp to `--rate`, hold (`--duration`, default 3 min), ramp down. **Pass/fail**: p95 < `--p95-ms` (default 500), p99 < 3x that, error rate < `--error-rate`
(default 1 %), checks > 99 %, plus the breaker. Pick `--rate` from real traffic (peak requests per second), not a guess; ask the user.
Long runs and short-lived tokens: set `APITEST_TOKEN_CMD="<command that prints a token>"` (and optionally `APITEST_TOKEN_REFRESH_S=240`). `run_k6.py` runs it before the start and serves refreshes to the script on a loopback-only URL, and the script also refetches after a 401. Tokens are numbered and each VU says which one it holds, so VUs that were rejected together cause one sign-in, not one each: the command runs after a rejection of the current token at most every `APITEST_TOKEN_MIN_GAP_S` seconds (default 30) and `APITEST_TOKEN_MAX_REFRESHES` times (default 200), and the run ends with "token refreshed N time(s)". Set the timed refresh below your token lifetime plus the gap. For an API with one live session per user or a sign-in rate limit, the command should return a young cached token (a new sign-in ends the previous token). The output is never printed. The command runs on your machine with your credentials, so use one you trust.
Valid values: the spec's example, default or enum are used first. For fields the spec gives no example for (the generator lists them), set `APITEST_EXAMPLES_FILE=examples.json` (`{"operationId.field": value, "*.field": value}`) and `APITEST_IDS_FILE=ids.json` (`{"path": {"business_id": "..."}}`), the same files the pytest suite reads.
Before the load starts every endpoint is sent once; one that already fails with its generated request is left out and listed (`APITEST_K6_BASELINE=off` keeps it). The error-rate threshold and the circuit breaker read `http_req_failed{phase:load}`, the requests sent during the load: the probe requests and the token fetches are not in it, so a probe that failed once cannot fail a healthy run. k6's own overall `http_req_failed` figure still includes them. Coverage (tested, skipped with the variable to set, dropped at baseline) is in `results/<profile>-coverage.json` and in the report.
Report: p95/p99 per endpoint, error rate, which endpoints were skipped and why, and the rerun command. Thresholds are the user's SLO:
say what you assumed.

## Not verified by the generator
It cannot know the server's capacity, autoscaling or rate limits. A 429 counts as a failed request; if the API throttles, lower `--rate`
instead of reading the failures as a bug.
