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
  4 k6 missing (it prints the install hint and the command; say plainly that nothing was run), 99 a threshold failed.
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
- Requests: 10 s timeout, no redirects (a redirect cannot lead the run to another host), header `X-Load-Test: api-test-pilot-skill`.
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
Report: p95/p99 per endpoint, error rate, which endpoints were skipped and why, and the rerun command. Thresholds are the user's SLO:
say what you assumed.

## Not verified by the generator
It cannot know the server's capacity, autoscaling or rate limits. A 429 counts as a failed request; if the API throttles, lower `--rate`
instead of reading the failures as a bug.
