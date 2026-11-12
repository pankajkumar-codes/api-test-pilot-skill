# Fuzzing (REST)

**Safety gate first.** Fuzzing sends hostile and malformed input, including to write endpoints. Only against a non-production target the user owns or is
authorized to test; production is never fuzzed. Fuzz tests carry the `intrusive` marker: the generated `conftest.py` skips them unless the target is local, or
`APITEST_TARGET_ENV` is a non-production value **and** `APITEST_AUTHORIZED=yes`. Never set these yourself: ask the user.

## Generate and run
```
python scripts/gen_pytest.py intake.json -o fuzz_tests --types fuzz       # opt-in: not part of --types all
APITEST_BASE_URL=http://localhost:8080 APITEST_BEARERAUTH_TOKEN=... python -m pytest fuzz_tests -v -ra
```
GraphQL schemas are not fuzzed in v1 (the generator says so); use the GraphQL security checks in `graphql.md`. Schemathesis is a heavier alternative for
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
| `APITEST_FUZZ_CASES` per endpoint | 40 | 500 |
| `APITEST_FUZZ_RPS` | 5 | 20 |
| `APITEST_FUZZ_MAX_REQUESTS` per run | 2000 | 5000 |
| `APITEST_FUZZ_MAX_BYTES` per payload | 65536 | 1 MiB |
| `APITEST_FUZZ_SEED` | 1337 (fixed) | n/a |
The request budget is per process: parallel runs (pytest-xdist) multiply it, so run fuzz tests serially. Non-finite values (`nan`, `inf`) fall back to the default.
Writes (POST/PUT/PATCH) run on any gated target; DELETE also needs `APITEST_ALLOW_DESTRUCTIVE=yes`. The fixed seed makes a rerun send the same requests;
vary the seed for more coverage. Findings are deduplicated per kind, status and case class.

## Report
Per endpoint: cases sent, findings with severity and repro, the seed. A clean run means "no finding in N bounded cases", not "secure": say so.
