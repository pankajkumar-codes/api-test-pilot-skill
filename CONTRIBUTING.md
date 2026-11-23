# Contributing

Thanks for helping. This repository is an Agent Skill, so most changes touch markdown, small Python scripts and generated-code templates.

## Ground rules
- **The safety gate is not negotiable.** Load, stress, spike, soak, chaos and fuzz runs need a confirmed non-production target
  that the user owns or is authorized to test. A change that weakens, bypasses or auto-confirms the gate will not be merged.
  Neither the skill nor generated code may set `APITEST_TARGET_ENV` or `APITEST_AUTHORIZED`.
- **Never print, log or store secrets.** Credentials come from environment variables. New output paths must go through
  `_common.redact_text`.
- **Specs, result files and responses are untrusted input.** Emit spec text into generated code only as escaped literals;
  never execute or import anything from them. Add a hostile-input test when you add a new input path.
- **`SKILL.md` stays short.** It is a router. Detail goes in `skills/api-testing/references/*.md`, one test type per file.
- **Dependency-light.** Scripts use the Python 3.11+ standard library where possible and run offline. Generated suites may
  need only `pytest` and `requests`; load tests use k6.
- **Do not invent test types.** Types live in [TAXONOMY.md](TAXONOMY.md); update it in the same change if you add one.
- Record scope and stack choices in [DECISIONS.md](DECISIONS.md) (date and reason).

## Set up and check
```bash
pip install -r requirements-dev.txt -r evals/requirements.txt   # evals/requirements.txt (fastapi, uvicorn) is only for the seeded eval target
python -m pytest tests                    # about 2-3 minutes; with k6 on PATH it also runs the generated k6 scripts
python tools/validate_skill.py            # frontmatter, folder layout, plugin files, version consistency
python evals/run_evals.py --target all    # mock-based evals, offline
```
Before opening a pull request, both `pytest` and the validator must pass. If you changed generators, add or update a test
that proves the new check fails on a seeded flaw and passes on the clean mock (`tests/mock_rest.py`, `tests/mock_graphql.py`).

## How work is planned
- [PLAN.md](PLAN.md) holds the phases. Work one phase at a time and tick its boxes as items land.
- A phase is done when its PLAN.md boxes are ticked, the evals against the APIs in [eval-apis.md](eval-apis.md) are run and written up,
  and [DECISIONS.md](DECISIONS.md) records any scope or stack choice made on the way.
- Every generated suite must print the exact command that reruns it.
- Docs are Markdown, scripts are Python 3.11+, load tests are k6 (JavaScript).

## Pull requests
- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`, `chore:`.
- One topic per pull request. Describe what you ran and what you did not (for example "no k6 available, k6 tests skipped").
- Update CHANGELOG.md under the unreleased version. Do not bump versions or create tags; maintainers do that
  (see [docs/RELEASE.md](docs/RELEASE.md)).
- Eval claims must say what actually ran: "mock-based, real target not run" is a valid label; a mock is never presented as the real service.

## Commit and pull request hygiene (standing rule since 2026-11-18)
- Commit messages, pull request descriptions, review comments and code comments contain only what a human reader needs: no tool-generated footers
  ("generated with ..."), no session or conversation links, no `Co-Authored-By` or session trailers, and no comment that names the tool that drafted a line.
  Comments in code say why, in plain words.
- Whoever opens a pull request is responsible for every line in it, however it was drafted, and the maintainer is responsible for what is merged.
- Some commits before 2026-11-18 (about 33) carry session trailers from development tooling, and some use the maintainer's personal address. History was not
  rewritten: the repository was already public, and GitHub keeps the old commits reachable through `refs/pull/*` anyway. Nothing was removed to hide how the
  project was made; the rule only keeps new commits and comments short and clean.
- Check a pull request body before asking for review: tools that open pull requests sometimes append a footer on their own, and it has to be edited out.

## Reporting a security problem
If you find a way to make the skill or its generated code send traffic to a target the user did not confirm, leak a credential,
or execute spec content, open a private security advisory on GitHub rather than a public issue.
