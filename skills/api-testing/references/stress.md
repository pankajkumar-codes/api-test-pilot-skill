# Stress testing (k6)

**Safety gate first**: stress runs push a service past its limits. Only against a non-production target the user owns or is authorized
to test; production is never a target. Ask the user to confirm and to export `APITEST_TARGET_ENV` and `APITEST_AUTHORIZED=yes`
(never set them yourself). Setup, caps and the launcher are in `load.md`.

## Goal
Find where the service starts to degrade (the breaking point) and whether it recovers afterwards.

## Generate and run
```
python scripts/gen_k6.py intake.json -o k6_tests --profiles stress --rate 5 --steps 4 --duration 1m
python scripts/run_k6.py k6_tests/stress.js --base-url https://staging.example.com
```
`--rate` is the **expected** traffic. The script climbs in `--steps` steps (default 4: x1, x2, x3, x4 of the rate; at most 100 req/s at the top),
holds each for `--duration` (default 1 min), then returns to x1 for at least 90 s to watch recovery. Choose `--rate` so that
`rate x steps` is above the capacity you expect but below the hard cap of 100 req/s; if it is not, the generator refuses and says why.

## Reading the result
Failing is the point, so only the circuit breaker (50 % errors, run aborts) and the recovery stage assert. Read the stage metrics in the summary:
`http_req_duration{stage:step2_hold}`, `http_req_failed{stage:step3_hold}`, ... The **breaking point** is the first step where p95 climbs steeply,
errors appear, or `dropped_iterations` shows up. **Recovery** is the `recovery` stage: errors must return to about 0 and latency to the baseline.
A service that stays degraded after the load drops is the most serious finding. If the breaker aborted the run, say at which stage.

## Report
Per step: rate, p95, error rate. State the breaking point as "between x req/s and y req/s", the failure mode (latency growth, 5xx, timeouts, refused
connections) and whether it recovered. Do not call it a capacity number: it is for this environment only.
