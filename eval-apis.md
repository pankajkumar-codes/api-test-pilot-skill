# Eval APIs

Use public or self-hosted APIs you are allowed to test. For each, record the bugs the
skill is expected to find, then compare with what it actually finds.

| API | Spec | Notes | Expected findings |
|-----|------|-------|-------------------|
| Swagger Petstore (self-host via Docker) | OpenAPI 3 | Standard reference API | A faithful implementation should give no HIGH finding. Mock: noauth, noval, badid, trace, crash seeded and found. Real 1.0.29: run once on 2026-11-27, 6 HIGH checked by hand (4 credential checks the reference server skips by design, 2 crashes on records the suite wrote), see docs/evals.md |
| A deliberately vulnerable API (e.g. crAPI, VAmPI — self-hosted) | OpenAPI | Known OWASP API flaws | BOLA, mass assignment, injection (VAmPI also: password change, debug data exposure, user enumeration, no rate limit). Mock: 7 of 8 found, mass assignment is a known gap. Real VAmPI: run once on 2026-11-27, same gap (mass assignment missed), see docs/evals.md. crAPI: not run |
| A small custom FastAPI app with seeded bugs (`evals/targets/seeded_fastapi.py`) | OpenAPI | You control the bugs | Off-by-one, missing validation, wrong status codes (also BOLA, no auth). **Run for real, locally: 5 of 5 found, clean variant clean** |
| A self-hosted GraphQL API with seeded flaws (e.g. DVGA — Damn Vulnerable GraphQL Application) | Introspection | GraphQL-specific flaws | Depth/batching abuse, introspection exposed, field-level authz. Mock (shop schema, not DVGA): 10 of 10 found. Real DVGA 2.2.0: run once on 2026-11-27, see docs/evals.md |

Rule: load/stress/chaos evals run only against self-hosted instances.

How to run these (mock mode and real mode) and where the results are recorded: `evals/README.md` and `docs/evals.md`. Mock results are always labelled "mock-based, real target not run".
