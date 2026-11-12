# Eval APIs

Use public or self-hosted APIs you are allowed to test. For each, record the bugs the
skill is expected to find, then compare with what it actually finds.

| API | Spec | Notes | Expected findings |
|-----|------|-------|-------------------|
| Swagger Petstore (self-host via Docker) | OpenAPI 3 | Standard reference API | TBD in Phase 5 |
| A deliberately vulnerable API (e.g. crAPI, VAmPI — self-hosted) | OpenAPI | Known OWASP API flaws | BOLA, mass assignment, injection |
| A small custom FastAPI app with seeded bugs | OpenAPI | You control the bugs | Off-by-one, missing validation, wrong status codes |

| A self-hosted GraphQL API with seeded flaws (e.g. DVGA — Damn Vulnerable GraphQL Application) | Introspection | GraphQL-specific flaws | Depth/batching abuse, introspection exposed, field-level authz |

Rule: load/stress/chaos evals run only against self-hosted instances.
