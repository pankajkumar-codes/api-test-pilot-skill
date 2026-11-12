# api-testing-skill

An Agent Skill that plans, generates and runs API tests: functional, security,
contract, performance and resilience.

> Status: scaffold. See [PLAN.md](PLAN.md) for progress.

## Layout
```
skills/api-testing/     the skill (SKILL.md + references, scripts, templates)
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
cd api-testing-skill
git init
claude
```
Then: "Read CLAUDE.md and PLAN.md, resolve the open decisions in DECISIONS.md with me, then start Phase 1."

## License
TBD (see DECISIONS.md).
