# Soak testing (k6)

**Safety gate first**: soak runs keep a service under load for a long time. Only against a non-production target the user owns or is authorized
to test; production is never a target. Ask the user to confirm and to export `APITEST_TARGET_ENV` and `APITEST_AUTHORIZED=yes`
(never set them yourself). Setup, caps and the launcher are in `load.md`. Long runs also need someone watching the target's own dashboards.

## Goal
Find problems that only appear over time: memory or connection leaks, growing latency, queue buildup, log or disk exhaustion, expiring tokens or caches.

## Generate and run
```
python scripts/gen_k6.py intake.json -o k6_tests --profiles soak --rate 2 --duration 30m
python scripts/run_k6.py k6_tests/soak.js --base-url https://staging.example.com
```
Default: 2 req/s for 30 minutes (a deliberately modest rate: duration, not intensity, is the variable). `--duration` is capped, with ramps, at 4 hours in total.
Do a `--quick` run first. Tell the user how long the real run takes before starting it.

## Pass/fail and reading the result
The hold is split into 10-minute windows (`stage:w01`, `w02`, ...). Each window must keep p95 < `--p95-ms` and the global error rate < `--error-rate`.
**Degradation shows as later windows getting slower or failing while early ones pass**: compare `w01` with the last window. Flat is good; a steady upward
trend is a leak even if every window is under the threshold, so report the trend, not only pass/fail. k6 sees only the client side: ask for the server's memory,
connection and disk graphs for the same period.

## Report
p95 and error rate per window (a small table), the trend in one sentence, what the server-side graphs showed (or that they were not checked), and the rerun command.
