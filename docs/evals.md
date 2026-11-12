# Evals

## Phase 2: generator validation against local mock servers (2026-11-12)

The targets in `eval-apis.md` (Petstore, crAPI/VAmPI, DVGA, a seeded FastAPI app) are not reachable from the build
sandbox, so Phase 2 was validated against stdlib mock servers that ship in `tests/`:

- `tests/mock_rest.py` + `tests/fixtures/mockapi.yaml` (pets, toys, admin, preview/SSRF endpoints; bearer auth)
- `tests/mock_graphql.py` + `tests/fixtures/shop.graphql` (queries, mutations, recursive types, sensitive fields)

For each server the **clean** variant must pass every generated test that is not skipped by a documented gate, and each
**seeded bug** must make a specific generated test fail (`tests/test_generators.py`, runs in CI with `python -m pytest tests`).

| Seeded flaw (server option) | Expected finding | Generated test that must fail |
|---|---|---|
| `bola` | BOLA | `test_api1_bola_getPet` |
| `noauth` | no authentication | `test_listPets_rejects_missing_credentials` |
| `leak` | excessive data exposure | `test_api3_no_sensitive_fields_listPets` |
| `contract` | response differs from schema | `test_listPets_response_contract` (and both integration flows) |
| `mass` | mass assignment | `test_api3_mass_assignment_createPet` |
| `noval` | missing validation | `test_createPet_rejects_invalid_input[name_above_max_length]` |
| `crash` | 5xx + DB error on `'` | `test_injection_createPet[body_name[0]]` |
| `stacktrace` | stack trace in errors | `test_api8_errors_do_not_leak_internals` |
| `admin` | BFLA | `test_api5_bfla_adminStats` |
| `headers` | missing hardening headers | `test_api8_security_headers` |
| `cors` | CORS reflects origin + credentials | `test_api8_cors_not_permissive` |
| `ssrf` | internal URL accepted | `test_api7_ssrf_preview_url[...]` |
| `paging` | page size ignored | `test_listPets_limit_one_returns_at_most_one` |
| `rate_limit` without `Retry-After` | bad throttle response | `test_rate_limit_behaviour` |
| GraphQL `introspection`, `nodepth`, `noalias`, `batching`, `fieldauth`, `bola`, `suggest`, `verbose`, `crash` | introspection on, no depth/alias/batch limit, field-level authz missing, BOLA, schema suggestions, verbose errors, injection crash | `test_introspection_disabled`, `test_query_depth_is_limited`, `test_alias_abuse_is_limited`, `test_batching_is_disabled_or_limited`, `test_field_authz_User_email`, `test_bola_pet`, `test_error_messages_do_not_suggest_schema_names`, `test_errors_do_not_leak_internals`, `test_injection_pets[search[0]]` |

Also verified: the safety gate matrix (production blocks intrusive tests even on a local host, remote hosts need
`APITEST_TARGET_ENV` and `APITEST_AUTHORIZED`), no DELETE against existing data without `APITEST_ALLOW_DESTRUCTIVE`,
no secret canaries in generated files or failure output, redacted curl reproductions, 3.11-compatible syntax of scripts and generated files.

Not verified yet (Phase 2): the `eval-apis.md` targets (Phase 5), real-world specs with large schemas/polymorphism, OAuth2 token
acquisition (the suite reads a token from env), k6 reuse of the GraphQL endpoint shape (Phase 3), multipart/XML bodies.
Findings against real APIs may include false positives (for example a public API that accepts any URL parameter); every
finding names the check so the plan can mark accepted behaviour.

## Phase 3: k6, fuzzing and chaos plans against local mock servers (2026-11-12)

Same constraint as Phase 2: the `eval-apis.md` targets are not reachable from the build sandbox, and load/stress/chaos evals must only
target self-hosted instances anyway. Validation used the stdlib mock servers in `tests/` and **real k6 v1.3.0** (official release binary,
SHA-256 checked against the published checksums file). Tests that need k6 skip with an explicit reason when it is missing
(`K6_BIN` or PATH); CI without k6 still runs generation, static, gate and fuzz tests.

| Area | What was run | Result |
|------|--------------|--------|
| k6 load, stress (2 steps), spike, soak (quick shapes) | generated scripts executed by k6 against `mock_rest.py` (clean) | exit 0, only GET sent, no admin or write calls, per-stage metrics present in `--summary-export` |
| Circuit breaker | `mock_rest.py` with `down` (every call 500), planned 3 min run | k6 exits 99 after about 20 s, far fewer requests than planned |
| Gate | `run_k6.py` and `k6 run` directly, production/unconfirmed/odd hosts | refused before any request; mock log empty |
| Gate parity | 46 URLs x 10 env combinations | JavaScript gate (node) identical to `safety_gate.py`; never looser than the Phase 2 pytest gate |
| Caps | rates 1..100, all profiles; hand-built steep ramps; env caps | refused or inside caps; run-time caps can only lower |
| Writes | `--include-writes` with and without `APITEST_ALLOW_WRITES` | POST sent only with both opt-ins; DELETE never generated |
| GraphQL load | `mock_graphql.py`; canned server answering 200 + `errors` | clean run exit 0; errors-in-200 fails the checks threshold (exit 99) |
| Hostile specs | quotes, comments, U+2028, `__proto__`, template literals in ids, paths, params, schemes, servers (REST and GraphQL) | strings only appear inside the JSON literal; unusual names/paths dropped or skipped; `node --check` and `k6 inspect` pass |
| Fuzzing | `mock_rest.py` clean vs seeded bugs | clean passes; `crash`, `jsoncrash`, `hang`, `crlf` each fail `test_fuzz[createPet]` with severity and redacted repro |
| Fuzz limits | env values above the hard caps; budget of 5 requests | clamped; at most 5 requests sent |
| Chaos plans | structure, hostile free text, production refusal | no code blocks or commands; no spec free text copied; production refused |

The fuzzer found a real bug in the mock server on its first clean run (a bare JSON `null` body left the connection without a reply); fixed in `tests/mock_rest.py`.

Real k6 behaviour is exercised only by tests that need a k6 binary; CI without k6 skips them, so add a k6 install step to CI to keep them running.

Not verified: k6 against the `eval-apis.md` targets and any real network (Phase 5); k6 other than v1.3.0 on Linux x86-64; runs longer than a few
minutes (the full-length shapes were checked by construction and by the `--quick` shapes, not run end to end); chaos experiments (v1 produces plans only);
GraphQL fuzzing (not built); WebSocket/gRPC load.
