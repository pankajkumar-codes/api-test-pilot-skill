# PLAN

Status legend: [ ] todo · [~] in progress · [x] done

## Phase 0 — Scope and stack
- [x] Confirm v1 scope: REST + OpenAPI 3.x, plus GraphQL (added in Phase 2). gRPC and hosted service later
- [x] Choose output stack (default: pytest + requests/httpx for functional, k6 for load)
- [x] Choose license and skill name for marketplaces
- [x] Record choices in DECISIONS.md

## Phase 1 — Skeleton and intake
- [x] SKILL.md router: intake → plan → generate → run → report
- [x] scripts/parse_openapi.py: endpoints, methods, params, schemas, auth → JSON (+ tests/)
- [x] Support Postman collection and curl as secondary inputs (parse_postman.py, parse_curl.py, intake.py)
- [x] templates/test-plan.md
- [ ] Phase 1 eval: run intake.py on the Petstore spec (eval-apis.md); pending, needs network access. Fixture tests pass.

## Phase 2 — Functional modules (built; real eval-apis runs pending, see docs/evals.md)
- [x] references/functional.md: positive, negative, edge
- [x] references/integration.md: multi-endpoint flows (create → read → update → delete)
- [x] references/contract.md: response vs schema, breaking-change detection (spec_diff.py)
- [x] references/security.md: authN/authZ, BOLA/IDOR, injection, mass assignment (OWASP API Top 10)
- [x] references/data-validation.md and resilience.md (idempotency, retries, pagination, rate limits)
- [x] Generators that emit runnable pytest files (gen_pytest.py; validated against local mock servers, see docs/evals.md)
- [x] GraphQL support (see below)

### GraphQL (Phase 2)
- [x] scripts/parse_graphql.py: introspection query or SDL → queries, mutations, types, args (JSON)
- [x] references/graphql.md: positive/negative/edge adapted to GraphQL (errors arrive as 200 + `errors` array, so assert on the body, not just status)
- [x] GraphQL security checks: query depth/complexity limits, introspection exposure in prod, batching/alias abuse, field-level authorization, injection in variables
- [~] Reuse shared modules where possible: auth, redaction, safety gate and runtime reused (done); load (k6) and reporting reuse follow in Phases 3 and 4 via the shared intake endpoint shape
- [ ] Add a GraphQL API to eval-apis.md and validate (Phase 5)

## Phase 3 — Non-functional modules
- [ ] references/load.md, stress.md, spike.md, soak.md → k6 scripts
- [ ] references/chaos.md → fault-injection plans (latency, 5xx, timeouts, dependency down)
- [ ] references/fuzzing.md
- [ ] Safety gate wired into every non-functional flow

## Phase 4 — Reporting
- [ ] templates/report.md: summary, per-endpoint coverage, severity-ranked findings
- [ ] scripts/summarize_results.py (pytest + k6 output → report)
- [ ] Every finding includes a reproducible command

## Phase 5 — Evals
- [ ] Run against each API in eval-apis.md; record expected vs found bugs
- [ ] Iterate on prompts/references until expected bugs are found
- [ ] Add results to docs/evals.md

## Phase 6 — Package and publish
- [ ] README with install, usage, examples, screenshots
- [ ] LICENSE, CONTRIBUTING, CHANGELOG
- [ ] Validate SKILL.md frontmatter and folder layout
- [ ] Push to GitHub, tag v0.1.0
- [ ] Submit to Claude and other skill marketplaces

## Out of scope for v1
UI accessibility testing (separate skill), mobile, gRPC (v2), hosted service (revisit only if the skill gains traction).
