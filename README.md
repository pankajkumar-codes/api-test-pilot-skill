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
| Chaos | a fault-injection **plan** only; nothing is injected |
| Reporting | one Markdown report: coverage per endpoint, findings ranked by severity, a repro command for each |

Protocols: REST (OpenAPI 3.x and Swagger 2) and GraphQL. Out of scope for v1: gRPC, mobile, UI accessibility, a hosted service.
Full list of test types: [TAXONOMY.md](TAXONOMY.md).

> **Status: 0.1.0, not yet released.** The code is complete and tested; the release itself (tag, marketplace listings) is
> still to be done. See [docs/RELEASE.md](docs/RELEASE.md). Two third-party targets (Petstore, VAmPI, DVGA) have not been run
> for real yet, only against labelled mock stand-ins: read [docs/evals.md](docs/evals.md) before you trust the detection numbers.

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
- `pytest` and `requests` to run the generated suites: `pip install pytest requests`.
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

## Usage

Tell your agent what to test, where it is running, and how to authenticate. Credentials go in environment variables, never in chat.

> "Test this API using `openapi.yaml` against `http://localhost:8080`. Run functional and security tests, then give me a report."

The agent then follows `skills/api-testing/SKILL.md`: intake, a plan you approve, the safety gate, generation, run, report.
You can also run every step yourself. The scripts are the same ones the agent calls.

```bash
SK=skills/api-testing/scripts                      # or ~/.claude/skills/api-testing/scripts

python $SK/intake.py openapi.yaml -o intake.json --summary          # or schema.graphql --url http://localhost:4000/graphql
python $SK/gen_pytest.py intake.json -o api_tests --types all       # prints the rerun command and the env vars it needs
APITEST_BASE_URL=http://localhost:8080 APITEST_BEARERAUTH_TOKEN=... python -m pytest api_tests -v -ra
python -m pytest api_tests -p no:cacheprovider --junitxml=results/pytest.xml
python $SK/summarize_results.py --junit results/pytest.xml --intake intake.json --base-url http://localhost:8080 -o reports/report.md
python $SK/spec_diff.py old-intake.json new-intake.json             # breaking changes between two spec versions
```

Non-functional runs (after you have confirmed the target; see Safety):
```bash
export APITEST_TARGET_ENV=staging APITEST_AUTHORIZED=yes            # yours to set, never the agent's
python $SK/gen_k6.py intake.json -o k6_tests --profiles all --quick # --quick: 10x shorter, checks the plumbing first
python $SK/run_k6.py k6_tests/load.js --base-url https://staging.example.com
python $SK/gen_pytest.py intake.json -o fuzz_tests --types fuzz
python $SK/gen_chaos_plan.py intake.json -o chaos-plan.md --env staging
```

Notes on running generated suites:
- Tests that delete or overwrite data are marked `destructive`, need `APITEST_ALLOW_DESTRUCTIVE=yes`, and run last.
  With `pytest-xdist` each worker orders its own share, so use `-p no:xdist` (or `-n 0`) when that order matters to you.
- If more than half of the tests skip (missing credentials, ids or gate variables), pytest prints a warning and the report adds
  one, because a green result then covers only the tests that ran. Use `-rs` to see the reasons.

## Example (real output)

Everything below was produced from a clean directory with the shipped zip, a fresh virtual environment and the demo API in
[`examples/`](examples) with two seeded flaws (`bola`, `leak`). The full report is in [examples/sample-report.md](examples/sample-report.md).
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
    --base-url http://127.0.0.1:8080 -o reports/report.md
Wrote reports/report.md (7 findings, 261 tests, 0 k6 summaries)
```

The report starts with `**Result: FAIL** — 7 finding(s): HIGH 5, MEDIUM 2.` The BOLA and data-exposure failures are the seeded
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

- Unit, generator, hostile-spec, safety-gate and reporting tests: `python -m pytest tests` (several hundred tests, about a minute;
  with k6 on the path, about five more minutes because the generated k6 scripts are executed too).
- Detection of seeded flaws: 26 of 27 found; the gap (VAmPI mass assignment) is documented. Targets that could not be reached
  from the build sandbox ran as labelled mocks; see [docs/evals.md](docs/evals.md).
- **Not yet done:** runs against the real Petstore, VAmPI and DVGA, which you can do with [evals/README.md](evals/README.md).

## Develop

```bash
pip install -r requirements-dev.txt
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

## License
[MIT](LICENSE). The vendored OpenAPI documents in `evals/specs/` are test fixtures under their upstream projects' own licenses.
