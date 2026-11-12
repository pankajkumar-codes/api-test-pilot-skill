---
name: api-testing
description: Plan, generate and run API tests from an OpenAPI spec, GraphQL schema, Postman collection or curl examples. Covers functional (positive, negative, edge, integration), contract, security, load/stress/soak and chaos testing. Use when the user wants to test, audit or load-test an HTTP API.
---

# API Testing

## Workflow
1. **Intake** — get the spec (OpenAPI, GraphQL introspection/SDL, Postman or curl), base URL, auth method, and environment.
   Credentials come from environment variables, never from chat or logs.
2. **Plan** — list endpoints and propose which test types apply. Show the plan and get a go-ahead
   (use `templates/test-plan.md`).
3. **Safety gate** — for load, stress, spike, soak, chaos or fuzz: confirm the target is
   non-production and that the user owns or is authorized to test it. If not confirmed, stop.
4. **Generate** — read only the reference files for the chosen test types and produce runnable tests.
5. **Run** — execute if the environment allows; otherwise give exact commands to run.
6. **Report** — summarize per endpoint, rank findings by severity, include a reproducible command each
   (use `templates/report.md`).

## Reference files (load on demand)
- `references/functional.md` — positive, negative, edge
- `references/graphql.md` — GraphQL-specific functional and security checks
- `references/integration.md` — multi-endpoint flows
- `references/contract.md` — schema conformance, breaking changes
- `references/security.md` — OWASP API Top 10 checks
- `references/load.md` — load, stress, spike, soak (k6)
- `references/chaos.md` — fault-injection plans
- `references/fuzzing.md` — malformed-input fuzzing

## Not covered
UI accessibility testing (separate concern).
