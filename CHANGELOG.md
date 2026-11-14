# Changelog

All notable changes are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/). Nothing has been tagged or released yet.

## [0.1.0] - unreleased

First release candidate, built in phases (see PLAN.md).

### Changed (reports and docs, 2026-11-14)
- `summarize_results.py` now writes a self-contained HTML report (inline CSS, JS and SVG charts, no external assets, light and dark theme) and a `report.json` next to it. Default output is `reports/report.html`; `-o report.md` still writes Markdown, `--no-json` skips the JSON.
- README: results section (real Spring Boot run, public sandbox run, seeded flaws), install and usage for every test type, group or single test, safety gate and env var table.
- Code comments rewritten to be shorter and plainer; generated files no longer carry the project name in their header comments.
- Samples: `examples/sample-report.html` and `examples/spring-boot-demo/` (inputs, HTML and JSON report).

### Fixed (eval follow-ups, 2026-11-13)
- Invented sample values from the field name when the spec gives none: `email` gets a valid address, `*_status`/`status_code` 200, `*_url`/`link`/`webhook` an https URL, `port` 8080 (a range, default, enum or pattern still wins). They stay marked as guesses.
- No create-then-read integration flow for a path parameter the server echoes or computes (`/anything/{anything}`), unless the create response documents it.
- A skipped test for an inferred auth scheme says the scheme was inferred from the parameter name.
- `chaos_run.py` exits 4 when a signal cuts the run short (partial results, faults undone); `references/chaos.md` notes that a secret in the target URL path would still be echoed.

### Added
- Skill `api-testing`: `SKILL.md` router plus 16 reference files loaded on demand.
- Intake parsers: OpenAPI 3.x and Swagger 2, Postman collections, curl commands, GraphQL SDL and introspection JSON, all
  normalized to one intake JSON (`intake.py`).
- Generators for pytest suites: smoke, positive, negative, edge, data-validation, contract, integration, resilience and
  security (OWASP API Top 10), for REST and GraphQL, with a runtime that enforces the safety gate and redacts secrets.
- Breaking-change report between two spec versions (`spec_diff.py`).
- k6 scripts for load, stress, spike and soak (`gen_k6.py`, `run_k6.py`), hard caps and a circuit breaker.
- Seeded, rate-limited fuzzing for REST (`--types fuzz`) and fault-injection plans (`gen_chaos_plan.py`).
- Executable chaos testing (`chaos_run.py`, `chaos_proxy.py`): latency, error, drop, truncate and blackhole faults in a local proxy, TCP faults between the API and a dependency, dependency start/stop commands the user supplies, malformed and oversized payload bursts, steady load with recovery-time measurement; JUnit and JSON output that `summarize_results.py` reports. Same safety gate, hard caps, loopback-only proxies.
- `APITEST_READ_ONLY=yes` (or `--apitest-read-only`): public-sandbox mode that sends only GET/HEAD/OPTIONS and no active probes (pytest suites, k6 scripts and `chaos_run.py`).
- Safety gate for every non-functional flow (`safety_gate.py`, a copy inside each k6 script, the pytest runtime).
- Markdown report from pytest JUnit XML and k6 summaries (`summarize_results.py`), with a repro command per finding.
- Eval driver, mock targets and a real local seeded target (`evals/`); results in `docs/evals.md`.
- Packaging: Claude Code plugin and marketplace manifests, `tools/validate_skill.py`, `tools/build_skill_zip.py`,
  LICENSE (MIT), CONTRIBUTING, release checklist and marketplace listing copy (`docs/`).
- Repository hygiene: SECURITY.md (reporting and responsible use), issue and pull request templates, GitHub Actions CI
  (tests on Python 3.11 to 3.13, validator, ruff, optional k6 job), `ruff.toml`.
- `examples/`: a demo API with seedable flaws, a sample spec and a real generated report.

### Fixed
- Required request-body fields whose sample value is invented are flagged in the failure text of a 4xx (as query parameters already were); integration flows skip with a reason when a create response has no id (Location header or a single id-like field are used first); a credential parameter with no declared security scheme (`X-API-Key`, `Authorization`, `api_key`) is treated as auth with a warning instead of being sent without credentials.
- False positives found by running the suite on four public APIs: the "unknown id returns 404" test now runs only for path
  parameters that name a resource (`id`, `petId`, `uuid`, `slug`, `username`, or the only parameter of an endpoint that documents a 404),
  not for `/redirect/{n}` or `/anything/{anything}`; optional query parameters with no spec example, default, enum, range or format are
  no longer sent with an invented value (`?status=1`), and a failure caused by an invented required value says so; a failure to
  connect (refused, DNS, connect timeout, proxy tunnel) is retried for GET/HEAD/OPTIONS and then skipped as "target unreachable" (smoke tests
  and `APITEST_UNREACHABLE=fail` fail instead, so a dead target still exits non-zero). An error after connecting (drop, reset, hang) fails
  the test with a clear message for every method, because it is what a crashing server does.
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
  suite sends; `APITEST_RETRIES` (default 2) sets the retries after a failed connect. Rate-limit probes skip while a throttle is set.
- A remote target without `APITEST_TARGET_ENV` now stops the whole pytest run with exit code 4 and one clear message,
  instead of erroring test by test (generated `conftest.py`, `preflight()` in the runtime).
- Destructive tests carry a `destructive` pytest marker; the run-last ordering uses it instead of reading test source.
- Runs where more than half of the tests skip print a warning in pytest and add one to the report.

### Known gaps
- Real Petstore, VAmPI and DVGA runs were not possible from the build sandbox; those evals ran against labelled mocks.
- VAmPI mass assignment is not detected (documented in `docs/evals.md`).
- GraphQL fuzzing, mass-assignment read-back and uniquified create bodies are deferred to a later version.
