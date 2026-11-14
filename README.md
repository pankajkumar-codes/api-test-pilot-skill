# api-test-pilot

An [Agent Skill](https://agentskills.io/specification) that plans, generates and runs API tests from an OpenAPI spec,
a GraphQL schema, a Postman collection or curl examples. It writes plain pytest and k6 files you can read, edit and keep.

| Area | What it produces |
|---|---|
| Functional | smoke, positive, negative, edge, data-validation, integration flows (pytest) |
| Contract | responses checked against the spec; breaking-change report between two spec versions |
| Security | OWASP API Top 10 checks: authN/authZ, BOLA, BFLA, injection, mass assignment, SSRF, data exposure, CORS and headers |
| Resilience | idempotency, retries, pagination, rate-limit behaviour |
| Load, stress, spike, soak | k6 scripts with hard caps, plus a launcher |
| Fuzzing | seeded, rate-limited malformed-input suite (REST) |
| Chaos | a fault-injection plan, plus runnable experiments (local fault proxies, dependency start/stop commands you supply, malformed and oversized payload bursts) with recovery-time measurement; same safety gate |
| Reporting | one self-contained HTML report (plus JSON): results per test type, coverage per endpoint, findings ranked by severity, a repro command for each |

Protocols: REST (OpenAPI 3.x and Swagger 2) and GraphQL. Out of scope for v1: gRPC, mobile, UI accessibility, a hosted service.
Full list of test types: [TAXONOMY.md](TAXONOMY.md).

> **Status: 0.1.0, not yet released.** The code is complete and tested; the release itself (tag, marketplace listings) is
> still to be done. See [docs/RELEASE.md](docs/RELEASE.md). The three third-party targets (Petstore, VAmPI, DVGA) have **not** been run
> for real, only against labelled mock stand-ins that the same author wrote, so the detection numbers show that the checks work on those
> seeded flaws and nothing more. Read [docs/evals.md](docs/evals.md) before you trust them.

## Results: what has been run

The skill has been run end to end on a real service and on public sandboxes, and the numbers below are from those runs. What each run
did and did not cover is stated next to it. Detection numbers against *seeded* flaws are in [docs/evals.md](docs/evals.md).

**Open the report:** [spring-boot-demo, real run (HTML)](examples/spring-boot-demo/report.html) ·
[seeded-flaw demo, 7 findings (HTML)](examples/sample-report.html). Download the file and open it in a browser; it works offline.

### A real Spring Boot service (local, throwaway database)

Target: a Spring Boot 4 REST demo built from source and run on `localhost:8080` against a throwaway PostgreSQL 16. The spec was
written by hand from the controller (7 endpoints). Nothing outside the sandbox was touched.

| Run | Tests | Pass | Fail | Skip | Outcome |
|---|---|---|---|---|---|
| Functional: smoke, positive, negative, edge, data-validation, contract, security, integration, resilience | 166 | 165 | 0 | 1 | no findings |
| Fuzz, 150 cases per endpoint at 20 req/s | 7 | 5 | 1 | 1 | **1 MEDIUM, a real bug** |
| Chaos: latency, 5xx, dropped connections, payload bursts, database outage | 15 | 15 | 0 | 0 | no findings |
| Fuzz again after the bug was fixed | 7 | 7 | 0 | 0 | finding gone |
| k6 load, stress, spike, soak (GET endpoints only) | 4 profiles | 4 | 0 | 0 | 0 failed requests, p95 3 to 8 ms |

The real finding: `GET /api/v1/software-engineers?page=2147483647` returned 500, because page times size overflowed Spring Data's integer
offset. It was confirmed by hand and fixed in the demo project. After the fix the fuzz run passed 7 of 7.

What was not done on this target, so you can judge the numbers:
- k6 ran **GET endpoints only, at the default rates** (5 to 25 req/s, up to 50 virtual users). The soak was **shortened to 5 minutes**
  (the default is 30). This shows the demo is comfortable at that rate on a small machine. It is not a capacity limit.
- Writes and intrusive probes ran only because the database was disposable. HTTPS and authentication were not covered (the demo has none).
- The sample HTML report above combines the functional run, the fuzz run after the fix and the four k6 runs. The chaos run's own result
  files are not part of it.

### Four public sandbox APIs (read-only)

JSONPlaceholder, ReqRes, HTTPBin and Webhook.site, with `APITEST_TARGET_ENV=production` so that only reads were sent, a 0.3 s pause between
tests, no credentials, and no load, fuzz, stress, spike, soak or chaos.

| | Tests | Pass | Fail | Skip |
|---|---|---|---|---|
| Total across the four APIs | 1098 | 257 | 52 | 789 |

72% of the tests were skipped, almost all by the safety gate, so this run says little about writes. Most failures were false positives that
this run exposed in the skill (redirect-only endpoints, guessed sample values, ids for non-id path parameters). They were fixed afterwards.
The real findings were a CORS policy that reflects any origin with credentials, missing HSTS and a spec mismatch on HTTPBin's cookie endpoints.
Specs for three of the four APIs were written by hand.

### Seeded flaws and tests of the skill itself
- On the demo API in [`examples/`](examples) with two seeded flaws, the skill found both (BOLA, 3 findings, and data exposure, 1 finding). The other 3 failures come from
  using a non-admin token on an admin endpoint; the [sample report](examples/sample-report.html) shows all 7.
- Against local mocks with 14 seeded REST flaws and 9 GraphQL flaws, the matching generated test fails on each flaw and the clean variant
  passes. The three third-party eval targets (Petstore, VAmPI, DVGA) have **not** been run for real.
- The repository's own tests run on Python 3.11, 3.12 and 3.13 in CI.

What this does not show: detection rates on real third-party APIs, behaviour under production load, or anything about APIs that need OAuth flows
the suite cannot fetch tokens for. Treat a green report as "the checks that ran found nothing".

## Safety first

- **Load, stress, spike, soak, chaos and fuzz runs need a non-production target that you own or are authorized to test.**
  You confirm it by exporting `APITEST_TARGET_ENV` (for example `staging`) and `APITEST_AUTHORIZED=yes` yourself. The skill and
  the generated code never set these for you, and production is always refused.
- Local addresses (`localhost`, `127.x.x.x`, `::1`) pass without those variables, because they are your own machine.
- A target that is not local and has no `APITEST_TARGET_ENV` stops the whole run before a single request is sent (exit code 4
  with a message), instead of failing test by test.
- With `APITEST_TARGET_ENV=production`, only read-only calls are sent. Active probes and every POST/PUT/PATCH/DELETE are skipped.
- Load scripts are GET-only by default and capped (100 requests/s, 200 virtual users, 4 hours) with a circuit breaker at 50 % failures.
- Credentials come from environment variables only and are never printed, logged or written to disk. Reports redact secrets, so a
  `curl` repro line shows `<redacted>` where your credential goes; put your own value back before running it.
- Specs, result files and responses are treated as untrusted input: nothing from them is executed, and generated code embeds
  spec text only as escaped literals.

## Requirements

- Python 3.11 or newer. Intake, generators and the report script use only the standard library (PyYAML is optional).
- `pytest` and `requests` to run the generated suites: `pip install pytest requests`. Tested with Python 3.11, 3.12 and 3.13,
  and with the oldest supported versions (pytest 8.0, requests 2.31, PyYAML 6.0) as well as the newest at the time of writing.
- [k6](https://k6.io/docs/get-started/installation/) only for load, stress, spike and soak runs. Everything else works without it.
- Generating and reporting work offline. Running tests needs network access to the target API.

## Install

Pick one. Every route ends with the same folder: `api-testing/` containing `SKILL.md`, `scripts/`, `references/`, `templates/`.

**1. Claude Code, as a plugin** (this repository is also a one-plugin marketplace):
```
/plugin marketplace add pankajkumar-codes/api-test-pilot-skill
/plugin install api-test-pilot@api-test-pilot
```
The first line registers the marketplace, the second installs the plugin (the part after `@` is the marketplace name).
From a shell the same steps are `claude plugin marketplace add pankajkumar-codes/api-test-pilot-skill` and
`claude plugin install api-test-pilot@api-test-pilot`. These commands only work once the repository is public on GitHub.
The skill then loads automatically when you ask to test an API.

**2. Claude Code, as plain files** (works for any checkout, no plugin system needed):
```bash
git clone https://github.com/pankajkumar-codes/api-test-pilot-skill.git
mkdir -p ~/.claude/skills && cp -r api-test-pilot-skill/skills/api-testing ~/.claude/skills/    # all projects
# or, for one project: mkdir -p .claude/skills && cp -r .../skills/api-testing .claude/skills/
```

**3. Claude app (claude.ai), as an uploaded skill:** build the zip and upload it in Settings, Capabilities, Skills. Menu names
change between releases; see Anthropic's [Using Skills in Claude](https://support.claude.com/en/articles/12512180-using-skills-in-claude) if they differ.
```bash
python tools/build_skill_zip.py        # validates first, writes dist/api-testing-0.1.0.zip with one top-level api-testing/ folder
```

**4. Other agents and marketplaces that read the Agent Skills format:** copy `skills/api-testing/` into that tool's skills folder.
The folder follows the open specification (`name` equals the folder name, `description` states when to use it), checked by
`python tools/validate_skill.py`. Each marketplace has its own submission steps; see [docs/LISTING.md](docs/LISTING.md).

## Quick start

1. Install the skill (next section) and install the test libraries: `pip install pytest requests`.
2. Put the API's spec (OpenAPI/Swagger, GraphQL schema or introspection JSON, Postman collection, or a file of curl commands) in your project.
3. Start your agent and say what you want:

> "Test this API using `openapi.yaml` against `http://localhost:8080`. Run functional and security tests, then give me a report."

The agent follows `skills/api-testing/SKILL.md`: intake, a plan you approve, the safety gate, generation, run, report. It prints the
exact rerun command at the end. The report lands in `reports/report.html` (open it in a browser) with `reports/report.json` next to it.

You can ask for everything, a group or one type in plain words, for example "run only the contract tests" or "load test the GET endpoints".

## Run it yourself, step by step

The scripts are the same ones the agent calls. `SK` points at the scripts folder.

```bash
SK=skills/api-testing/scripts                      # or ~/.claude/skills/api-testing/scripts

# 1. Normalize the spec (GraphQL: add --url http://localhost:4000/graphql)
python $SK/intake.py openapi.yaml -o intake.json --summary

# 2. Credentials and target: environment variables only. intake.py lists the names it expects.
export APITEST_BASE_URL=http://localhost:8080
export APITEST_BEARERAUTH_TOKEN=...                # only if the API needs it

# 3. Generate, run, report
python $SK/gen_pytest.py intake.json -o api_tests --types all
python -m pytest api_tests -p no:cacheprovider -v -ra --junitxml=results/pytest.xml
python $SK/summarize_results.py --junit results/pytest.xml --intake intake.json --base-url "$APITEST_BASE_URL"
# writes reports/report.html and reports/report.json; add -o report.md for Markdown
```

### Pick the test types

| You want | Command | Needs the safety gate? |
|---|---|---|
| **Everything functional and security** | `gen_pytest.py intake.json -o api_tests --types all` | security probes only |
| **One pytest type** | `gen_pytest.py intake.json -o api_tests --types contract` | see table below |
| **A group** | `gen_pytest.py intake.json -o api_tests --types smoke,positive,negative,edge` | see table below |
| **Re-run one type later** | `python -m pytest api_tests/test_security.py -v` | same as the type |
| **One single test** | `python -m pytest "api_tests/test_security.py::test_api1_bola_getPet" -v` | same as the type |
| **Everything including non-functional** | all of the above plus the k6, fuzz and chaos rows below | yes |

The pytest types for `--types` are `smoke`, `positive`, `negative`, `edge`, `data-validation`, `integration`, `contract`, `security`,
`resilience` and `fuzz`. Each one becomes one file, `api_tests/test_<type>.py`. `all` means every type except `fuzz`.
Typical groups: *functional* = `smoke,positive,negative,edge,data-validation,integration`; *API quality* = `contract,resilience`.

| Type | How to run it | Safety gate |
|---|---|---|
| Functional (smoke, positive, negative, edge, data-validation, integration) | `--types` as above, then pytest | no |
| Contract | `--types contract`; spec drift between versions: `python $SK/spec_diff.py old-intake.json new-intake.json` | no |
| Security (OWASP API Top 10) | `--types security` | active probes need a local or confirmed non-production target |
| Resilience | `--types resilience` | no, but it sends bursts; use `APITEST_RPS` on shared targets |
| **Load** | `python $SK/gen_k6.py intake.json -o k6_tests --profiles load` then `python $SK/run_k6.py k6_tests/load.js` | **yes** |
| **Stress** | `--profiles stress`, run `k6_tests/stress.js` | **yes** |
| **Spike** | `--profiles spike`, run `k6_tests/spike.js` | **yes** |
| **Soak** | `--profiles soak`, run `k6_tests/soak.js` (30 min by default; `--duration 5m` shortens it) | **yes** |
| **Chaos** | `python $SK/chaos_run.py intake.json --dry-run` first, then `--experiments proxy-latency,payload`; plan only: `gen_chaos_plan.py intake.json -o chaos-plan.md --env staging` | **yes** |
| **Fuzz** | `--types fuzz` into its own folder, e.g. `-o fuzz_tests`, then pytest | **yes** |

k6 needs [k6](https://k6.io/docs/get-started/installation/) installed. Use `--quick` on `gen_k6.py` for a 10x shorter first run that only checks the
plumbing. k6 scripts are GET-only unless you pass `--include-writes`, and are capped at 100 req/s, 200 virtual users and 4 hours.
Several k6 profiles in one go: `--profiles load,stress`, or `--profiles all`. Pass each k6 summary (`results/<profile>-summary.json`) to the
report with `--k6`, and every pytest result file with `--junit`; both can be repeated.

### The safety gate and environment variables

Load, stress, spike, soak, chaos and fuzz runs, and the active security probes, run only against a target you have confirmed is
**not production** and that you own or are authorized to test. You confirm it yourself:

```bash
export APITEST_TARGET_ENV=staging      # any non-production name; "production" is always refused for these runs
export APITEST_AUTHORIZED=yes
```

The skill and the generated code never set these for you. `localhost`, `127.x.x.x` and `::1` pass without them. A remote target with no
`APITEST_TARGET_ENV` stops the whole run (exit code 4) before a request is sent.

| Variable | What it does |
|---|---|
| `APITEST_BASE_URL` | target to test (overrides the spec's server) |
| `APITEST_TARGET_ENV`, `APITEST_AUTHORIZED` | the safety gate, see above |
| `APITEST_READ_ONLY=yes` | send only GET, HEAD and OPTIONS, no active probes (for public sandboxes) |
| `APITEST_ALLOW_DESTRUCTIVE=yes` | allow tests that delete or overwrite data; they run last |
| `APITEST_<SCHEME>_TOKEN` (for example `APITEST_BEARERAUTH_TOKEN`) | credentials; `intake.py` prints the names your spec needs |
| `APITEST_PATH_<NAME>`, `APITEST_VICTIM_<NAME>` | existing ids for path parameters, and ids owned by another user (for BOLA checks) |
| `APITEST_RPS`, `APITEST_DELAY_MS` | cap the request rate or pause between requests on shared targets |
| `APITEST_FUZZ_CASES`, `APITEST_FUZZ_RPS`, `APITEST_FUZZ_SEED` | size, rate and seed of the fuzz run |

The full list is in `skills/api-testing/references/` (one file per test type).

### The report

`summarize_results.py` writes `reports/report.html`: a single file with no external assets, so it opens offline and can be attached to a ticket
or a CI run. It has the pass/fail verdict, a chart and totals, results per test type (click a type to see every test with its status and reason), findings ranked by severity (each with what happened,
what was expected and a command to reproduce it), k6 numbers with threshold results, per-endpoint coverage, skip reasons and limitations. It follows
your system's light or dark theme. `reports/report.json` has the same data for scripts. Secrets are removed from both. Add `-o report.md` for the older Markdown form.

Notes on running generated suites:
- Tests that delete or overwrite data are marked `destructive`, need `APITEST_ALLOW_DESTRUCTIVE=yes`, and run last.
  With `pytest-xdist` each worker orders its own share, so use `-p no:xdist` (or `-n 0`) when that order matters to you.
- Be polite to shared targets: `APITEST_DELAY_MS=300` (or `--apitest-delay-ms 300`) pauses between requests, `APITEST_RPS=5`
  (or `--apitest-rps 5`) caps the rate; with both, the slower limit applies. A failed connect is retried (GET/HEAD/OPTIONS only,
  `APITEST_RETRIES`, default 2) and then skipped as "target unreachable" (set `APITEST_UNREACHABLE=fail` to fail instead; smoke tests always
  fail). A server that drops or hangs after connecting is a failure, never a skip. Rate-limit probes skip while a throttle is set.
- The safety gate's skips are listed once (`-rs`) with a one-line summary instead of once per test.
- If more than half of the tests skip (missing credentials, ids or gate variables), pytest prints a warning and the report adds
  one, because a green result then covers only the tests that ran. Use `-rs` to see the reasons.

## Example (real output)

Everything below was produced from a clean directory with the shipped zip, a fresh virtual environment and the demo API in
[`examples/`](examples) with two seeded flaws (`bola`, `leak`). The full report is in [examples/sample-report.html](examples/sample-report.html).
The demo API is a local mock written for the project's own tests, not a third-party service.

```text
$ python examples/demo_server.py --bugs bola,leak &
demo API on http://127.0.0.1:8080  (seeded flaws: bola, leak; Ctrl-C to stop)

$ python api-testing/scripts/intake.py pets-api.yaml -o intake.json --summary
Source: openapi 3.0.3
Servers: http://localhost:8080
Auth schemes: bearerAuth (http/bearer)
Endpoints: 12
...
Wrote intake.json (12 endpoints)

$ python api-testing/scripts/gen_pytest.py intake.json -o api_tests --types all
Generated 132 test functions in 12 files under api_tests
  smoke: 7
  positive: 13
  negative: 20
  ...
Rerun with:
  APITEST_BASE_URL=http://localhost:8080 python -m pytest api_tests -v -ra

$ APITEST_BASE_URL=http://127.0.0.1:8080 APITEST_BEARERAUTH_TOKEN=tokA APITEST_VICTIM_PETID=2 APITEST_PATH_PETID=1 \
  APITEST_PATH_TOYID=7 APITEST_VICTIM_TOYID=8 python -m pytest api_tests -p no:cacheprovider -q --junitxml=results/pytest.xml
...
FAILED api_tests/test_security.py::test_api1_bola_getPet - Failed: [HIGH][API...
FAILED api_tests/test_security.py::test_api1_bola_listToys - Failed: [HIGH][API...
FAILED api_tests/test_security.py::test_api1_bola_getToy - Failed: [HIGH][API...
FAILED api_tests/test_security.py::test_api3_no_sensitive_fields_listPets - F...
...
7 failed, 239 passed, 15 skipped in 0.88s

$ python api-testing/scripts/summarize_results.py --junit results/pytest.xml --intake intake.json \
    --base-url http://127.0.0.1:8080 -o reports/report.html
Wrote reports/report.json
Wrote reports/report.html (7 findings, 261 tests, 0 k6 summaries)
```

The report opens with `FAIL: 7 finding(s): HIGH 5, MEDIUM 2.` The BOLA and data-exposure failures are the seeded
flaws. The three `/admin/stats` failures come from using a non-admin token on an admin endpoint; they are not seeded flaws.

The safety stop, on a remote target without `APITEST_TARGET_ENV`:
```text
$ APITEST_BASE_URL=https://api.example.invalid python -m pytest api_tests -q
ERROR: Target api.example.invalid is not local, and APITEST_TARGET_ENV is not set. Export APITEST_TARGET_ENV to the
environment you are testing (dev, staging, ...; 'production' limits the run to read-only calls) before running. ...
$ echo $?
4
```

## What has and has not been validated

- Unit, generator, hostile-spec, safety-gate and reporting tests: `python -m pytest tests` (337 pass and 18 skip when only `requirements-dev.txt` is installed, i.e. without k6 and the FastAPI extras; about two and a half minutes
  on 4 cores; with k6 on the path the generated k6 scripts are executed too and it takes several minutes longer).
  Run on Python 3.11, 3.12 and 3.13 (the CI workflow runs the same three).
- Detection of seeded flaws: 26 of 27 found; the gap (VAmPI mass assignment) is documented. Targets that could not be reached
  from the build sandbox ran as labelled mocks; see [docs/evals.md](docs/evals.md).
- **Not yet done:** runs against the real Petstore, VAmPI and DVGA, which you can do with [evals/README.md](evals/README.md).

## Develop

```bash
pip install -r requirements-dev.txt -r evals/requirements.txt   # evals/requirements.txt (fastapi, uvicorn) is only for the seeded eval target
python -m pytest tests                     # add k6 (K6_BIN or PATH) to also execute the generated k6 scripts
python tools/validate_skill.py             # SKILL.md frontmatter, folder layout, plugin files, version consistency
python evals/run_evals.py --target all --markdown
```
Contributing: [CONTRIBUTING.md](CONTRIBUTING.md). Changes: [CHANGELOG.md](CHANGELOG.md). Design decisions: [DECISIONS.md](DECISIONS.md).
Build plan and status: [PLAN.md](PLAN.md). Instructions for Claude Code in this repo: [CLAUDE.md](CLAUDE.md).

`intake.json` (parser output) describes your API; it is git-ignored, do not commit it.

## Repository layout
```
skills/api-testing/     the skill: SKILL.md (router), references/ (loaded on demand), scripts/, templates/
.claude-plugin/         plugin.json and marketplace.json (Claude Code plugin packaging)
tools/                  validate_skill.py, build_skill_zip.py (repository tooling, not part of the skill)
examples/               demo API, sample spec, a real sample report
tests/  evals/  docs/   tests, eval driver and targets, validation record and release docs
```

## Security and responsible use
See [SECURITY.md](SECURITY.md): how to report a vulnerability privately, and the rules for testing only systems you own or may test.

## License
[MIT](LICENSE). The vendored OpenAPI documents in `evals/specs/` are test fixtures under their upstream projects' own licenses.
