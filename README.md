# api-test-pilot

An [Agent Skill](https://agentskills.io/specification) that plans, generates and runs API tests from an OpenAPI spec,
a GraphQL schema, a `.proto` file, a Postman collection or curl examples. It writes plain pytest and k6 files you can read, edit and keep.

**In:** an OpenAPI spec. **Out:** a folder of plain, runnable pytest files, and the exact command to run them again.

```bash
python skills/api-testing/scripts/run_all.py openapi.yaml --out apitest_run   # prints the rerun command at the end
```

### What is stable and what is not

Each level is earned by evidence, and a feature moves up only when it meets every point of the next level:

- **Stable**: covered by the required CI tests on Python 3.11 to 3.13, and run end to end on at least one service that was not written for this
  project, with the results recorded in this README or [docs/evals.md](docs/evals.md). Flags and output change only with a CHANGELOG entry.
- **Beta**: covered by CI tests and run end to end on at least one service not written for this project, with results recorded, but the runs are
  few or narrow and known gaps are listed. Expect rough edges.
- **Experimental**: works, but has only been run against targets written for this project (mocks, seeded apps, stand-ins, fixture files), or needs
  an optional tool that CI does not install. It can change or be removed between 0.x releases.
- **Planned**: not built yet.

"A service not written for this project" so far means the maintainer's own Spring Boot demo (written earlier to learn Spring Boot, before and apart
from this skill, with no seeded flaws) and the four public sandbox APIs described under [Results](#results-what-has-been-run). Two bugs found in
the demo during these runs were fixed there. The Beta labels for resilience, k6, REST fuzzing and chaos rest on that one demo alone. The mocks, the seeded FastAPI, gateway, GraphQL and gRPC apps and the Petstore, VAmPI and DVGA stand-ins
do not count, because the same author wrote the flaws and the checks.

| Feature | Status | Evidence and what is missing |
|---|---|---|
| OpenAPI / Swagger in, pytest out (smoke, positive, negative, edge, data-validation, contract, integration) | Stable | The core. Required CI on 3.11 to 3.13; run on the Spring Boot demo and the four public sandbox APIs. |
| HTML report, rerun command per finding | Stable | HTML report produced for every run above, with JSON alongside it where the runner wrote one. |
| `spec_diff.py` breaking-change report | Experimental | Tested in CI on fixture spec pairs only; not yet run on two real versions of someone else's spec. |
| Security checks (OWASP API Top 10) | Beta | Run on the Spring Boot demo (no findings) and the public APIs (CORS and header findings that look real, not confirmed with the providers). BOLA, BFLA, mass assignment and injection detection is shown only on seeded flaws the author wrote; VAmPI mass assignment is a known miss. Expect false positives and read each finding. Stable needs real VAmPI or crAPI runs. |
| Resilience (idempotency, pagination, rate limits) | Beta | Run on the Spring Boot demo only, including the rate-limit tests; the public API runs left them out. |
| k6 load, stress, spike, soak | Beta | Real k6 runs in an optional CI job (not required to merge). Load, stress, spike and a 30-minute soak ran on the Spring Boot demo with 0 failed requests, on GET endpoints at the default rates. Capacity ceilings have not been measured, and only Linux has been checked. GET-only by default (`--k6-writes` opts in) and capped. |
| Fuzzing, REST | Beta | Seeded and rate-limited. Found a real 500 on the Spring Boot demo (page number overflow), which was then fixed in the demo. |
| Fuzzing, GraphQL | Experimental | Opt-in (`--types fuzz`). Run against the GraphQL mock only. |
| Chaos (local fault proxy, payload bursts, your own dependency commands) | Beta | Run on the Spring Boot demo with a real PostgreSQL stopped and frozen; found a slow-failure problem (`dep-slow`) that was fixed in the demo and rechecked; the second finding, `dep-blackhole`, was not rechecked after the fix. Loopback proxies only; HTTPS through the proxy, remote targets and Windows are not checked. |
| GraphQL input | Experimental | Generated suites run against a GraphQL mock and a seeded local server, both written for this project. No independent GraphQL API (DVGA or other) has been run. |
| Postman and curl input | Experimental | Parsed from fixture files only; no real-world collection has been run end to end. |
| gRPC | Experimental | Unary calls only, needs the optional `grpcurl`, which CI does not install. Run against a local `grpcio` server written for the tests. gRPC load tests (`ghz`) are planned and not generated yet. |
| Real Petstore, VAmPI, DVGA and crAPI evals | Planned | Only labelled local stand-ins have been run so far. |
| GitHub Action, MCP server | Planned | Not started. The MCP server is planned for v0.2 with a gate the agent cannot answer for (see DECISIONS.md, v0.2). |

Status means how much evidence there is, not a promise of support. Last reviewed 2026-11-24 against the runs recorded in [docs/evals.md](docs/evals.md).

| Area | What it produces |
|---|---|
| Functional | smoke, positive, negative, edge, data-validation, integration flows (pytest) |
| Contract | responses checked against the spec; breaking-change report between two spec versions |
| Security | OWASP API Top 10 checks: authN/authZ, BOLA, BFLA, injection, mass assignment (the created record is read back where the spec has a read endpoint), SSRF, data exposure, CORS and headers |
| Resilience | idempotency, retries, pagination, rate-limit behaviour |
| Load, stress, spike, soak | k6 scripts with hard caps, plus a launcher |
| Fuzzing | seeded, rate-limited malformed-input suite (REST) |
| Chaos | a fault-injection plan, plus runnable experiments (local fault proxies, dependency start/stop commands you supply, malformed and oversized payload bursts) with recovery-time measurement; same safety gate |
| Reporting | one self-contained HTML report (plus JSON): results per test type, coverage per endpoint, findings ranked by severity, a repro command for each |

Protocols: REST (OpenAPI 3.x and Swagger 2, including specs split over several files, with webhooks and callbacks listed) and GraphQL, plus gRPC unary calls (needs the optional `grpcurl` tool; functional, contract and security tests, tested against a real local gRPC server; load tests are planned next).
Scope: gRPC streaming is not covered. **Not part of this skill at all:** UI accessibility and mobile app testing
(they are not API testing; accessibility could become a separate skill) and a hosted service (the skill runs in your environment).
Full list of test types: [TAXONOMY.md](TAXONOMY.md).

> **Version 0.1.0 is the first version.** The code is public and tested, but no release tag has been cut and it is not yet listed in any
> skill marketplace; that is the remaining release work, see [docs/RELEASE.md](docs/RELEASE.md). The third-party eval targets
> (Petstore, VAmPI, DVGA, crAPI) have **not** been run for real, only against labelled mock stand-ins written by the same author, so the detection numbers
> show that the checks work on those seeded flaws and nothing more. Read [docs/evals.md](docs/evals.md) before you trust them.

## Results: what has been run

The skill has been run end to end on a real service and on public sandboxes, and the numbers below are from those runs. What each run
did and did not cover is stated next to it. Detection numbers against *seeded* flaws are in [docs/evals.md](docs/evals.md).

**Open the report:** [spring-boot-demo, real run (HTML)](examples/spring-boot-demo/report.html) ·
[deliberately flawed demo API, 9 findings (HTML)](examples/sample-report.html) ·
[four public sandbox APIs](examples/public-sandbox/README.md). GitHub shows HTML files as source, so download the file and open it in a browser.
Each report is one self-contained file (no external scripts, styles or fonts) and was checked to open with no network connection.

### A real Spring Boot service (local, throwaway database)

Target: a Spring Boot 4 REST demo built from source and run on `localhost:8080` against a throwaway PostgreSQL 16. The spec was
written by hand from the controller (7 endpoints, [openapi.json](examples/spring-boot-demo/openapi.json)). Nothing outside the sandbox was touched. This is the latest run, with the fuzz bug in the demo fixed
before it and before the `dep-slow` fix described below; one report covers all of it: [report.html](examples/spring-boot-demo/report.html).

| Run | Tests | Pass | Fail | Skip | Outcome |
|---|---|---|---|---|---|
| Functional: smoke, positive, negative, edge, data-validation, contract, security, integration, resilience (incl. 2 rate-limit tests) | 166 | 166 | 0 | 0 | no findings |
| Fuzz (seeded malformed inputs, after the bug below was fixed) | 7 | 7 | 0 | 0 | no findings |
| Chaos: latency, 5xx, dropped connections, payload bursts, database outage and freeze | 16 | 15 | 1 | 0 | **1 MEDIUM**: with the database frozen, requests failed after about 5.0 s |
| **All tests** | **189** | **188** | **1** | **0** | report verdict FAIL (1 MEDIUM) |
| k6 load, stress, spike, 30-minute soak (GET endpoints only) | 4 profiles | 4 | 0 | 0 | 0 failed requests, p95 2.8 to 4.7 ms |

The 189 are the pytest and chaos tests; the k6 profiles are reported separately with their thresholds, none crossed.

An earlier fuzz run found a real bug: `GET /api/v1/software-engineers?page=2147483647` returned 500, because page times size overflowed Spring Data's
integer offset. It was fixed in the demo project, and the fuzz run above passed 7 of 7 afterwards. The chaos finding was a frozen database taking about 5 s to fail instead of failing fast: the demo set no read timeout on its database connection.
That was "at the limit" of the 5 s default, not a measured hang (see [docs/evals.md](docs/evals.md)). It is now fixed in the demo
([spring-boot-demo#5](https://github.com/pankajkumar-codes/spring-boot-demo/pull/5): JDBC socket timeout, frozen database answers 503 in 2 to 3 s). The table and report above are the run
before that fix. On 2026-11-17 the chaos suite was rerun against the fixed demo (spring-boot-demo main 0acc5b5, local PostgreSQL 16, six experiments: proxy-latency, proxy-5xx, proxy-drop, payload, dep-outage, dep-slow): all 16 checks passed, and `dep-slow` now shows 4 of 8 requests failing during the freeze (p95 4.0 s, recovery 0.8 s) and no finding. The example report was not regenerated, and the second chaos finding (`dep-blackhole`) was not rechecked.

What was not done on this target, so you can judge the numbers:
- k6 ran **GET endpoints only, at the default rates** (about 4 to 10 req/s on average, up to 50 virtual users). That shows the demo is comfortable
  at that rate on a small machine. It is not a capacity limit.
- The rate-limit tests ran without pacing, the rest at 40 req/s, because the skill skips its burst probes when a rate cap is set.
- Writes and intrusive probes ran only because the database was disposable. HTTPS and authentication were not covered (the demo has none).
- Chaos cases show no request or response bodies in the report (they are not captured through HTTP).

### Four public sandbox APIs (read-only)

Rerun on 2026-11-14 with the current version: JSONPlaceholder, ReqRes, HTTPBin and Webhook.site, with `APITEST_READ_ONLY=yes` (only GET, HEAD and OPTIONS),
a 0.3 s pause between requests, no credentials, and no load, fuzz, stress, spike, soak, chaos or resilience tests. Reports and specs: [examples/public-sandbox](examples/public-sandbox/README.md).

| API | Tests | Pass | Fail | Skip | Findings |
|---|---|---|---|---|---|
| JSONPlaceholder | 378 | 24 | 2 | 352 | 1 HIGH (CORS reflects any origin with credentials), 1 LOW (no HSTS, `X-Powered-By`) |
| ReqRes | 192 | 21 | 11 | 160 | 10 MEDIUM (the list endpoints accept invalid `page` and `per_page`), 1 LOW (`X-Powered-By`) |
| HTTPBin | 413 | 139 | 8 | 266 | 3 HIGH (CORS reflects any origin with credentials; `/cookies/set` and `/cookies/delete` answer 302 where the spec says 200), 4 MEDIUM (the same cookie mismatch), 1 LOW (no HSTS) |
| Webhook.site | 55 | 2 | 0 | 53 | none |
| **Total** | **1038** | **186** | **21** | **831** | |

80% of the tests were skipped, all by design: read-only mode never sends writes (320 skips) or active probes (406), and the rest need ids or data
the run did not have. So this run says little about writes and nothing about load. The first run on 2026-11-12 (1098 tests, 52 failures) had many false
positives; those were fixed in the skill, and the failures that remain are the API's behaviour or mistakes in the hand-written specs (the cookie mismatch,
the strict `page` rules). Whether the CORS and header findings matter for a public demo API is your call. Three of the four specs were written by hand.

### Seeded flaws and tests of the skill itself
- On the demo API in [`examples/`](examples) with two seeded flaws, the skill found both (BOLA, 5 findings, and data exposure, 1 finding). The other 3 failures come from
  using a non-admin token on an admin endpoint; the [sample report](examples/sample-report.html) shows all 9.
- 28 flaws were seeded across local targets (13 REST flaws in mock stand-ins for Petstore and VAmPI, 5 in a seeded FastAPI app run as a real local server,
  10 in a GraphQL mock). The matching generated test failed on 27 of them (the gap is VAmPI mass assignment) and the clean variants passed apart from one listed limitation.
  The third-party eval targets (Petstore, VAmPI, DVGA, crAPI) have **not** been run for real.
- The repository's own tests run on Python 3.11, 3.12 and 3.13 in CI.

What this does not show: detection rates on real third-party APIs, behaviour under production load, or anything about APIs that need OAuth flows
the suite cannot fetch tokens for. Treat a green report as "the checks that ran found nothing".

## Safety first

**The safety gate is a consent prompt, not an enforced control.** It stops honest mistakes: running a load test at the wrong URL, or
forgetting that a target is shared. It does not stop anyone who is set on doing harm, and this project does not call itself safe by default.
In particular:
- The gate reads environment variables. Anyone, or any program or agent with a shell, can set them. The skill tells an agent never to set
  `APITEST_AUTHORIZED` or `APITEST_TARGET_ENV` itself, and the repository tests that this text stays in place, but an instruction is not a lock.
- A remote system reached through a tunnel, port-forward or local proxy looks like `localhost` and passes without the variables.
- `APITEST_TARGET_ENV=staging` is a label you type. Nothing checks that the target really is staging.
- A spec, collection or API response is untrusted text and could try to talk an AI agent into exporting the variables. The skill says to ignore
  that and a test covers it, but review what an agent runs before you let it run.

What the gate does:

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

You are responsible for what you point this tool at. Only test systems you own or have written permission to test; see
[SECURITY.md](SECURITY.md#legal-and-misuse).

## Requirements

Only Python, `pytest` and `requests` are needed for everything except the features marked below. A tool you do not use is never
checked: nothing imports it, and the other features run the same whether it is installed or not.

| Tool | Needed for | Install |
|---|---|---|
| Python 3.11+ | everything (intake, generators, report use only the standard library) | python.org, or your package manager |
| `pytest`, `requests` | running any generated suite | `pip install pytest requests` |
| PyYAML (optional) | faster YAML specs; there is a built-in fallback | `pip install pyyaml` |
| [k6](https://k6.io) | load, stress, spike and soak runs only | see step 3 below |
| [grpcurl](https://github.com/fullstorydev/grpcurl) | gRPC tests only (without it they skip and say so) | see step 4 below |
| [ghz](https://ghz.sh) | gRPC load tests only (planned, not generated yet) | see step 5 below |
| fastapi, uvicorn, strawberry-graphql | the local gateway and GraphQL examples in `examples/aws-local` and their tests (`pip install -r examples/aws-local/requirements.txt`) | pip |
| Docker | only for running a local emulator or demo services yourself | docker.com |

`grpcio` is **not** needed: the gRPC tests call `grpcurl` as a program. Generating and reporting work offline; running tests needs network
access to the target API. Tested with Python 3.11 to 3.13, and with the oldest supported pytest 8.0, requests 2.31 and PyYAML 6.0.

Step by step:
1. Install Python 3.11 or newer and check it: `python --version`.
2. `pip install pytest requests` (add `pyyaml` if you like).
3. Load tests only: install k6 (macOS `brew install k6`, Windows `winget install k6`, Debian/Ubuntu see the
   [k6 install guide](https://grafana.com/docs/k6/latest/set-up/install-k6/)). Check: `k6 version`.
4. gRPC tests only: install grpcurl (macOS `brew install grpcurl`, Go `go install github.com/fullstorydev/grpcurl/cmd/grpcurl@latest`,
   or a binary from the [releases page](https://github.com/fullstorydev/grpcurl/releases)). Check: `grpcurl -version`. If it is not on
   `PATH`, set `APITEST_GRPCURL=/path/to/grpcurl`.
5. gRPC load tests only (when they arrive): install ghz (`brew install ghz` or a binary from the [releases page](https://github.com/bojand/ghz/releases)).

## Install

Pick one. Every route ends with the same folder: `api-testing/` containing `SKILL.md`, `scripts/`, `references/`, `templates/`.

**1. Claude Code, as a plugin** (this repository is also a one-plugin marketplace):
```
/plugin marketplace add pankajkumar-codes/api-test-pilot
/plugin install api-test-pilot@api-test-pilot
```
The first line registers the marketplace, the second installs the plugin (the part after `@` is the marketplace name).
From a shell the same steps are `claude plugin marketplace add pankajkumar-codes/api-test-pilot` and
`claude plugin install api-test-pilot@api-test-pilot`. The repository is public, so these work from any machine (checked with Claude Code on a clean profile).
The skill then loads automatically when you ask to test an API; to call it by name, use `/api-test-pilot:api-testing`.

**2. Claude Code, as plain files** (works for any checkout, no plugin system needed):
```bash
git clone https://github.com/pankajkumar-codes/api-test-pilot.git
mkdir -p ~/.claude/skills && cp -r api-test-pilot/skills/api-testing ~/.claude/skills/    # all projects
# or, for one project: mkdir -p .claude/skills && cp -r .../skills/api-testing .claude/skills/
```

**3. Claude app (claude.ai), as an uploaded skill:** from the repository root, build the zip and upload it in Settings, Capabilities, Skills. Menu names
change between releases; see Anthropic's [Using Skills in Claude](https://support.claude.com/en/articles/12512180-using-skills-in-claude) if they differ.
```bash
python tools/build_skill_zip.py        # validates first, writes dist/api-testing-0.1.0.zip with one top-level api-testing/ folder, and dist/SHA256SUMS
```
If you download the zip from a GitHub release instead, download `SHA256SUMS` from the same release into the same folder and check it with
`sha256sum -c SHA256SUMS` (macOS: `shasum -a 256 -c SHA256SUMS`). It must print `api-testing-0.1.0.zip: OK`.

**4. Other agents and marketplaces that read the Agent Skills format:** copy `skills/api-testing/` into that tool's skills folder.
The folder follows the open specification (`name` equals the folder name, `description` states when to use it), checked by
`python tools/validate_skill.py`. Each marketplace has its own submission steps; see [docs/LISTING.md](docs/LISTING.md).

## Quick start

1. Install the skill (see Install above) and install the test libraries: `pip install pytest requests`.
2. Put the API's spec (OpenAPI/Swagger, GraphQL schema or introspection JSON, Postman collection, or a file of curl commands) in your project.
3. Start your agent and say what you want:

> "Test this API using `openapi.yaml` against `http://localhost:8080`. Run functional and security tests, then give me a report."

The agent follows `skills/api-testing/SKILL.md`: intake, a plan you approve, the safety gate, generation, run, report. It prints the
exact rerun command at the end. The report lands in `reports/report.html` (open it in a browser) with `reports/report.json` next to it.

You can ask for everything, a group or one type in plain words, for example "run only the contract tests" or "load test the GET endpoints".

Prefer a single command without an agent? `python skills/api-testing/scripts/run_all.py openapi.yaml --out apitest_run` runs intake, generation,
both test passes (session-ending tests such as sign-out run last) and the report, then prints what to set to un-skip the remaining tests.
`--check-env` only lists missing tools. Add `--types all,fuzz` for the opt-in fuzz suite. Add `--k6` (or `--k6=load,soak`; default load, stress, spike) and `--chaos` to run those after the tests and put them in the same report; `--k6-writes` also generates POST/PUT/PATCH requests (they still need `APITEST_ALLOW_WRITES=yes`, which you set), `--k6-docker` runs k6 in a container, and chaos dependency faults need `--dependency-config FILE --allow-hooks`. k6 and chaos go through their own scripts and gates, so a non-local target still needs your `APITEST_TARGET_ENV` and `APITEST_AUTHORIZED=yes`; when the gate refuses, the driver says so and skips that part. The safety gate still applies; the driver never sets it.
Exit code 0 unless the verdict is FAIL (an INCOMPLETE or INCONCLUSIVE run also exits 0, so read the verdict), 1 findings, 2 a step could not run. Extra pytest arguments go to both passes, so do not pass your own `-m`.

## Run it yourself, step by step

The scripts are the same ones the agent calls. `SK` points at the scripts folder.

```bash
SK=skills/api-testing/scripts                      # or ~/.claude/skills/api-testing/scripts

# 1. Normalize the spec (GraphQL: add --url http://localhost:4000/graphql)
python $SK/intake.py openapi.yaml -o intake.json --summary
# Spec declares no security scheme but the API needs a token? Add: --assume-bearer --public-paths '/health,/docs*'

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
`resilience` and `fuzz`. Each one becomes one file, `api_tests/test_<type>.py`. `all` means every type except `fuzz`. GraphQL schemas support `smoke`, `positive`, `negative`, `edge`, `security`, `contract`, `integration`, `data-validation`, `resilience` and `fuzz`: every pytest type works for GraphQL.
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

With `APITEST_RPS` or `APITEST_DELAY_MS` set, the rate-limit tests cannot send their burst and skip. `run_all.py` handles this for a target on this machine: it runs them in a separate burst pass without the pacing (after the main pass, before any sign-out tests) and reports every test once. For any other target it keeps your pacing and prints the command to run them (`pytest -m rate_limit`) when a short burst is acceptable. `--expect-rate-limit` tells it the API is meant to throttle.

k6 needs [k6](https://k6.io/docs/get-started/installation/) installed. Use `--quick` on `gen_k6.py` for a 10x shorter first run that only checks the
plumbing. k6 scripts are GET-only unless you pass `--include-writes`, and are capped at 100 req/s, 200 virtual users and 4 hours.
For k6 runs longer than your access token lives, set `APITEST_TOKEN_CMD` (the same variable the pytest suite uses) to a command that prints a fresh token (and `APITEST_TOKEN_REFRESH_S` to refresh on a timer); `run_k6.py` feeds it to the script and the token is never printed. The command runs once per renewal, not once per virtual user: at most every `APITEST_TOKEN_MIN_GAP_S` seconds (default 30) and `APITEST_TOKEN_MAX_REFRESHES` times (default 200), and the run ends with "token refreshed N time(s)". On an API that allows one live session per user (each sign-in ends the previous one) or rate-limits sign-ins, let the command return a young cached token.

k6 reads the same `APITEST_EXAMPLES_FILE` and `APITEST_IDS_FILE` as the pytest suite, so load tests send valid values (a first-of-month date, a real id) instead of invented ones; the generator lists the fields that still use an invented value.

Several k6 profiles in one go: `--profiles load,stress`, or `--profiles all`. On a local target the stress steps double up to the 100 req/s cap (5, 10, 20, 40, 80, 100) so a limit is found; `--ramp linear` gives the gentler plan, and remote targets keep it by default. Pass each k6 summary (`results/<profile>-summary.json`) to the
report with `--k6`, and every pytest result file with `--junit`; both can be repeated. Add `--k6-dir k6_tests` (the folder with `manifest.json`) so the report can show the peak rate and virtual users reached beside the caps and, for stress, the last good and first bad step (or "no limit found up to X req/s"). Results in a sibling folder: add `--root ..`.

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
| `APITEST_SEND_OPTIONAL_HEADERS` | comma list of headers the spec marks optional but the server needs, for example `Idempotency-Key,If-Match`. Sent on every operation that declares them: `Idempotency-Key` gets a fresh unique value per call, others get `*` or the value after `=` (`If-Match=\"v1\"`) |
| `APITEST_TENANT_WRITES` | `yes` also calls write operations against the other tenant in the tenant isolation matrix (default: reads only). The control call in your own tenant reads the record first and retries once with its current ETag if the server answers 412 or 428; if it is still refused, the test skips and says why, instead of reporting a false pass or failure |
| `APITEST_ROLE_MATRIX`, `APITEST_TOKEN_<ROLE>` | JSON file `{"createFiling": {"allow": ["owner"], "deny": ["billing"]}}` and one token per role, for the role (BFLA) matrix. Do not name a role `cmd` (`APITEST_TOKEN_CMD` is the token command). Role calls send only the role's token, so `APITEST_COOKIES` and the token command are not used: cookie-session APIs cannot be role-tested yet |
| `APITEST_TOKEN_CMD`, `APITEST_TOKEN_CMD_B` | a command (split into arguments, run without a shell) that prints a fresh token. Run when a call that carries credentials gets 401, at most once per 5 s and 100 times per run, then the call is sent again. For tokens that expire during a long run. Used by the generated pytest suites only (not k6, fuzz runs or GraphQL suites). A test that expects a 401 with the default token can have it hidden by the retry when this is set. The output is never printed |
| `APITEST_LOGIN_EMAIL`, `APITEST_LOGIN_USERNAME`, `APITEST_LOGIN_PASSWORD`, `APITEST_LOGIN_OTP` | a real account for sign-in style operations (POST to a path with login, sign-in, authenticate, token, session or auth): the values go into body fields of that kind (email, username, password, otp). Kept out of reports like other secrets |
| `APITEST_COOKIES` | `name=value; name2=value2` cookies sent with every call made with the default credentials (also on operations with no security scheme, never on the no-credentials and bad-credentials probes), for example an HttpOnly refresh token |
| `APITEST_IDS_FILE` | JSON file `{"path": {"business_id": "..."}, "victim": {...}}` with the same ids, instead of many variables (a variable wins over the file). The run ends with a list of the variables that would unskip the most tests |
| `APITEST_EXAMPLES_FILE` | JSON file `{"createFiling.period_start": "2024-04-01", "*.state_code": "27"}`: valid values for body fields and query parameters the spec gives no example for. The report lists the fields that need one |
| `APITEST_PATH_<NAME>`, `APITEST_VICTIM_<NAME>` | existing ids for path parameters, and ids owned by another user (for BOLA checks) |
| `APITEST_UNIQUE_BODIES=no`, `APITEST_RUN_ID` | stop tagging unique-looking create fields (`email`, `username`, `slug` ...) with a run id, or pin its 4-character prefix to reproduce a run (a rerun then repeats the same values) |
| `APITEST_RPS`, `APITEST_DELAY_MS` | cap the request rate or pause between requests on shared targets |
| `APITEST_FUZZ_CASES`, `APITEST_FUZZ_RPS`, `APITEST_FUZZ_SEED` | size, rate and seed of the fuzz run. The request budget is split between endpoints; the rate defaults to 50/s against a loopback target and 5/s elsewhere |
| `APITEST_FUZZ_ENDPOINTS` | fuzz only these operation ids, e.g. `createPet,listPets`; the run ends with a budget summary per endpoint |

The full list is in `skills/api-testing/references/` (one file per test type).

### The report

`summarize_results.py` writes `reports/report.html`: a single file with no external assets, so it opens offline and can be attached to a ticket
or a CI run. It has the pass/fail verdict, a chart and totals, results per test type (click a type to see every test with its status, reason, the request sent and the response received; secrets are redacted), findings ranked by severity (each with what happened,
what was expected and a command to reproduce it), k6 numbers with threshold results, per-endpoint coverage, skip reasons and limitations. Chaos runs show up as their own test type
(`chaos_run.py` writes a JUnit file you pass with `--junit`; chaos cases have no request or response bodies). It follows
your system's light or dark theme. `reports/report.json` has the same data for scripts. Secrets are removed from both. Add `-o report.md` for the older Markdown form.
The summary gives passed, failed and could-not-run counts and the variables to set to run the skipped tests (with how many tests each unskips); findings are also grouped by root cause (one group per repeated test). A run whose only findings are informational gets the verdict NOTES, not FAIL. Skipped tests are listed under four headings with a count each, by who can fix them: the skill could have done it itself, you need to supply something, a safety default is off on purpose, or the target cannot provide it (`skip_groups` in `report.json`; the flat `skips` list stays). A refusal (4xx) of a value the skill had to invent because the spec gives no example, default or enum, or a 412 for a stale `If-Match`, is not a failure: it skips as "could not verify", counts as could not run, and the report lists the fields that need an example (set them in `APITEST_EXAMPLES_FILE`). Operations with a required `If-Match` header read the record first and send its current ETag.

### Create the records the tests need (optional)

Most skipped tests say "no existing id for X". `seed_ids.py` is an opt-in setup stage that makes those ids: for every resource with a create endpoint and an item endpoint it sends one create request and writes the new id to an ids file (the same `APITEST_IDS_FILE` the suite reads). Nothing calls it for you:

```
python $SK/seed_ids.py intake.json --out ids.json --dry-run     # print what it would create, send nothing
python $SK/seed_ids.py intake.json --out ids.json               # create the records, write ids.json
APITEST_IDS_FILE=ids.json pytest api_tests                      # run the suite with them
python $SK/seed_ids.py intake.json --cleanup ids.json           # delete what it created
```

It writes data, so it follows the same gate as the intrusive runs: production is refused, a target that is not on this machine needs your `APITEST_TARGET_ENV` (non-production) and `APITEST_AUTHORIZED=yes`, and `APITEST_READ_ONLY=yes` turns it off. An id you already set (`APITEST_PATH_<NAME>` or an entry in the file) is kept and no record is made for it. Each create also sends the header and query parameters the spec marks required, with a fresh `Idempotency-Key` per create; headers the spec marks optional are not sent, even when `APITEST_SEND_OPTIONAL_HEADERS` is set for the suite. Credentials come from the usual environment variables; output lists operation ids, status codes and ids, never bodies or tokens. It makes one record per resource (at most 25); nested resources are created when their parents' ids are known. What it created is listed under `created` in the ids file, and `--cleanup` deletes it newest first (a record with no DELETE endpoint is listed for you to remove by hand). It does not set the `victim` ids, which need a second user's data. Exit codes: 0 done, 1 a record could not be created or removed, 2 refused or bad input.

### File GitHub issues for the failures (optional)

`file_issues.py` turns the findings in `reports/report.json` into GitHub issues. It is opt-in and previews by default (no network, no token):

```
python $SK/file_issues.py reports/report.json --repo OWNER/NAME            # preview
export APITEST_GITHUB_TOKEN=...                                            # a token that may create issues on the repo
python $SK/file_issues.py reports/report.json --repo OWNER/NAME --create   # preview, then asks you to type yes
```

Failures on the same endpoint with the same kind of failure become one issue that lists every occurrence. Each issue carries a hidden fingerprint, so a rerun skips what is already filed (open, or closed unless `--refile-closed`). Samples are scrubbed again before they leave your machine, and `--no-samples` leaves them out (use it for a public repository). `--min-severity` (default MEDIUM) and `--max-issues` (default 20) bound a run. Details: `skills/api-testing/references/issues.md`. It exits 0 on success or preview, 2 on bad input, 3 when not confirmed or no token, 4 when some issues could not be created.

Exit codes, for scripts and CI: pytest exits 0 when every test passed, 1 when any failed, and 4 when the safety gate stops a remote target that has no
`APITEST_TARGET_ENV`. `run_k6.py` exits 3 when the gate refuses the target, 4 when k6 is not installed, 5 when k6 is installed but cannot start, otherwise with k6's own code, except that a crossed threshold gives 91 (error rate or checks, also when latency crossed too), 92 (latency only) or 99 (kind unknown), and the last line names the kinds. `run_k6.py --check` prints whether k6 can start (its version) before you generate anything. Where the binary cannot be installed or started (some sandboxed agents block it), `--docker` runs the `grafana/k6` image: `python $SK/run_k6.py k6_tests/load.js --docker` (add `--docker-image grafana/k6:<tag>` to pin it). Variables are passed to the container by name only, never by value, and host networking is used, which works on Linux; with Docker Desktop point `--base-url` at `host.docker.internal`. `--k6 "some command"` also accepts a command with arguments.
`chaos_run.py` exits 0 (all passed or skipped), 1 (an experiment found a problem), 2 (bad input), 3 (gate refused) or 4 (cut short by a signal; partial results are written).
`summarize_results.py` exits 0 whenever it wrote the report, **even when the verdict is FAIL**; fail your pipeline on the test runs' exit codes, not on the report script.

Notes on running generated suites:
- Tests that delete or overwrite data are marked `destructive`, need `APITEST_ALLOW_DESTRUCTIVE=yes`, and run last. That includes the fuzzed DELETE in an `all,fuzz` suite, which goes after every other test because it uses the same id.
  With `pytest-xdist` each worker orders its own share, so use `-p no:xdist` (or `-n 0`) when that order matters to you.
- Be polite to shared targets: `APITEST_DELAY_MS=300` (or `--apitest-delay-ms 300`) pauses between requests, `APITEST_RPS=5`
  (or `--apitest-rps 5`) caps the rate; with both, the slower limit applies. A failed connect is retried (GET/HEAD/OPTIONS only,
  `APITEST_RETRIES`, default 2) and then skipped as "target unreachable" (set `APITEST_UNREACHABLE=fail` to fail instead; smoke tests always
  fail). A server that drops or hangs after connecting is a failure, never a skip. Rate-limit probes skip while a throttle is set.
- The safety gate's skips are listed once (`-rs`) with a one-line summary instead of once per test.
- Every report states "Ran X of Y tests" under the verdict, so skipped tests are never hidden in a total. If more than half of the tests skip (missing credentials, ids or gate variables), pytest prints a warning and the report adds
  one, because a green result then covers only the tests that ran. Use `-rs` to see the reasons.

## Example (real output)

The skill scripts below ran from the shipped zip unpacked in a clean directory with a fresh virtual environment (the demo API itself
needs this repository checkout, because it reuses `tests/mock_rest.py`). It is the demo API in
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
Generated 134 test functions in 12 files under api_tests
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
9 failed, 240 passed, 20 skipped in 0.88s

$ python api-testing/scripts/summarize_results.py --junit results/pytest.xml --intake intake.json \
    --base-url http://127.0.0.1:8080 -o reports/report.html
Wrote reports/report.json
Wrote reports/report.html (9 findings, 269 tests, 0 k6 summaries)
```

The demo API is **deliberately flawed** (that is what `--bugs bola,leak` switches on), so this report is supposed to fail. It opens with
`FAIL: 9 finding(s): HIGH 7, MEDIUM 2.` (rerun on commit 4c8b12f with the commands above: 240 passed, 9 failed, 20 skipped). Six of the nine are the seeded flaws:
five BOLA findings (HIGH, two of them from the tenant isolation matrix) and one data exposure (HIGH). The other three are all `GET /admin/stats` returning 403 (one HIGH smoke test, two MEDIUM),
because the run uses a non-admin token on an admin endpoint; the mock server is behaving correctly there, so they are not seeded flaws.

The safety stop, on a remote target without `APITEST_TARGET_ENV`:
```text
$ APITEST_BASE_URL=https://api.example.invalid python -m pytest api_tests -q
ERROR: Target api.example.invalid is not local, and APITEST_TARGET_ENV is not set. Export APITEST_TARGET_ENV to the
environment you are testing (dev, staging, ...; 'production' limits the run to read-only calls) before running. ...
$ echo $?
4
```

## What has and has not been validated

- Unit, generator, hostile-spec, safety-gate and reporting tests: `python -m pytest tests` (811 pass and 36 skip on 2026-11-26, main at a2d6dee plus the file-by-file review changes, with `requirements-dev.txt` and `evals/requirements.txt` installed and without k6 and strawberry-graphql; with only `requirements-dev.txt` the FastAPI eval test also skips, giving 810 and 37; about seven
  and a half minutes on 4 cores; with k6 on the path the generated k6 scripts are executed too and it takes several minutes longer).
  Run on Python 3.11, 3.12 and 3.13 (the CI workflow runs the same three).
- Detection of seeded flaws: 27 of 28 found; the gap (VAmPI mass assignment) is documented. Targets that could not be reached
  from the build sandbox ran as labelled mocks; see [docs/evals.md](docs/evals.md).
- Real runs: one local Spring Boot service (all test types, k6 and chaos) and four public sandbox APIs read-only, as described under Results. No real-API detection rates are claimed, and nothing here is "production-tested".
- **Not yet done:** runs against the real Petstore, VAmPI and DVGA, which you can do with [evals/README.md](evals/README.md).
- **Not yet done, and not claimed:**
  - gRPC load tests through `ghz` are not generated or run; gRPC unary tests are covered, against a local `grpcio` server and real grpcurl 1.9.1.
  - The API gateway and GraphQL example apps in `examples/aws-local/` are local stand-ins written for the tests. The AWS emulator has not been run against them.
  - VAmPI mass assignment is a known miss in the eval (see [docs/evals.md](docs/evals.md)), and the 27 of 28 figure is for seeded flaws in mocks, not real APIs.

## Privacy and support

- **Privacy.** The skill has no telemetry, no account and no server of its own. Its scripts send HTTP requests only to the target API
  you name (and, if you ask for it, to GitHub when `file_issues.py` files an issue with a token you supply). Specs, generated tests,
  results and reports stay on your machine. Credentials are read from environment variables and redacted from reports, which is
  pattern-based, so read a report before you share it. Your agent (for example Claude) processes whatever you paste or let it read under
  that product's own privacy terms, not this project's.
- **Support.** Open an issue at <https://github.com/pankajkumar-codes/api-test-pilot/issues>. This is a single-maintainer preview with
  no service level; expect answers when the maintainer has time. Report a security problem privately, see [SECURITY.md](SECURITY.md).
- **Warranty.** MIT licence, provided as is. Only test APIs you own or are authorized to test; see the safety gate above and [SECURITY.md](SECURITY.md).

## Develop

```bash
pip install -r requirements-dev.txt -r evals/requirements.txt   # evals/requirements.txt (fastapi, uvicorn) is only for the seeded eval target
python -m pytest tests                     # add k6 (K6_BIN or PATH) to also execute the generated k6 scripts
python tools/validate_skill.py             # SKILL.md frontmatter, folder layout, plugin files, version consistency
python evals/run_evals.py --target all --markdown
```
Real-target evals (`--real --base-url`, on containers you start yourself) print the pass/fail/skip counts, the verdict line and the path of
the report they write; see [evals/README.md](evals/README.md).
Contributing: [CONTRIBUTING.md](CONTRIBUTING.md). Changes: [CHANGELOG.md](CHANGELOG.md). Design decisions: [DECISIONS.md](DECISIONS.md).
Build plan and status: [PLAN.md](PLAN.md).

`intake.json` (parser output) describes your API; it is git-ignored, do not commit it.

## Repository layout
```
skills/api-testing/     the skill: SKILL.md (router), references/ (loaded on demand), scripts/, templates/
.claude-plugin/         plugin.json and marketplace.json (Claude Code plugin packaging)
tools/                  validate_skill.py, build_skill_zip.py, make_icon.py (repository tooling, not part of the skill)
examples/               demo API and sample spec, sample reports, the Spring Boot and public sandbox runs, local gateway and GraphQL apps
tests/  evals/  docs/   tests, eval driver and targets, validation record and release docs
```

## Security and responsible use
See [SECURITY.md](SECURITY.md): how to report a vulnerability privately, and the rules for testing only systems you own or may test.

## License
[MIT](LICENSE). Contributors follow the [code of conduct](CODE_OF_CONDUCT.md).

## Third-party software and trademarks
Nothing from another project is bundled into the skill. What it relies on, or ships next to it:

| Component | License | How it is used |
|---|---|---|
| [k6](https://github.com/grafana/k6) | AGPL-3.0 | A separate program you install yourself. The skill writes k6 scripts and starts the `k6` command; it does not include, link or modify k6, so k6's license does not apply to this repository's code. |
| [pytest](https://github.com/pytest-dev/pytest), [requests](https://github.com/psf/requests), [PyYAML](https://github.com/yaml/pyyaml) | MIT, Apache-2.0, MIT | Installed by you with pip; not copied into the repository. |
| [FastAPI](https://github.com/fastapi/fastapi), [Uvicorn](https://github.com/encode/uvicorn) | MIT, BSD-3-Clause | Only for the local eval target (`evals/requirements.txt`). |
| `evals/specs/petstore-openapi.yaml` | Apache-2.0 ([swagger-api/swagger-petstore](https://github.com/swagger-api/swagger-petstore)) | Test fixture, unchanged, with its license field kept. Not part of the skill. |
| `evals/specs/vampi-openapi.yaml` | Upstream license ([erev0s/VAmPI](https://github.com/erev0s/VAmPI)) | Test fixture, unchanged. Not part of the skill. |
| [OWASP API Security Top 10 (2023)](https://owasp.org/API-Security/) | CC BY-SA 4.0 | Category ids and names are used to label findings; OWASP's text is not copied. The Top 10 is a project of the OWASP Foundation. |

OpenAPI is a trademark of The Linux Foundation, GraphQL is a trademark of The GraphQL Foundation, OWASP is a trademark of the OWASP Foundation,
k6 and Grafana are trademarks of Grafana Labs, Swagger is a trademark of SmartBear Software, and Claude and Anthropic are trademarks of Anthropic.
They are named only to say what this project works with. This project is independent and is not affiliated with, sponsored or endorsed by any of them.
