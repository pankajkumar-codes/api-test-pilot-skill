# api-test-pilot

An Agent Skill that plans, generates and runs API tests: functional, security,
contract, performance and resilience.

> Status: Phase 4 built (Markdown report from pytest and k6 results) on top of Phase 3 (k6 load/stress/spike/soak, fuzzing, chaos plans) and the Phase 2 pytest generators. See [PLAN.md](PLAN.md) for progress.

## Layout
```
skills/api-testing/     the skill (SKILL.md + references, scripts, templates)
  scripts/              intake parsers (OpenAPI, Postman, curl, GraphQL), pytest generators, spec_diff (stdlib, offline)
  templates/pytest/     runtime helpers copied next to every generated suite
tests/                  tests for the scripts; generated suites run against mock servers
docs/evals.md           validation record
PLAN.md                 phased build plan
TAXONOMY.md             test types covered
DECISIONS.md            scope and stack decisions
eval-apis.md            APIs used to validate the skill
CLAUDE.md               instructions for Claude Code
```

## What it does (to be finalized in Phase 6)
Plans, generates and runs API tests for REST (OpenAPI) and GraphQL: functional, contract,
security, load/stress/spike/soak, chaos and fuzzing. See TAXONOMY.md.

## Install (to be finalized in Phase 6)
- Claude Code: copy `skills/api-testing/` to `~/.claude/skills/` (all projects) or `.claude/skills/` (one project).
- Claude app: zip the `api-testing` folder and upload it in Skills settings (verify current steps).
- Other marketplaces: TBD per listing format.

## Usage (to be finalized in Phase 6)
Give it a spec (OpenAPI file or GraphQL schema/endpoint), a base URL and how to authenticate.
Credentials go in environment variables, never in chat. Example:
"Test this API using openapi.yaml against http://localhost:8080. Run functional and security tests."

## Safety
Load, stress, spike, soak, chaos and fuzz runs only against non-production systems you own or
are authorized to test.

## Requirements (to be finalized)
Python 3.11+; k6 for load tests.

## Start building in Claude Code
```bash
cd api-test-pilot
git init
claude
```
Then: "Read CLAUDE.md and PLAN.md, resolve the open decisions in DECISIONS.md with me, then start Phase 1."

Note: `intake.json` (parser output) describes your API; it is git-ignored, do not commit it.

## Generate tests (Phase 2)
```bash
python skills/api-testing/scripts/intake.py openapi.yaml -o intake.json        # or schema.graphql --url http://localhost:4000/graphql
python skills/api-testing/scripts/gen_pytest.py intake.json -o api_tests --types all
APITEST_BASE_URL=http://localhost:8080 APITEST_BEARERAUTH_TOKEN=... python -m pytest api_tests -v -ra
python skills/api-testing/scripts/spec_diff.py old.json new.json              # breaking-change report
```
Generated suites need `pytest` and `requests`. Active security probes only run against a local or confirmed
non-production target you are authorized to test (see `skills/api-testing/references/security.md`).

## Non-functional tests (Phase 3)
Load, stress, spike, soak, fuzz and chaos need a **non-production target you own or are authorized to test**. Export
`APITEST_TARGET_ENV=staging` and `APITEST_AUTHORIZED=yes` yourself (not needed for `localhost`); production is always refused.
```bash
python skills/api-testing/scripts/gen_k6.py intake.json -o k6_tests --profiles all --quick     # --quick: 10x shorter, checks the plumbing
python skills/api-testing/scripts/run_k6.py k6_tests/load.js --base-url http://localhost:8080   # needs k6; prints the rerun command
python skills/api-testing/scripts/gen_pytest.py intake.json -o fuzz_tests --types fuzz          # seeded, rate-limited fuzzing (REST)
python skills/api-testing/scripts/gen_chaos_plan.py intake.json -o chaos-plan.md --env staging  # a plan only: nothing is injected
```
k6 scripts are GET-only by default, capped (100 req/s, 200 VUs, 4 h, gentle ramps) and abort when half the requests fail. See
`skills/api-testing/references/load.md`.

## Report (Phase 4)
```bash
python -m pytest api_tests -p no:cacheprovider --junitxml=results/pytest.xml
python skills/api-testing/scripts/summarize_results.py --junit results/pytest.xml --k6 results/load-summary.json \
    --intake intake.json -o reports/report.md       # summary, per-endpoint coverage, findings ranked by severity, repro commands
```
Result files are treated as untrusted input and secrets are scrubbed from the report. See `skills/api-testing/references/reporting.md`.

## Develop
```bash
pip install -r requirements-dev.txt
python -m pytest tests          # ~1 min; add k6 (K6_BIN or PATH) to also execute the generated k6 scripts (~5 min more)
python skills/api-testing/scripts/intake.py path/to/openapi.yaml --summary
```

## License
MIT (see DECISIONS.md). Add the LICENSE file in Phase 6.
