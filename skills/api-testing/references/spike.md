# Spike testing (k6)

**Safety gate first**: spike runs hit a service with a sudden surge. Only against a non-production target the user owns or is authorized to
test; production is never a target. Ask the user to confirm and to export `APITEST_TARGET_ENV` and `APITEST_AUTHORIZED=yes`
(never set them yourself). Setup, caps and the launcher are in `load.md`.

## Goal
Check what happens when traffic jumps (a campaign, a retry storm, a failover) and, above all, that the service **returns to normal afterwards**.

## Generate and run
```
python scripts/gen_k6.py intake.json -o k6_tests --profiles spike --rate 5 --spike-multiplier 5
python scripts/run_k6.py k6_tests/spike.js --base-url https://staging.example.com
```
Shape: ramp to the baseline, hold 60 s, jump to `rate x multiplier` in 10 s (a spike ramp is never shorter than 5 s), hold 60 s, drop back in 10 s,
then 120 s of **recovery** at the baseline. `--spike-multiplier` is 2 to 10 and the peak may not exceed 100 req/s.

## Pass/fail and reading the result
The assertion is the recovery stage: `http_req_duration{stage:recovery}` p95 < 2x `--p95-ms` and error rate < 5 % (or `--error-rate`, if higher).
`baseline` and `spike` numbers are informational: compare p95/errors in `spike` with `baseline`. Look for: queueing (latency grows but errors stay 0),
load shedding (fast 429/503), autoscaling lag (errors early in the spike, fine later), and a recovery that never completes (stuck queue, exhausted pool).
`dropped_iterations` during the spike means the VU cap was reached before the planned rate (raise `--max-vus` up to 200 if the host can take it).

## Report
Baseline vs spike vs recovery table (p95, error rate), time to recover, and which of the behaviours above was seen.
