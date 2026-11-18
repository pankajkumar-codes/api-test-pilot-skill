# Changelog

All notable changes are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/). Nothing has been tagged or released yet.

## [0.1.0] - unreleased

First release candidate, built in phases (see PLAN.md).

### Added (empty optional body, issue 50)
- When a JSON body has no required property, the edge tests also send `{}` and require a clean answer (no 5xx). When some properties are required the baseline body already carries only those, so a separate "required fields only" case would repeat it.

### Added (examples, ids file, no-scheme specs, issues 46, 51, 52)
- Intake reads `x-example`, the OpenAPI 3.1 `examples` list and the first named `examples` entry as the example. A simple `pattern` (classes, ranges, counts, groups, first alternative) gets a generated matching value; patterns it cannot build, such as lookaheads, stay flagged as invented.
- `APITEST_EXAMPLES_FILE` (`operationId.field` or `*.field` to a value) sets body fields and query parameters at run time; the report lists the fields whose invented value drew a 4xx ("give an example for these").
- `APITEST_IDS_FILE` holds path and victim ids in one JSON file (an environment variable still wins). Every run ends with "Set these to run the skipped tests": each variable and how many tests it would unskip.
- Intake warns when a spec declares no security scheme; `--assume-bearer` (with `--public-paths` globs) makes the other operations need a bearer token. Not done from issue 46: the setup stage that creates the objects itself, tracked as a follow-up.

### Added (optional headers the server enforces, issue 42)
- A write that answers 400, 412, 422 or 428 with a body naming a header the spec declares optional (`idempotency_key_required`, `if_match_required`) is reported as spec drift (LOW): "the server seems to require header X; the spec says optional", with the setting that fixes it.
- `APITEST_SEND_OPTIONAL_HEADERS=Idempotency-Key,If-Match` sends those headers on every operation that declares them. A bare `Idempotency-Key` gets a fresh unique value per call, any other name gets `*`, and `Name=value` sets the value.

### Fixed (report evidence and report.json, issues 41, 43)
- A failure that is a 400, 404, 409, 410, 413, 415 or 422 on a request that used an invented value (no `example` or default in the spec) is now tagged INFO and titled "needs an example", not HIGH or MEDIUM. 401, 403, 429 and 5xx keep their normal rating.
- `report.json` now lists every finding (up to 5000) instead of the first 100. New fields: `findings_shown` (how many the page and Markdown list) and `findings_truncated`.

### Fixed (tenant-scoped APIs, issues 38, 39, 40)
- Integration flows no longer fail with `KeyError` for resources under a tenant path such as `/b/{business_id}/...`. Path parameters that no create step in the flow produces are read from `APITEST_PATH_<NAME>`; when one is not set the flow skips and names the variable.
- The "request without credentials must be rejected" test uses a configured `APITEST_PATH_<NAME>` id (or a spec default) when there is one, for read operations; write operations always use a made-up id so a wrongly accepted unauthenticated write cannot change real data. With a made-up id it also accepts 404, because many APIs resolve the path before they check who is asking, and says so in the test message.
- The `minLength` and `maxLength` edge cases for fields whose name implies a structure (email, password, code, otp, token, url, date, phone, file name and similar) no longer demand success from a run of `a`; they only require a clean answer (2xx or 4xx, never 5xx).
### Added (GitHub issues for failed tests, 2026-11-18)
- `scripts/file_issues.py`: files GitHub issues for the findings in `report.json`. Preview by default (offline, no token); `--create` shows the preview and asks for `yes` (or takes `--yes`), and refuses without a terminal or a token. Findings on the same endpoint with the same kind of failure become one issue; a hidden fingerprint makes reruns skip issues already filed (closed ones too, unless `--refile-closed`). Samples are scrubbed again, `--no-samples` leaves them out, the token comes from `APITEST_GITHUB_TOKEN` and is only sent over https. Limits: `--min-severity`, `--max-issues`. Reference: `references/issues.md`; tests: `tests/test_file_issues.py`, run against a local fake GitHub API.

### Checked (chaos rerun on the fixed demo, 2026-11-17)
- Chaos rerun against spring-boot-demo main 0acc5b5 (JDBC socket timeout fix) on a local PostgreSQL 16, with the skill at f790ae6: proxy-latency, proxy-5xx, proxy-drop, payload, dep-outage and dep-slow, 16 checks, 16 passed, 0 failed. `dep-slow` (database frozen with SIGSTOP for 6 s): requests during the freeze failed with p95 4.0 s and the API recovered 0.8 s after SIGCONT; `dep-outage` recovered in 3.2 s. The earlier MEDIUM finding is gone. `dep-blackhole` was not rerun, the example report was not regenerated, and capacity was not measured.

### Fixed (rate-limit burst setting, 2026-11-17)
- `APITEST_RATE_BURST` is read through one runtime helper. A value that is not a whole number from 1 to 1000 now fails the test with one clear message instead of a `ValueError` traceback in the generated suite. An unset value still uses the per-test default.
- The four review notes from the chaos and sample-value PR were already fixed in the 2026-11-14 batch below; nothing was left open there.
- README lists what is still unverified: ghz load tests, the AWS emulator run, the stand-in status of the gateway and GraphQL apps, and the VAmPI mass-assignment gap.

### Checked (final full pass over v2, 2026-11-17)
- Full suite on main: 616 pass and 23 skip with the example-app packages installed (605 and 34 with only the dev requirements). `ruff check`, `tools/validate_skill.py` and the zip build are clean. The gRPC suites also pass against real grpcurl 1.9.1 (32 tests in `test_grpc.py` and `test_grpc_real.py`).
- Eval tables re-run on main: Petstore mock 325 pass, VAmPI mock 232 pass and 1 known failure, seeded FastAPI app 91 pass, GraphQL mock 102 pass; every seeded flaw is found except the documented VAmPI mass-assignment gap. `docs/evals.md` had stale clean-variant counts (326, 230 and 85) and now matches these runs.
- Not run: k6 load tests (k6 is not installed here; CI runs them), gRPC load through ghz (not generated yet) and the AWS emulator.

### Fixed (issue 22, 2026-11-17)
- A spec built from nested YAML aliases (an alias bomb) no longer hangs intake or writes gigabytes of JSON. Resolving stops after 50,000 nodes in one schema and 1,000,000 across all schemas, the examples and defaults an author wrote are cut at 10,000 nodes each, and the finished intake at 500,000 nodes. Each cut adds a warning, and the schema one names the schema. Ordinary large schemas are unaffected. Test counts were measured on this branch merged with main at f4f31b0: 613 pass and 34 skip with only the dev requirements.

### Added (local gateway and GraphQL examples, 2026-11-17)
- `examples/aws-local/`: a FastAPI app that behaves like an API Gateway REST API in front of a function (stage prefix, `x-api-key`, usage-plan throttling, request validation errors, `502` for a failing function) and a strawberry GraphQL app with an API key. `tests/test_aws_local.py` runs the generated suites against both as real servers, clean and with seeded flaws (skipped unless fastapi, uvicorn and strawberry-graphql are installed).
- GraphQL intake: `intake.py --api-key-header NAME` sends the credential in that header (from `APITEST_APIKEY_KEY`) instead of a bearer token. The runtime uses whichever scheme the intake declares.
- Fixed: a GraphQL argument that is non-null but has a default (`first: Int! = 20`) is optional, but its variable was still declared `Int!` and left out, so every server rejected the request. The variable is now declared nullable. Found by running the suite against the real strawberry server. The k6 GraphQL generator got the same fix.
- SECURITY.md notes that a bearer token is visible in grpcurl's process arguments.
- A clean run of the whole suite is now 605 pass and 34 skip with only the dev requirements (616 pass and 23 skip with the example-app packages installed).
- The AWS emulator was not run: there is no Docker daemon in the build environment and the Debian package could not be located. These apps are stand-ins, not the emulator.

### Added (gRPC unary tests, 2026-11-17)
- `intake.py` reads `.proto` files (proto2 and proto3, nested messages, `oneof`, `map`, imports below the proto's folder) and `gen_pytest.py` generates smoke, positive, negative, edge, contract and security suites for unary methods. They call the optional `grpcurl` tool, so nothing else is needed; without it every test skips.
- Reports and repro lines show a gRPC call as an HTTP-shaped exchange with the auth header redacted. Streaming methods are listed and skipped.
- Tested two ways with seeded flaws (missing auth, invalid input accepted, open reflection, wrong response shape, server fault on injection, leaked internals): against a stand-in `grpcurl` (always runs), and against a real `grpcio` server through real grpcurl 1.9.1 (`tests/test_grpc_real.py`, skipped unless grpcio, grpcio-tools and grpcurl are installed). Clean run: 73 tests pass, and every seeded flaw is found. Load tests through `ghz` come next. Hostile `.proto` files (non-integer or out-of-range field numbers, very deep nesting including `oneof`, non-UTF-8 bytes) end in a one-line error. A clean run of the whole suite is now 591 pass and 24 skip.

### Fixed (issue 28, 2026-11-17)
- A GraphQL SDL number literal that is too large (an integer over Python's 4300-digit limit, or a float such as `1e999` that overflows to infinity) now stops intake with `GraphQL parse error: number too large` (exit code 2) instead of a traceback or an invalid `Infinity` in the JSON.

### Added (GraphQL resilience tests, 2026-11-17)
- `--types resilience` now works for GraphQL (and is part of `all`), so every pytest type is available for GraphQL. Repeatable reads, a response budget, page-size and cursor handling for list queries, an `intrusive` rate-limit burst (a 429 needs a valid `Retry-After`) and a repeated delete of an object the test created itself.
- The GraphQL fuzzer skips the `long_name` case when the byte limit leaves no room for it, instead of always repeating the name twice.
- The GraphQL mock gains `ignorefirst`, `slowpets` and `throttle` flaws (unit-test only).
- A clean run of the whole suite is now 565 pass and 18 skip.

### Added (GraphQL data-validation tests, 2026-11-17)
- `--types data-validation` now works for GraphQL (and is part of `all`): one violation per case on a valid baseline, rejection expected. Covers missing required input fields, wrong types and unknown enum values inside input objects, unknown input fields, and breaches of `@constraint` limits (`minLength`, `maxLength`, `min`, `max`, `format`).
- The SDL parser now reads `@constraint` limits into the intake (`constraints` on arguments and input fields); values of the wrong kind are ignored. Introspection JSON has no directives, so constraint cases need the SDL.
- The GraphQL mock enforces `@constraint` and rejects unknown input fields, and has a `novalidate` flaw that does neither. `stale`, `undeleted` and `novalidate` are unit-test flaws only: the eval table still lists the original 10 seeded GraphQL flaws.
- Hardening: `@constraint` limits must be finite numbers within 10^12 (or strings of at most 200 characters), and a `minLength` above 10000 gets no too-short case, so a hostile schema cannot make the generator build a huge string or write an unimportable suite.
- The GraphQL clean eval variant now runs 85 tests, all 10 seeded GraphQL flaws are still found, and a clean run of the whole suite is 557 pass and 18 skip.

### Added (GraphQL integration flows, 2026-11-17)
- `--types integration` now works for GraphQL (and is part of `all`): per object type with a create, a read by id and a delete mutation, one flow runs create -> read -> list -> update -> read -> delete -> read (gone). It uses only the object it creates, cleans it up even when a step fails, and skips writes on production. A schema with no such type gets a generator note.
- Fuzz follow-ups: `huge_whitespace` and `long_name` cases respect the byte limit, the opt-in test asserts the `deletePet` entry directly, and a test shows an unconfirmed remote target is refused before any request.
- The GraphQL mock gains an `updatePet` mutation and two seeded flaws (`stale`: an update that is not stored, `undeleted`: a delete that does nothing). The GraphQL clean variant now runs 72 tests, and a clean run of the whole suite is 549 pass and 18 skip.

### Added (GraphQL fuzz tests, 2026-11-17)
- `--types fuzz` now works for GraphQL (opt-in, intrusive, not part of `all`): one seeded, bounded test per query and mutation sends hostile variable values, missing and extra variables, malformed envelopes, damaged query text and other content types, and reports 5xx, hangs, stack traces and slow answers. The REST fuzzer's send-and-judge loop moved into a shared `run_cases`. `delete*` mutations also need `APITEST_ALLOW_DESTRUCTIVE`.
- Documented: an `Int` answered as `1.0` fails the contract check.

### Added (GraphQL contract tests, 2026-11-17)
- `--types contract` now works for GraphQL (and is part of `all`): each query that returns an object is run with its default selection and the answer is checked against the schema: selected fields present, scalar JSON types, enum values, nulls only where the type allows them, list types as arrays, nested lists (`[[Item!]!]!`) level by level. Mismatches are MEDIUM findings. The generated `operations.py` carries the type table the check needs.
- A tenth seeded GraphQL flaw (`badshape`: a number for a String, an enum value the schema lacks) joins the eval table: 27 of 28 seeded flaws are now found (the gap is still VAmPI mass assignment), and the GraphQL clean variant runs 66 tests.
- Fixed in the same change: the README test count (a clean run with only the dev requirements is 534 pass and 18 skip).

### Added (webhooks and callbacks, 2026-11-17)
- OpenAPI 3.1 `webhooks` and operation `callbacks` are read into a `webhooks` list in the intake (name, method, payload schema and example, response codes; callbacks also the parent endpoint and expression). `intake.py --summary` lists them. They are calls the API makes to you, so no tests are generated for them. Secret-looking payload values are dropped, and at most 200 are listed.
- A whole-file `$ref` no longer shows an internal name (`f4`) as the schema's `$name`.
- Wrongly typed fields in a webhook or callback, a non-map `paths` and a non-map `responses` become warnings instead of crashing the intake.

### Added (multi-file specs, 2026-11-17)
- OpenAPI specs split over several files are read: relative file `$ref`s to schemas, path items and parameters, and refs between the files, are resolved offline. Only `.yaml`, `.yml` and `.json` files inside the spec's folder are read (at most 50 files of 5 MB each). Absolute paths, URLs, `..` escapes, symlinks pointing out of the folder, unreadable or over-nested files are skipped with a warning. YAML aliases stay shared, so an alias-heavy spec does not blow up, and a spec without file refs is parsed exactly as before.

### Added (mass-assignment read-back, 2026-11-17)
- The API3 mass-assignment probe reads the created record back when the spec has a matching read endpoint, and fails if an injected privileged field (`is_admin`, `role`, ...) was stored even though the create response did not echo it. The probe record is removed afterwards when the spec has a delete endpoint. The probe body now gets the same run tag as other create bodies.
- When no id can be taken from the create response, the read-back skips with a reason. Still not covered: creates whose read endpoint sits under another path or documents no such field (the VAmPI register case), and nested resources.

### Fixed (unique create bodies review, 2026-11-16)
- Unique-field detection matches whole field names only. `country_code`, `currency_code`, `postal_code`, `hostname`, `surname`, `postcode` and `reference` are no longer tagged; fields with a `const` or `default`, or an example that looks like a fixed code (`US`, `AB12`), are skipped. The intake now keeps `const` in simplified schemas (dropped for secret-named fields like `example` and `enum`).
- `scrub()` in the generator also drops `const` for secret-named properties (defense in depth, with a canary test).
- Under pytest-xdist each worker adds its number to the tag; `POST /authors` is no longer mistaken for an auth endpoint.
- Docs: a pinned `APITEST_RUN_ID` repeats values on a rerun; multipart bodies and the k6 scripts are not tagged.

### Added (unique create bodies, 2026-11-14)
- Baseline create bodies get a run tag on unique-looking string fields (`email`, `username`, `slug`, `sku`, `name` ...), so repeated creates on a target that keeps data no longer collide and skip. Enum, pattern, secret and read-only fields are never touched, nor values where the tag would not fit `maxLength`. Echo and read-back checks compare against the body that was actually sent. `APITEST_UNIQUE_BODIES=no` opts out, `APITEST_RUN_ID` pins the tag.

### Fixed (review nits, 2026-11-14)
- Name-based sample values: a hinted email, url or phone number cut by `maxLength` falls back to `sample-<name>` instead of becoming invalid; the hint patterns are narrower (`email_template`, `phone_type`, `order_status` get no hint; `homepageURL` does).
- `chaos_run.py`: a second signal during cleanup no longer skips writing the results; the run still exits 4. `references/chaos.md` reworded.

### Changed (demo fix, 2026-11-14)
- Docs: the chaos `dep-slow` finding is fixed in spring-boot-demo (PR #5, JDBC socket timeout, 503 in 2 to 3 s); README and `docs/evals.md` say so. The chaos suite was rerun on 2026-11-17, see below.

### Changed (naming and README audit, 2026-11-14)
- Plugin, marketplace and display name are now `api-test-pilot-skill` / "API Test Pilot Skill" (README, plugin.json, marketplace.json, listing copy, release checklist). The k6 template header is `X-Load-Test: api-test-pilot-skill` and its user agent `api-test-pilot-skill-k6`. The skill folder stays `api-testing`.
- README: scope wording (gRPC planned for v2; accessibility, mobile and hosted service outside the skill), status line, results tables match the final runs, exit codes, install notes.
- Samples: the spring-boot-demo report is the final run (189 tests); the sample report title marks the demo API as deliberately flawed; new `examples/public-sandbox/` with the four read-only public API reports and specs.
- Fixed: `docs/RELEASE.md` said `claude plugin marketplace add .`, which Claude Code rejects; it is `./`.

### Added (request and response in reports, 2026-11-14)
- Generated suites record the first three requests of each test (method, URL, status, time, headers, bodies; secrets redacted, bodies clipped) into the JUnit file; the HTML and JSON reports show them under each test case.

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
