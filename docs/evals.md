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

Who ran what (PR 3): the PR 3 reviewer did **not** run k6 (no binary in the review environment, so the 13 real-k6 tests skipped there); the author did, with k6 v1.3.0, and all 13 real-k6 tests passed. Treat the real-k6 results as author-verified, not independently re-run.

Not verified: k6 against the `eval-apis.md` targets and any real network (Phase 5); k6 other than v1.3.0 on Linux x86-64; runs longer than a few
minutes (the full-length shapes were checked by construction and by the `--quick` shapes, not run end to end); chaos experiments against real third-party targets (see the Phase 8 section for the local run);
GraphQL fuzzing (not built); WebSocket/gRPC load.

## Phase 4: reporting on real pytest and k6 output (2026-11-12)

The report script was exercised on output produced by real tools, not hand-written fixtures: the generated REST suite run by real pytest against
`tests/mock_rest.py` (clean, and with seeded bugs `bola, mass, leak, contract, noauth, cors, stacktrace`), the seeded `crash` bug through the real fuzz
suite, and generated k6 scripts run by **real k6 v1.3.0** (same checksum-verified binary as Phase 3) against the clean mock and against `down`
(every call 500). `eval-apis.md` targets are still out of reach of the build sandbox (Phase 5).

| Area | What was run | Result |
|------|--------------|--------|
| Clean pytest run | 254 generated tests, healthy mock | PASS report, no findings, 19 skips listed with reasons |
| Seeded bugs | same suite, 7 seeded bugs | 46 failing tests = 46 findings, HIGH first; BOLA on getPet, mass assignment on createPet, contract violation on listPets all present; every finding has curl + pytest commands; the first finding's pytest command re-run on the same server fails again |
| Fuzz | `--types fuzz` against `crash` | HIGH finding per endpoint with all distinct repro lines |
| k6 healthy | load, spike, stress (quick) | no findings; stage and per-endpoint tables present |
| k6 `down` | load, exit 99 | `checks` and `http_req_failed` thresholds reported HIGH with observed values; confirmed that `true` in `--summary-export` means crossed |
| Canary secrets | tokens, passwords, cookies, JWTs, userinfo, query params, `curl -u`, headers and env values placed in message, text, names, skip reasons, system-out, k6 names, intake and `--title` | none reach the report (the regenerate command leaked a `--title` secret on the first run; fixed by scrubbing it) |
| Hostile XML | XXE (file and http), billion laughs, parameter entities, DOCTYPE hidden in UTF-16, undefined entities, depth, size, case count, FIFO, `/dev/zero` | refused with a clean error before any expansion; nothing read from the file system |
| Markdown | link/image/HTML injection, fence break-outs, table rows, headings, `{{placeholders}}`, bidi/zero-width/control characters, autolinks | everything outside fenced blocks is escaped; headings are only the template's; placeholders are not re-substituted |
| Redaction speed | 200 KB pathological inputs (`a=a=a=...`, backslash runs, long values) | linear; a first version was quadratic on `a=a=...` (24 s) and was rewritten |

Real-k6 tests skip with a reason when no k6 binary exists (`K6_BIN` or PATH), as in Phase 3; with k6 present `tests/test_reporting.py` runs 86 tests.

Not verified: k6 versions other than v1.3.0 and platforms other than Linux x86-64 (the `--summary-export` layout is what v1.3.0 writes; later k6 versions may
change it, and the script fails with a clear error when `metrics` is missing); JUnit from runners other than pytest; GraphQL results beyond what the shared
pytest/k6 shapes carry; the report rendered in a real Markdown renderer (escaping is checked structurally with a CommonMark fence/heading check, not by
rendering); `eval-apis.md` targets and any real network (Phase 5).

## Phase 5: evals against the eval-apis.md targets (2026-11-12)

**Status in one paragraph.** None of the real third-party targets could be run from the build sandbox, so nothing below says how the real
Swagger Petstore, VAmPI, crAPI or DVGA behave. What was run: (1) the real upstream OpenAPI documents (fetched from GitHub raw, which was reachable)
through intake and generation, (2) stand-in mock servers built from those documents, (3) one **real local server**, the seeded FastAPI app. Every table
says which of the three it is. The commands to run the real targets on your own machine are in [`evals/README.md`](../evals/README.md).

### Reachability re-check (13:49 UTC, through the configured proxy, TLS verification on)
| Check | Result |
|---|---|
| `petstore3.swagger.io`, `petstore.swagger.io` | no connection (HTTP status 000), same as earlier phases |
| `github.com` pages (DVGA repo page, swagger-petstore releases) | 403 from the proxy |
| `raw.githubusercontent.com` (Petstore, VAmPI, crAPI specs, DVGA/VAmPI READMEs) | 200 |
| `pypi.org` | 200 (fastapi, uvicorn, graphql-core installed) |
| `hub.docker.com` metadata API | 200 (images `swaggerapi/petstore3`, `erev0s/vampi`, `dolevf/dvga` exist); `registry-1.docker.io` answers 401 (reachable) |
| Docker daemon | not running in the sandbox, so no container could be started |

### Real upstream specs through intake and generation (static, no target contacted)
| Spec | Intake | Generated suite |
|---|---|---|
| swagger-petstore `openapi.yaml` (OpenAPI 3.0.4) | 19 endpoints, 2 auth schemes (oauth2, api_key) | 142 tests; notes that the octet-stream upload body is not generated |
| VAmPI `openapi3.yml` (3.0.1) | 14 endpoints, bearer | 123 tests |
| crAPI `crapi-openapi-spec.json` (3.0.1, 44 endpoints) | 44 endpoints, bearer | 389 tests; notes that multipart bodies are not generated |
crAPI was only taken through intake and generation (its spec is not vendored and no crAPI mock exists); it was never run against anything.

### How a mock run is scored
`python evals/run_evals.py --target all` starts the stand-in once per variant: **each seeded flaw alone**, plus a **clean** variant. A flaw counts as
found when one of its listed generated tests fails (the "Expected" column is the test-name pattern, written before the run was scored). The clean variant must
have no failing test, apart from listed known limitations. The expected-test lists were written by the same people who wrote the generators and the
mocks, so a "found" here means the check works on that flaw, not that it would find the same flaw in software we did not write.

### Results
**Total: 26 of 27 seeded flaws found, 1 documented gap. 4 clean variants: 3 with zero failures, 1 with one listed limitation.** Whole run: about two minutes on 4 cores.
(Full run through `python evals/run_evals.py --target all --markdown`; the tables below are its output.)

#### Swagger Petstore 3 (mock of the documented behaviour) (mock-based, real target not run)

| Seeded flaw | Class | Expected: failing test matches | Found | Matched test |
|---|---|---|---|---|
| `noauth`: protected routes answer without credentials | API2 | `rejects_missing_credentials`, `invalid_credentials_rejected` | yes | `test_negative::test_addPet_rejects_missing_credentials` |
| `noval`: invalid pet bodies and enum values accepted | API3/API8 | `test_addPet_rejects_invalid_input\[missing_name\]`, `test_findPetsByStatus_rejects_invalid_input\[query_status_not_in_enum\]` | yes | `test_data_validation::test_addPet_rejects_invalid_input[missing_name]` |
| `badid`: non-numeric id is a 500, unknown pet is a 200 | API8 | `test_getPetById_unknown_id_not_found`, `test_getPetById_rejects_invalid_input\[path_petId_wrong_type\]` | yes | `test_data_validation::test_getPetById_rejects_invalid_input[path_petId_wrong_type]` |
| `trace`: stack trace in error bodies | API8 | `test_api8_errors_do_not_leak_internals` | yes | `test_security::test_api8_errors_do_not_leak_internals` |
| `crash`: quote in a name causes a database error (500) | API8/injection | `test_injection_addPet\[body_name\[0\]\]` | yes | `test_security::test_injection_addPet[body_name[0]]` |

Clean variant: 326 passed, 0 failed, 7 skipped


#### VAmPI (mock of the documented vulnerabilities) (mock-based, real target not run)

| Seeded flaw | Class | Expected: failing test matches | Found | Matched test |
|---|---|---|---|---|
| `sqli`: SQL injection through the username path parameter | API8/injection | `test_injection_.*get_by_username` | yes | `test_security::test_injection_api_views_users_get_by_username[path_username[3]]` |
| `bola`: books of other users readable | API1 (BOLA) | `test_api1_bola_.*get_by_title` | yes | `test_security::test_api1_bola_api_views_books_get_by_title` |
| `pwchange`: any user can change another user's password | API1/API5 | `test_api1_bola_.*update_password`, `test_api5_.*update_password` | yes | `test_security::test_api1_bola_api_views_users_update_password` |
| `mass`: `admin` accepted at registration | API3 (mass assignment) | `test_api3_mass_assignment_.*register_user` | **NO** (known gap) | `-` |
| `debug`: debug endpoint lists passwords | API3 (excessive data exposure) | `test_api3_no_sensitive_fields_.*debug` | yes | `test_security::test_api3_no_sensitive_fields_api_views_users_debug` |
| `enum`: login reveals whether the user exists | API2 | `test_api2_login_does_not_reveal_valid_usernames` | yes | `test_security::test_api2_login_does_not_reveal_valid_usernames_api_views_users_login_user` |
| `norate`: login is never throttled | API4 | `test_api4_rate_limiting_present` | yes | `test_security::test_api4_rate_limiting_present` |
| `noauthdel`: non-admin can delete users | API5 (BFLA) | `test_api5_bfla_.*delete_user` | yes | `test_security::test_api5_bfla_api_views_users_delete_user` |

Known gaps (flaw not detected today):
- `mass`: the register response does not echo the stored fields and no documented read endpoint exposes `admin`; the skill checks the write response only (no read-back of the created record)

Clean variant: 230 passed, 1 failed, 12 skipped
- known limitation, fails on the clean target: `test_api_views_users_login_user_response_contract` (login needs real credentials; the suite never sends spec-example passwords, so its success-path contract check gets a 400)


#### Seeded FastAPI app (REAL server, local) (real local server (self-hosted, seeded by us))

| Seeded flaw | Class | Expected: failing test matches | Found | Matched test |
|---|---|---|---|---|
| `offbyone`: limit=N returns N+1 items | logic | `test_listItems_limit_one_returns_at_most_one` | yes | `test_resilience::test_listItems_limit_one_returns_at_most_one` |
| `noval`: documented limits not enforced on create | API3/API8 | `test_createItem_rejects_invalid_input\[name_above_max_length\]`, `test_createItem_rejects_invalid_input\[price_below_minimum\]` | yes | `test_data_validation::test_createItem_rejects_invalid_input[name_above_max_length]` |
| `status`: wrong status codes (201 documented, 200 sent; missing delete is 200, not 404) | contract | `test_createItem_response_contract`, `test_deleteItem_unknown_id_not_found` | yes | `test_contract::test_createItem_response_contract` |
| `bola`: items of other users readable | API1 (BOLA) | `test_api1_bola_getItem` | yes | `test_security::test_api1_bola_getItem` |
| `noauth`: no authentication | API2 | `rejects_missing_credentials` | yes | `test_negative::test_createItem_rejects_missing_credentials` |

Clean variant: 91 passed, 0 failed, 5 skipped


#### GraphQL shop API (mock; DVGA stand-in) (mock-based, real target not run)

| Seeded flaw | Class | Expected: failing test matches | Found | Matched test |
|---|---|---|---|---|
| `introspection`: introspection enabled | GraphQL | `test_introspection_disabled` | yes | `test_graphql_security::test_introspection_disabled` |
| `nodepth`: no query depth limit | GraphQL | `test_query_depth_is_limited` | yes | `test_graphql_security::test_query_depth_is_limited` |
| `noalias`: no alias/field-count limit | GraphQL | `test_alias_abuse_is_limited` | yes | `test_graphql_security::test_alias_abuse_is_limited` |
| `batching`: array batching allowed | GraphQL | `test_batching_is_disabled_or_limited` | yes | `test_graphql_security::test_batching_is_disabled_or_limited` |
| `fieldauth`: field-level authorization missing | API5/GraphQL | `test_field_authz_User_email` | yes | `test_graphql_security::test_field_authz_User_email` |
| `bola`: object-level authorization missing | API1 (BOLA) | `test_bola_pet` | yes | `test_graphql_security::test_bola_pet` |
| `suggest`: schema suggestions in errors | GraphQL | `test_error_messages_do_not_suggest_schema_names` | yes | `test_graphql_security::test_error_messages_do_not_suggest_schema_names` |
| `verbose`: stack traces in errors | API8 | `test_errors_do_not_leak_internals` | yes | `test_graphql_security::test_errors_do_not_leak_internals` |
| `crash`: injection crashes the resolver | injection | `test_injection_pets` | yes | `test_graphql_security::test_injection_pets[search[0]]` |

Clean variant: 63 passed, 0 failed, 5 skipped

### What the evals changed in the skill (found by running, fixed, covered by tests in `tests/test_evals.py`)
| Finding | Cause | Fix |
|---|---|---|
| Clean Petstore: later tests got 404 for ids an earlier DELETE test had consumed | alphabetical test order ran destructive tests before smoke/security | generated `conftest.py` runs destructive tests last, BOLA/BFLA probes first among them (a BFLA probe that runs after the account deleted itself sees a 401 and passes by accident: seen on VAmPI `noauthdel`) |
| `PUT /pet` positive and idempotency tests got 404 | the baseline body had no `id`, so the update targeted nothing | PUT/PATCH baseline bodies include an optional `id` that has a spec example |
| Repeated create tests failed with 400/409 "already exists" | every create test sends the same baseline body | skipped with a reason (duplicate create), never a verdict; documented in `references/functional.md` |
| Login/other calls failed with 429 once a burst test had run | throttling is a property of the target, not a defect | a 429 where another status was expected is a skip with a reason; an expected 429 still passes |
| VAmPI `DELETE /users/{username}` ("Only Admins" in the summary) not treated as admin-only | admin detection looked at path and tags only | the summary and description are matched as well (`Only Admins`, `admin only`, `requires admin`) |
| The unknown-id negative test ran for admin-only endpoints with ordinary credentials | missing admin guard | same guard as the other success-path tests |
| User enumeration (VAmPI `enum`) not checked | no such test | new `test_api2_login_does_not_reveal_valid_usernames_*` for login endpoints: two failed logins (existing vs unknown name) must look alike; needs `APITEST_KNOWN_USERNAME`; intrusive, so gated |
| SQL injection in a path parameter (VAmPI `sqli`) not found | path payloads had no quote | added an encoded `x' OR '1'='1` path payload |
| First run on the real FastAPI target flagged missing `X-Content-Type-Options` | true finding on our own app | the clean app now sets it (the check works; the "clean" baseline needed it) |

Not changed on purpose: positive/contract tests for login endpoints need real credentials in the body (the suite never sends spec-example passwords),
so the success-path contract test of a login endpoint fails on a target that is otherwise clean (listed as a known limitation of the VAmPI clean variant).

### Known gaps and not verified
- **VAmPI mass assignment is not found.** The generated test checks the write response only; VAmPI-style APIs answer a register call with a message and expose the
  stored `admin` flag nowhere the spec documents. Detecting it needs a read-back of the created record, which is not built (candidate for v1.1).
- The Petstore and VAmPI mocks follow the *documents*, not necessarily the real servers (for example the document declares `GET /books/v1/{title}` as an array; whether the real
  app returns an array was not checked). Where the real server differs, the first real run will show contract findings that are the real server's, not the skill's.
- Not run at all: real Petstore, real VAmPI, real crAPI (no mock either), real DVGA. The GraphQL mock is the generic shop schema from `tests/`, not DVGA's schema; DVGA scenarios
  the skill does not cover are listed in `evals/README.md`.
- GraphQL real mode relies on `evals/dump_introspection.py`; its query was checked against `graphql-core` (a real GraphQL engine) and the result was read by `intake.py`, but not against DVGA.
- Real mode (`--real --base-url`) was exercised only against a local mock started by hand (path runs, report written, no scoring); no real third-party target was used.
- k6 was not installed in this sandbox: the 17 real-k6 tests skip here (full suite: 259 passed, 17 skipped). The Phase 3/4 notes about real-k6 runs still stand; nothing in Phase 5 touches k6.
- Load, stress, spike, soak, chaos and fuzz were not part of the Phase 5 evals (they are gated and need self-hosted targets); fuzzing the seeded app and the mocks was covered in Phase 3.
- No eval target is fuzzed or load-tested in `run_evals.py`: it only runs the functional, contract, security, data-validation, integration and resilience suites.

## Phase 8: executable chaos on a real local app (2026-11-13)
Target: `pankajkumar-codes/spring-boot-demo` (Spring Boot 4.1, `e4544f8`) run from its jar on `localhost:8080`, against a **throwaway PostgreSQL 16** started for the run
(`initdb` in a temp directory, port 5332; nothing else was touched). Spec: the five-endpoint `openapi.json` from the earlier local run. Everything ran locally; no third-party host was contacted.
Probe: `GET /api/v1/software-engineers` (reads the database), 5 req/s.

| Experiment | What ran | Result |
|---|---|---|
| `proxy-latency` (+250, +1000, +3000 ms), `proxy-5xx` (50 %), `proxy-drop` (abort and reset, 50 %) | local HTTP fault proxy in front of the app | pass: no 5xx from the app itself, recovered in under 1 s each time |
| `payload` (POST, 10 requests per case) | oversized 256 KiB, malformed JSON, 800-deep nesting, `text/plain`, truncated body | pass: 400, 400, 400, 415, connection closed cleanly; the app answered health probes afterwards |
| `dep-outage` | PostgreSQL stopped with `pg_ctl stop -m fast` for 8 s, then started (user-supplied commands via `--allow-hooks`) | pass: 8 of 12 probes failed during the outage, all within about 3 s (Hikari `connection-timeout=3000`), recovered 2.9 s after the database returned |
| `dep-reset` | TCP proxy between the app and PostgreSQL resets every connection | pass: recovered 3.2 s after the fault was removed |
| `dep-latency` (+2 s per chunk) | same proxy | pass: no failures, but p95 rose to 8.1 s (several round trips per query) |
| `dep-slow` | PostgreSQL frozen with SIGSTOP for 8 s, then SIGCONT | **finding (MEDIUM)**: failed requests took about 5.0 s (limit 5.0 s); the queries wait on a socket with no read timeout; recovered 0.8 s after SIGCONT |
| `dep-blackhole` | proxy swallows the bytes, connections stay open | **finding (MEDIUM)**: 8 of 8 probes failed after about 5.0 s; recovered 3.7 s after the fault was removed |

What the two findings mean: the app fails fast (about 3 s) when the database is *down* (connection refused) but takes about 5 s, and not 3, when the database is *hung or
silent*. The cause was not investigated beyond this run; a JDBC `socketTimeout` and a query timeout are the usual way to bound it. The limit (`--fail-fast-ms`, default 5000) is a default, not a standard,
and the 5.0 s observed sits right at it, so treat these two as "at the limit", not as a measured hang. Each fault was bounded, so unbounded hanging was not measured.
The chaos results were turned into a report with `summarize_results.py` (type `chaos`, two MEDIUM findings, each with the `chaos_run.py` rerun command).

Not verified: HTTPS upstreams (the proxy code supports them; no run), IPv6, chunked request bodies through the proxy (answered 411 by design), remote targets, Windows.
