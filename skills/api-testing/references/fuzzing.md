# Fuzzing (REST and GraphQL)

**Safety gate first.** Fuzzing sends hostile and malformed input, including to write endpoints. Only against a non-production target the user owns or is
authorized to test; production is never fuzzed. Fuzz tests carry the `intrusive` marker: the generated `conftest.py` skips them unless the target is local, or
`APITEST_TARGET_ENV` is a non-production value **and** `APITEST_AUTHORIZED=yes`. Never set these yourself: ask the user.

## Generate and run
```
python scripts/gen_pytest.py intake.json -o fuzz_tests --types fuzz       # opt-in: not part of --types all
APITEST_BASE_URL=http://localhost:8080 APITEST_BEARERAUTH_TOKEN=... python -m pytest fuzz_tests -v -ra
```
GraphQL schemas are fuzzed too: `gen_pytest.py intake.json --types fuzz` on a GraphQL intake writes `test_graphql_fuzz.py` (one intrusive test per query and mutation; subscriptions are skipped). It sends hostile variable values, missing and extra variables, malformed request envelopes and damaged query text, always as variables and never glued into the query, under the same bounds and safety gate. `delete*`-style mutations also need `APITEST_ALLOW_DESTRUCTIVE`. Schemathesis is a heavier alternative for
users who already have it; it is not wrapped, so run it with the same gate rules (non-production, authorized, rate-limited).

## What one test does (`fuzz_runtime.py`)
One test per endpoint builds a bounded list of cases around the valid baseline request and sends them in round-robin order:
body fields replaced by wrong types, boundary numbers (int32/int64 overflow, huge floats), oversize/unicode/control/NUL strings, injection strings
(SQL, NoSQL, XSS, template, format string, traversal, shell, LDAP, XXE-looking XML, CRLF); missing fields and `__proto__`-style extra keys; whole-body swaps and deep
nesting; malformed raw JSON (truncated, trailing comma, BOM, NaN, invalid UTF-8, gzip garbage); content-type mismatches; odd query and path values (including raw encoded segments).
Highest-value payloads come first, so a small budget still covers them.

**Findings** (each with severity and a redacted curl repro; response bodies are never printed):
5xx on malformed input (MEDIUM), connection reset or hang/timeout (MEDIUM), stack trace/database error/file content in a response (HIGH), sensitive fields in an error body (MEDIUM),
CRLF reflected into a header (HIGH), slow answers over `APITEST_FUZZ_SLOW_MS` (MEDIUM, possible ReDoS). A 4xx is the correct answer to bad input and is never a finding.

## Limits (env vars can lower the hard caps, never raise them)
| Variable | Default | Hard cap |
|----------|---------|----------|
| `APITEST_FUZZ_CASES` per endpoint (upper bound) | 40 | 500 |
| `APITEST_FUZZ_RPS` | 5 (50 for a loopback target) | 20 (100 for a loopback target) |
| `APITEST_FUZZ_MAX_REQUESTS` per run | 2000 | 5000 |
| `APITEST_FUZZ_MAX_BYTES` per payload | 65536 | 1 MiB |
| `APITEST_FUZZ_SEED` | 1337 (fixed) | n/a |
The run budget is shared: with N endpoints each gets `min(APITEST_FUZZ_CASES, budget / N)` cases (at least 1), so late endpoints are not starved by early ones, and a lower `APITEST_FUZZ_CASES` is always honored. The whole hostile pass needs up to 19 cases (20 with the margin); with fewer per endpoint its tail is cut and a warning names the `APITEST_FUZZ_MAX_REQUESTS` that would fit it (hard cap 5000, so about 250 endpoints at most; above that, fuzz a subset). Write operations (POST, PUT, PATCH, DELETE) run before reads. Details: the first cases of every endpoint are the hostile inputs: a NUL byte in each body field (up to 6) and
query parameter (up to 3), then very long, lone surrogate, control characters, emoji, right-to-left, negative and huge numbers, empty array and object, and null,
one per field in rotation. The request budget is per process: parallel runs (pytest-xdist) multiply it, so run fuzz tests serially. Non-finite values (`nan`, `inf`) fall back to the default.
Writes (POST/PUT/PATCH) run on any gated target; DELETE also needs `APITEST_ALLOW_DESTRUCTIVE=yes`. The fixed seed makes a rerun send the same requests;
vary the seed for more coverage. Findings are deduplicated per kind, status and case class.

**Budget report and choosing endpoints.** At the end of the run a "Fuzz budget" section prints the requests sent, how many endpoints got any, the min, median and max per endpoint, the endpoints that got none because the run budget was used up, and those cut off part-way. The same section appears for GraphQL suites. `APITEST_FUZZ_ENDPOINTS=id1,id2` fuzzes only those operation ids (GraphQL: operation names); the others skip with that reason. The run budget is split among the named ones only, and a name that matches no fuzz test is listed in the summary, so a typo is visible.

## Report
Per endpoint: cases sent, findings with severity and repro, the seed. A clean run means "no finding in N bounded cases", not "secure": say so.
