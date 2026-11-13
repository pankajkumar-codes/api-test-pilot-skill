# Changelog

All notable changes are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/). Nothing has been tagged or released yet.

## [0.1.0] - unreleased

First release candidate, built in phases (see PLAN.md).

### Added
- Skill `api-testing`: `SKILL.md` router plus 16 reference files loaded on demand.
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
- Repository hygiene: SECURITY.md (reporting and responsible use), issue and pull request templates, GitHub Actions CI
  (tests on Python 3.11 to 3.13, validator, ruff, optional k6 job), `ruff.toml`.
- `examples/`: a demo API with seedable flaws, a sample spec and a real generated report.

### Fixed
- False positives found by running the suite on four public APIs: the "unknown id returns 404" test now runs only for path
  parameters that name a resource (`id`, `petId`, `uuid`, `slug`, `username`, or the only parameter of an endpoint that documents a 404),
  not for `/redirect/{n}` or `/anything/{anything}`; optional query parameters with no spec example, default, enum, range or format are
  no longer sent with an invented value (`?status=1`), and a failure caused by an invented required value says so; a transient connection
  error (proxy hiccup, reset, timeout) is retried for GET/HEAD/OPTIONS and then skipped as "target unreachable" instead of failing.
- The safety-gate skip is reported as one `-rs` line plus one summary line, not one line per test. The gate itself is unchanged.
- Redaction fails closed: values nested deeper than 64 levels, or too deep to parse, are replaced by a placeholder and never printed
  raw (a secret hidden deep in a response can no longer reach a failure message or the JUnit file).
- Deeply nested JSON (a hostile spec, a fuzz body or a server response) no longer crashes redaction, repro lines or
  response parsing: `RecursionError` is handled like "not JSON" (this made five fuzz tests fail on Python 3.11); `intake.py`
  reports an over-nested input with exit code 2.
- `evals/run_evals.py --real` against a remote target without `APITEST_TARGET_ENV` prints one clear message and exits 2
  instead of a traceback.

### Changed
- Request throttle: `APITEST_DELAY_MS` / `APITEST_RPS` (or `--apitest-delay-ms` / `--apitest-rps`) space every request the generated
  suite sends; `APITEST_RETRIES` (default 2) sets the retries after a connection error.
- A remote target without `APITEST_TARGET_ENV` now stops the whole pytest run with exit code 4 and one clear message,
  instead of erroring test by test (generated `conftest.py`, `preflight()` in the runtime).
- Destructive tests carry a `destructive` pytest marker; the run-last ordering uses it instead of reading test source.
- Runs where more than half of the tests skip print a warning in pytest and add one to the report.

### Known gaps
- Real Petstore, VAmPI and DVGA runs were not possible from the build sandbox; those evals ran against labelled mocks.
- VAmPI mass assignment is not detected (documented in `docs/evals.md`).
- GraphQL fuzzing, mass-assignment read-back and uniquified create bodies are deferred to a later version.
