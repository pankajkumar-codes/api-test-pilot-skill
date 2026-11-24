# Test types (copy of TAXONOMY.md for installed skills; keep in sync)

| # | Type | What it checks | Output |
|---|------|----------------|--------|
| 1 | Smoke | Key endpoints reachable and return 2xx | pytest file |
| 2 | Positive | Valid input → expected status, body, headers | pytest |
| 3 | Negative | Missing credentials, unknown ids, malformed JSON/content type → correct 4xx and error shape | pytest |
| 4 | Edge | Boundaries, empty/huge payloads, unicode, null, special characters (never a 5xx) | pytest |
| 5 | Integration | Multi-endpoint flows, state across calls, dependencies | pytest |
| 6 | Contract | Responses match OpenAPI schema; no breaking changes between versions | pytest / diff report |
| 7 | Security | AuthN/authZ, BOLA/IDOR, injection, mass assignment, rate limiting, sensitive data exposure | pytest + findings |
| 8 | Data validation | One violation per case (type, format, enum, length, range, required) → 4xx | pytest |
| 9 | Resilience | Timeouts, retries, idempotency, pagination, rate-limit behaviour | pytest |
| 10 | Regression | Re-runnable full suite and the exact rerun command (no baseline comparison between runs yet) | suite + report |
| 11 | Load | Expected traffic: latency percentiles, error rate | k6 script |
| 12 | Stress | Beyond capacity: breaking point, recovery | k6 script |
| 13 | Spike | Sudden surge and recovery | k6 script |
| 14 | Soak | Long run: leaks, degradation | k6 script |
| 15 | Chaos | Dependency failure, latency injection, partial outages | fault-injection plan + `chaos_run.py` experiments (JUnit/JSON) |
| 16 | Fuzzing | Randomized malformed input; crashes, 5xx, leaks | script + findings |
| 17 | Versioning / compatibility | Old clients still work on new versions | diff report (`scripts/spec_diff.py`) |
| 18 | Error-message quality / i18n | Error bodies match the documented error schema and leak no internals; unicode round-trips. No localization checks yet | pytest (inside negative, edge and security) |

Protocols: REST (OpenAPI) and GraphQL; every type above applies to both unless noted (GraphQL specifics in `references/graphql.md`). gRPC unary calls
get smoke, positive, negative, edge, contract and security tests (`references/grpc.md`).

Accessibility: not applicable to APIs (UI concern). Tracked as a future separate skill.

Types 11–16 are gated by the safety gate (see SKILL.md step 3).
