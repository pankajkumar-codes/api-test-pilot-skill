# Marketplace listing copy

Plain facts only; every claim below is true of version 0.1.0 and is backed by the README, `docs/evals.md` or the tests.
Do not add claims about detection rates on real third-party targets until those runs exist.

## Names
- Display name: **API Test Pilot**
- Plugin and marketplace id: `api-test-pilot`; skill folder and skill name: `api-testing`
- Repository: https://github.com/pankajkumar-codes/api-test-pilot-skill
- License: MIT
- Category: testing (tags: api, testing, openapi, graphql, security, pytest, k6)

## One-line description (under 100 characters)
Plan, generate and run API tests from OpenAPI, GraphQL, Postman or curl, with a safety gate.

## Short description (under 300 characters)
Plans, generates and runs API tests from an OpenAPI spec, GraphQL schema, Postman collection or curl examples. Produces pytest and
k6 files, covers functional, contract, security (OWASP API Top 10), load and fuzz tests, and writes a severity-ranked report.
Heavy tests only run against targets you confirm are non-production.

## Long description
API Test Pilot is an Agent Skill for testing HTTP APIs. Give your agent an OpenAPI or Swagger spec, a GraphQL schema, a Postman
collection or a few curl commands, plus a base URL, and it walks through intake, a test plan you approve, generation, a run and a report.

What you get:
- **pytest suites** you can read and keep: smoke, positive, negative, edge, data validation, contract, integration, resilience.
- **Security checks** mapped to the OWASP API Top 10: missing or weak authentication, BOLA and BFLA, injection, mass assignment,
  SSRF, excessive data exposure, CORS and security headers.
- **k6 scripts** for load, stress, spike and soak runs, GET-only and capped by default.
- **Seeded fuzzing** for REST and a **fault-injection plan** for chaos testing (a plan only; nothing is injected).
- **A Markdown report** with per-endpoint coverage, findings ranked by severity and a repro command for each finding.
- **A breaking-change report** between two versions of a spec.

Safety by design: load, stress, spike, soak, chaos and fuzz runs refuse to start unless you confirm the target is non-production
and that you are authorized to test it. Production targets are refused, credentials are read from environment variables only and
redacted from every report, and specs and results are treated as untrusted input.

Requirements: Python 3.11+, `pytest` and `requests` for the generated suites, and k6 only for load tests. Planning and generating work offline.

Honest limits: REST and GraphQL only (no gRPC). Validated on a seeded local API and labelled mock stand-ins for Petstore, VAmPI
and DVGA (26 of 27 seeded flaws found; VAmPI mass assignment is a known gap). Runs against the real third-party targets are not yet done.

## Install lines
```
/plugin marketplace add pankajkumar-codes/api-test-pilot-skill
/plugin install api-test-pilot@api-test-pilot
```
Plain files: copy `skills/api-testing/` to `~/.claude/skills/`. Claude app: upload `dist/api-testing-<version>.zip`
(`python tools/build_skill_zip.py`).

## Example prompts
- "Test `openapi.yaml` against `http://localhost:8080`: functional and security tests, then give me a report."
- "Compare `v1.json` and `v2.json` and list the breaking changes."
- "Generate a k6 load test for this API. The target is our staging environment and I am authorized to test it."
- "Write a chaos plan for these endpoints for staging."

## Screenshots
None. The skill has no UI. The README shows real terminal output and `examples/sample-report.md` is a real generated report.
