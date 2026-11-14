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
- [x] Phase 1 eval: intake.py and gen_pytest.py ran on the real upstream Petstore, VAmPI and crAPI OpenAPI documents (fetched from GitHub raw; docs/evals.md Phase 5). The live Petstore server was not reachable.

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
- [x] Reuse shared modules where possible: auth, redaction, safety gate and runtime reused; k6 load (Phase 3) and reporting (Phase 4; GraphQL results arrive as ordinary pytest/k6 output) reuse the shared intake endpoint shape
- [~] GraphQL API in eval-apis.md validated against the mock stand-in (9 of 9); real DVGA run pending (Phase 5)

## Phase 3 — Non-functional modules
- [x] references/load.md, stress.md, spike.md, soak.md → k6 scripts (`gen_k6.py`, `run_k6.py`; REST and GraphQL queries)
- [x] references/chaos.md → fault-injection plans (latency, 5xx, timeouts, dependency down) via `gen_chaos_plan.py`; plans; executable experiments added in Phase 8 (`chaos_run.py`, `chaos_proxy.py`)
- [x] references/fuzzing.md (`--types fuzz` in `gen_pytest.py`, REST; GraphQL fuzzing deferred)
- [x] Safety gate wired into every non-functional flow (`safety_gate.py`, k6 script start-up gate, `intrusive` fuzz tests, chaos plan checklist)
- [~] Phase 3 eval: k6 scripts and fuzz suites validated against local mock servers (docs/evals.md); eval-apis.md targets still pending (Phase 5)

## Phase 4 — Reporting
- [x] templates/report.md: summary, per-endpoint coverage, severity-ranked findings
- [x] scripts/summarize_results.py (pytest JUnit XML + k6 `--summary-export` JSON → report; `references/reporting.md`)
- [x] Every finding includes a reproducible command (curl line from the runtime when present, exact pytest or `run_k6.py` command always)
- [~] Phase 4 eval: validated on real pytest and real k6 v1.3.0 output from the local mock servers (docs/evals.md); eval-apis.md targets still pending (Phase 5)

## Phase 5 — Evals
- [~] Run against each API in eval-apis.md; record expected vs found bugs (seeded FastAPI app run for real, locally; Petstore, VAmPI and GraphQL as labelled mock stand-ins; real third-party targets not reachable from the sandbox, run them with `evals/README.md`)
- [x] Iterate on prompts/references/generators until expected bugs are found (26 of 27 seeded flaws found; VAmPI mass assignment is a documented gap)
- [x] Add results to docs/evals.md
- [ ] Person runs the real Petstore, VAmPI (vulnerable and `vulnerable=0`) and DVGA evals on their machine and fills the "found" column (`evals/README.md`)

## Phase 6 — Package and publish
Preparation is done on a branch; the outward-facing steps need the maintainer (see docs/RELEASE.md).
- [x] README with install, usage, safety, requirements and examples (real terminal output and a real generated report; no screenshots because the skill has no UI)
- [x] LICENSE (MIT), CONTRIBUTING, CHANGELOG
- [x] Validate SKILL.md frontmatter and folder layout (`tools/validate_skill.py`, tests in `tests/test_packaging.py`; also checks plugin files and version consistency)
- [x] Packaging: Claude Code plugin + marketplace manifests (`claude plugin validate` passes; local install tried), `tools/build_skill_zip.py` for the Claude app upload
- [x] docs/RELEASE.md checklist and docs/LISTING.md marketplace copy
- [x] Phase 5 leftovers: remote target without APITEST_TARGET_ENV stops the run (exit 4) with a test; `destructive` marker; xdist note; warning when most tests skip
- [ ] Merge, make the repository public, tag v0.1.0 (maintainer)
- [ ] Submit to Claude and other skill marketplaces (maintainer; copy in docs/LISTING.md)

## Phase 7 — Production-readiness pass (before the public launch)
- [x] Nits: CHANGELOG reference count, README test timing, `run_evals.py --real` refusal prints one message and exits 2
- [x] Tests on Python 3.11, 3.12 and 3.13 plus the lowest supported dependency versions; fixed a RecursionError on 3.11
- [x] Hostile-input pass: over-nested JSON, binary and non-intake files give a clean error (exit 2), not a traceback
- [x] SECURITY.md, issue and PR templates, GitHub Actions CI (3 Pythons, validator, ruff, optional k6), `ruff.toml`
- [x] README commands re-run from a clean checkout and the shipped zip in a fresh venv; history scanned for secrets (clean)
- [ ] CI green on GitHub (first run happens on the pull request)

## Out of scope for v1
gRPC is deferred to v2. Not part of this skill at all (see DECISIONS.md, 2026-11-14): UI accessibility testing (a possible separate skill), mobile app testing, a hosted service.

## Phase 8 — Executable chaos and skill gaps (2026-11-13)
- [x] DECISIONS.md: chaos is no longer plan-only (overrides the Phase 0 decision)
- [x] `scripts/chaos_proxy.py` (HTTP and TCP fault proxies, loopback only) and `scripts/chaos_run.py` (runner, hooks, payload bursts, recovery time, JUnit + JSON)
- [x] Reporting understands chaos results (`summarize_results.py`: type `chaos`, intrusive note, rerun command)
- [x] Gaps: guessed required body fields flagged, integration flows skip when the create response has no id, undeclared auth parameters inferred, `APITEST_READ_ONLY` public-sandbox mode, PR #8 nits (the destructive marker and xdist note were already in)
- [x] Tests (`tests/test_chaos.py`) and a live run against the spring-boot-demo with a throwaway Postgres (docs/evals.md)
- [x] Eval follow-ups: name-based sample values, no echo-endpoint flows, inferred-auth skip hint, chaos exit code 4 on signal, URL-path secret note
