# CLAUDE.md — api-test-pilot-skill

## What this repo is
An Agent Skill (`skills/api-testing/`) that plans, generates and runs API tests
from an OpenAPI spec, Postman collection or curl examples. Goal: publish to
GitHub, then Claude and other AI skill marketplaces.

## How to work here
1. Read `PLAN.md` first. Work phase by phase; tick checkboxes as you finish.
2. Test-type definitions live in `TAXONOMY.md`. Do not invent new types without updating it.
3. Record every scope/stack choice in `DECISIONS.md` (date + reason).
4. Validate against the APIs in `eval-apis.md` before calling any phase done.

## Skill design rules
- `SKILL.md` stays short (router only). Detail goes in `references/*.md`, loaded on demand.
- Scripts must be dependency-light (stdlib where possible) and runnable offline.
- Every generated test suite must be reproducible: print the exact command to rerun.
- **Safety gate (non-negotiable):** load, stress, soak, spike, chaos and fuzz runs require
  the user to confirm the target is non-production and that they own or are authorized to
  test it. Never run these against an unconfirmed target.
- Never print or log secrets. Read credentials from env vars.

## Conventions
- Markdown for docs, Python 3.11+ for scripts, k6 (JS) for load tests.
- Keep files small and focused; one test type per reference file.
- Conventional commits (`feat:`, `fix:`, `docs:`).

## Definition of done (per phase)
Checklist ticked in PLAN.md, eval run documented, DECISIONS.md updated, committed.
