# Chaos testing: fault-injection plans (v1: plans only)

**Safety gate first.** Chaos experiments break things on purpose. They belong in a non-production environment that the user owns or is authorized to
test, run by the people who operate it. Production is out of scope. Ask the user to confirm the environment and the authorization; never tick the
plan's checklist or set the gate variables yourself.

## Scope in v1 (DECISIONS.md)
api-test-pilot **writes the plan**. It does not inject faults, does not contact the target, and the plan contains no executable injection
commands. The platform team runs the experiments with their own tooling and change process. Executable injection is a v2 question.

## Generate
```
python scripts/gen_chaos_plan.py intake.json -o chaos-plan.md --env staging \
    --dependency "orders db" --dependency "payments api"
```
- `--env` must be a non-production name (production is refused). `--dependency` is repeatable (default: datastore, cache, downstream HTTP service,
  broker, identity provider; at most 8). `--endpoints` picks the endpoints used to observe user impact (default: writes and authenticated ones first, up to 8).
- Only methods, paths, ids and documented status codes come from the spec. Titles, summaries and descriptions are **not** copied (untrusted text).
- Ask the user for the real dependencies; the defaults are a guess. Where the spec documents no 5xx or 429, the plan flags it: agree the intended contract first.

## What the plan contains
Safety gate and unchecked pre-flight checklist; scope; steady state (smoke tests and a low-rate k6 probe through `run_k6.py`, so the same gate applies);
then nine experiments, each with hypothesis, injection (what, not how), blast radius, expected behaviour, what to observe, abort conditions, rollback and a result line per dependency:
C1 added latency, C2 upstream 5xx, C3 timeouts and hung connections, C4 dependency down, C5 partial failure, C6 upstream rate limiting,
C7 malformed responses, C8 resource exhaustion, C9 network partition.

## After the experiments
Record results in the plan, turn findings into fixes or accepted risks, rerun failed scenarios. Report which experiments were **planned** and say
plainly that none were run by this tool.
