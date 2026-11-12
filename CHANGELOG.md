# Changelog

All notable changes are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/). Nothing has been tagged or released yet.

## [0.1.0] - unreleased

First release candidate, built in phases (see PLAN.md).

### Added
- Skill `api-testing`: `SKILL.md` router plus 15 reference files loaded on demand.
- Intake parsers: OpenAPI 3.x and Swagger 2, Postman collections, curl commands, GraphQL SDL and introspection JSON, all
  normalized to one intake JSON (`intake.py`).
- Generators for pytest suites: smoke, positive, negative, edge, data-validation, contract, integration, resilience and
  security (OWASP API Top 10), for REST and GraphQL, with a runtime that enforces the safety gate and redacts secrets.
- Breaking-change report between two spec versions (`spec_diff.py`).
- k6 scripts for load, stress, spike and soak (`gen_k6.py`, `run_k6.py`), hard caps and a circuit breaker.
- Seeded, rate-limited fuzzing for REST (`--types fuzz`) and fault-injection plans (`gen_chaos_plan.py`, plan only).
- Safety gate for every non-functional flow (`safety_gate.py`, a copy inside each k6 script, the pytest runtime).
- Markdown report from pytest JUnit XML and k6 summaries (`summarize_results.py`), with a repro command per finding.
- Eval driver, mock targets and a real local seeded target (`evals/`); results in `docs/evals.md`.
- Packaging: Claude Code plugin and marketplace manifests, `tools/validate_skill.py`, `tools/build_skill_zip.py`,
  LICENSE (MIT), CONTRIBUTING, release checklist and marketplace listing copy (`docs/`).
- `examples/`: a demo API with seedable flaws, a sample spec and a real generated report.

### Changed
- A remote target without `APITEST_TARGET_ENV` now stops the whole pytest run with exit code 4 and one clear message,
  instead of erroring test by test (generated `conftest.py`, `preflight()` in the runtime).
- Destructive tests carry a `destructive` pytest marker; the run-last ordering uses it instead of reading test source.
- Runs where more than half of the tests skip print a warning in pytest and add one to the report.

### Known gaps
- Real Petstore, VAmPI and DVGA runs were not possible from the build sandbox; those evals ran against labelled mocks.
- VAmPI mass assignment is not detected (documented in `docs/evals.md`).
- GraphQL fuzzing, mass-assignment read-back and uniquified create bodies are deferred to a later version.
