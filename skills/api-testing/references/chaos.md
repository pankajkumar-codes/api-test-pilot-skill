# Chaos testing: plans and runnable experiments

**Safety gate first.** Chaos breaks things on purpose. It belongs in a non-production environment that the user owns or is authorized to test.
Ask the user to confirm both; never set `APITEST_TARGET_ENV` or `APITEST_AUTHORIZED` yourself, never tick the plan's checklist for them.
`chaos_run.py` enforces the same gate as load and fuzz (`safety_gate.py`): production is refused (exit 3), a non-loopback target needs
`APITEST_TARGET_ENV` (non-production) and `APITEST_AUTHORIZED=yes`.

## 1. Plan (nothing is sent)
```
python scripts/gen_chaos_plan.py intake.json -o chaos-plan.md --env staging --dependency "orders db"
```
Nine experiments (C1 latency ... C9 partition) with hypothesis, blast radius, abort conditions and rollback. Spec free text is never copied in.

## 2. Run experiments
```
APITEST_TARGET_ENV=staging APITEST_AUTHORIZED=yes python scripts/chaos_run.py intake.json --base-url URL \
    --experiments proxy-latency,proxy-5xx,proxy-drop,payload --out results/chaos      # --dry-run first
```
Each experiment runs a steady GET load (default 5 req/s, `--probe /path` to choose, else parameterless GETs from the intake) while one fault is
active, removes the fault, and measures **recovery time** (first 5 consecutive healthy answers). Output: `results/chaos/chaos.xml` (JUnit) and `chaos.json`
(per-experiment numbers). Report with `summarize_results.py --junit results/chaos/chaos.xml --intake intake.json`.

| Experiment | What is injected | Needs |
|---|---|---|
| `proxy-latency`, `proxy-5xx`, `proxy-drop`, `proxy-truncate`, `proxy-blackhole` | faults on the network path in front of the API (local proxy on 127.0.0.1) | nothing |
| `payload` | oversized, malformed, deeply nested, wrong-type and truncated bodies in bursts (first 3 write endpoints; 4xx expected, 5xx or a hang is a finding) | a POST/PUT/PATCH with a JSON body |
| `dep-outage`, `dep-slow` | start/stop (or slow on/off) commands **you wrote** for a dependency | `--dependency-config`, `--allow-hooks`, loopback target |
| `dep-latency`, `dep-blackhole`, `dep-reset` | TCP faults between the API and a dependency | `--dep-proxy 15432=127.0.0.1:5432`: point the API's dependency address at `127.0.0.1:15432`, start the API, then run with `--wait-for-target-s 90` |

Proxy faults sit between the client and the API, so they show what clients and gateways see and whether the API recovers; they do not prove the API is
resilient to its own dependencies. Use the `dep-*` experiments for that. `dep-*` experiments skip with a reason when no traffic went through the proxy.

Dependency commands live in a JSON file, as argv lists (no shell), and every fault has an undo:
```
{"dependencies": [{"name": "postgres", "stop": ["docker", "stop", "pg"], "start": ["docker", "start", "pg"],
                   "slow_on": ["..."], "slow_off": ["..."]}]}
```
Commands are never guessed or inferred from the spec. They run only with `--allow-hooks`, only against loopback targets, are printed (secret-looking
arguments and `user:password@` in URLs masked; do not put secrets in commands anyway) before anything runs. The undo runs on error, Ctrl-C, SIGTERM and SIGHUP; signals are held while it runs, an undo stays pending until it has run, and the commands run in their own session (`MANUAL ACTION REQUIRED` is printed if even that fails).

`APITEST_READ_ONLY=yes`: `payload`, `dep-outage` and `dep-slow` are skipped with a reason (they send writes or change a dependency); the proxy experiments send GETs only. The printed rerun line, `chaos.json` and the report never contain the target's or a probe's query string, and the report rebuilds its command from the experiment names (it never copies the result file's line, so add `--dependency-config`/`--allow-hooks` yourself).

## Findings the runner reports
No recovery within `--max-recovery-s` (HIGH); API stopped accepting connections during a dependency fault (HIGH); requests hang instead of failing fast
(`--fail-fast-ms`, MEDIUM); the API answered 5xx on bad input or on a path-only fault (MEDIUM); unhealthy right after a payload burst (HIGH).
A skipped experiment says why. If the baseline is unhealthy nothing is injected.

## Bounds (hard ceilings in code)
Probe rate 50/s; probe timeout and settle wait 60 s; fault 120 s; dependency outage 60 s; whole run 30 min (checked between steps); payload 1 MiB; burst 100; latency 10 s; proxies on 127.0.0.1 only, upstream
passes the gate, at most 64 connections. Output holds paths, statuses and timings only: no headers, bodies or credentials (credentials come from the
`APITEST_*` environment variables the intake lists).

## After the run
Report which experiments **ran**, which were skipped and why, and the rerun command the runner prints. Turn findings into fixes or accepted risks.
