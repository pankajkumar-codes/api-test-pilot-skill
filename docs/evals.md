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

Not verified yet: the `eval-apis.md` targets (Phase 5), real-world specs with large schemas/polymorphism, OAuth2 token
acquisition (the suite reads a token from env), k6 reuse of the GraphQL endpoint shape (Phase 3), multipart/XML bodies.
Findings against real APIs may include false positives (for example a public API that accepts any URL parameter); every
finding names the check so the plan can mark accepted behaviour.
