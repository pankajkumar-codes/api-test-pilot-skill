---
name: api-testing
description: Plan, generate and run API tests from an OpenAPI spec, GraphQL schema, Postman collection or curl examples. Covers functional (positive, negative, edge, integration), contract, security, load/stress/soak and chaos testing. Use when the user wants to test, audit or load-test an HTTP API.
---

# API Testing

Router only. Follow the steps in order; load a reference file only when its step needs it.

## 1. Intake
Collect: spec or examples (OpenAPI/Swagger, Postman collection, curl commands; GraphQL arrives in Phase 2),
base URL, auth method, environment (local/staging/prod).
- Normalize the input: `python scripts/intake.py <file> -o intake.json --summary`
  (auto-detects the type; output shape and edge cases in `references/intake.md`).
- Surface the parser `warnings` to the user (missing base URL, no response schemas, etc.) and ask for what is missing.
- Credentials come from environment variables only (the parser lists the names, e.g. `APITEST_BEARERAUTH_TOKEN`).
  Never ask for, print or log secret values.
- `intake.json` describes the user's API; keep it out of version control (it is git-ignored by default).

## 2. Plan
Fill `templates/test-plan.md` from `intake.json`: endpoints in scope and the test types that apply
(type list: `references/test-types.md`). Show the plan and get a go-ahead before generating anything.

## 3. Safety gate
Load, stress, spike, soak, chaos and fuzz require the user to confirm (a) the target is
non-production and (b) they own or are authorized to test it. If not confirmed, stop. Do not weaken this.

## 4. Generate
Read only the reference files for the chosen test types and produce runnable tests.

## 5. Run
Execute if the environment allows; otherwise print the exact commands to run. Always print the rerun command.

## 6. Report
Summarize per endpoint, rank findings by severity, include a reproducible command per finding
(`templates/report.md`).

## Reference files (load on demand)
- `references/test-types.md` — the 18 test types and what each checks
- `references/intake.md` — normalized intake JSON, parser behavior and limits
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
