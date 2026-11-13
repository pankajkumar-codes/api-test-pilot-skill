---
name: api-testing
description: Plan, generate and run API tests from an OpenAPI spec, GraphQL schema, Postman collection or curl examples. Covers functional (positive, negative, edge, integration), contract, security, load/stress/soak and chaos testing. Use when the user wants to test, audit or load-test an HTTP API.
license: MIT
compatibility: Needs Python 3.11+ and, for generated suites, the pytest and requests packages. k6 is optional (load, stress, spike and soak runs only). No network access is needed to plan or generate; running tests needs access to the target API.
metadata:
  author: pankajkumar-codes
  version: "0.1.0"
  repository: https://github.com/pankajkumar-codes/api-test-pilot-skill
---

# API Testing

Router only. Follow the steps in order; load a reference file only when its step needs it.

## 1. Intake
Collect: spec or examples (OpenAPI/Swagger, Postman collection, curl commands, GraphQL SDL or introspection JSON),
base URL, auth method, environment (local/staging/prod).
- Normalize the input: `python scripts/intake.py <file> -o intake.json --summary`
  (auto-detects the type; for GraphQL add `--url <endpoint>`; shape and edge cases in `references/intake.md`).
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
The user exports `APITEST_TARGET_ENV` (non-production value) and `APITEST_AUTHORIZED=yes`; never set them yourself
(loopback targets pass without them; production is always refused). A public sandbox you only read from: `APITEST_READ_ONLY=yes` sends only GET/HEAD/OPTIONS and no active probes. `run_k6.py`, the k6 scripts and the fuzz tests enforce this too.

## 4. Generate
Read only the reference files for the chosen test types, then generate runnable pytest:
`python scripts/gen_pytest.py intake.json -o api_tests --types all` (REST and GraphQL; `--types smoke,positive,...` to pick).
It prints the rerun command, the env var names the suite needs and any endpoints it could not cover. Active security probes
are `intrusive`: the generated `conftest.py` skips them unless the target is local, or non-production and authorized
(`APITEST_TARGET_ENV`, `APITEST_AUTHORIZED=yes`); never set those variables yourself, ask the user.
Spec drift between versions: `python scripts/spec_diff.py old.json new.json`.
Non-functional (after step 3 only; details in each reference):
- load/stress/spike/soak: `python scripts/gen_k6.py intake.json -o k6_tests --profiles load` (GET-only, capped, `--quick` first), run with `python scripts/run_k6.py k6_tests/load.js`
- fuzz: `python scripts/gen_pytest.py intake.json -o fuzz_tests --types fuzz` (opt-in, REST only, seeded and rate-limited)
- chaos: plan with `python scripts/gen_chaos_plan.py intake.json -o chaos-plan.md --env staging`; run experiments with `python scripts/chaos_run.py intake.json --dry-run` first (proxy faults, payload bursts, your own dependency commands; never guess commands)

## 5. Run
Execute if the environment allows; otherwise print the exact commands to run. Always print the rerun command.
If k6 is not installed, say so and print the command; never claim a load run happened when it did not.

## 6. Report
Collect JUnit XML (`pytest --junitxml=results/pytest.xml`) and k6 summaries (`results/<profile>-summary.json`), then:
`python scripts/summarize_results.py --junit results/pytest.xml --k6 results/load-summary.json --intake intake.json -o reports/report.md`
Per-endpoint coverage, findings ranked by severity, a reproducible command per finding (`templates/report.md`, `references/reporting.md`).
Report what was run and what was skipped; never state results the files do not contain.

## Reference files (load on demand)
- `references/test-types.md` — the 18 test types and what each checks
- `references/intake.md` — normalized intake JSON, parser behavior and limits
- `references/functional.md` — smoke, positive, negative, edge
- `references/data-validation.md` — types, formats, enums, lengths, ranges, required fields
- `references/graphql.md` — GraphQL intake, functional and security checks
- `references/integration.md` — multi-endpoint flows (create, read, update, delete)
- `references/contract.md` — schema conformance, breaking changes (`spec_diff.py`)
- `references/security.md` — OWASP API Top 10 checks and the safety gate
- `references/resilience.md` — idempotency, pagination, timeouts, rate-limit behaviour
- `references/load.md` — k6 setup, caps and the launcher; load profile
- `references/stress.md`, `references/spike.md`, `references/soak.md` — the other k6 profiles
- `references/reporting.md` — building the report, severity rules, what it never prints
- `references/chaos.md` — chaos plans and runnable experiments (`chaos_run.py`)
- `references/fuzzing.md` — malformed-input fuzzing

## Not covered
UI accessibility testing (separate concern).
