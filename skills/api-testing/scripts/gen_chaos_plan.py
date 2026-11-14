#!/usr/bin/env python3
"""Generate a chaos / fault-injection PLAN (Markdown) from normalized intake JSON. Stdlib only, offline.

Usage:
  python gen_chaos_plan.py intake.json -o chaos-plan.md [--env staging] [--dependency "orders db"]...
                           [--endpoints id1,id2] [--max-endpoints 8]

This tool is the PLANNER (executable experiments live in chaos_run.py, DECISIONS.md 2026-11-13). It never injects a fault, never contacts the target, and the plan
contains no executable injection commands (no fenced code blocks): the user's platform team runs the experiments
with their own tooling, under their own change process. Every plan opens with the safety gate and an unchecked
pre-flight checklist; production is refused.

The plan is derived from the spec only for endpoint methods, paths, ids and documented status codes. Free text from the
spec (titles, summaries, descriptions, examples) is never copied into the plan: it is untrusted, and plans are read by people
and by AI assistants.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402
import safety_gate  # noqa: E402

SAFE_CHARS = re.compile(r"[^A-Za-z0-9/_{}.:~-]")
DEP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$")
DEFAULT_DEPENDENCIES = ["primary datastore", "cache", "downstream HTTP service", "message broker or queue", "identity provider"]

# id, title, hypothesis template, injection (what, not how), expected, abort, applies to
SCENARIOS = [
    ("C1", "Added latency", "When {dep} answers much slower than normal, the API still meets its own timeout and error budget "
     "and does not pile up requests.",
     "Delay traffic between the API and {dep} in steps (for example +100 ms, +500 ms, +2 s, +5 s), one step at a time, "
     "using a network-level or proxy-level fault tool your platform team already operates.",
     "p95 grows roughly by the injected delay at most; timeouts and retries stay bounded; no thread/connection pool exhaustion.",
     "p95 above 3x baseline for 2 minutes, or error rate above 5 %."),
    ("C2", "Upstream 5xx", "When {dep} returns server errors for part of the calls, the API degrades predictably: "
     "retries are bounded, errors are mapped to a documented status, and nothing is reported as success.",
     "Make {dep} (or a stand-in for it) answer 500/502/503 for 10 %, then 50 %, then 100 % of calls.",
     "Documented 5xx (or 503/504) from the API, a bounded retry count with backoff and jitter, a circuit breaker opening "
     "and closing, no data written twice.",
     "Error rate above 20 % of API traffic for 2 minutes at the 10 % step, or any data-integrity anomaly."),
    ("C3", "Timeouts and hung connections", "When {dep} accepts connections but never answers, every call ends at the "
     "API's own deadline instead of hanging.",
     "Accept the connection to {dep} and send no response (blackhole/stall) for a bounded period.",
     "A timeout error at the configured deadline (not later), freed resources, health checks reflecting the degraded state.",
     "Request concurrency growing without bound, or memory/threads still climbing after 2 minutes."),
    ("C4", "Dependency down", "When {dep} is unreachable (connection refused or name not resolving), the API fails fast "
     "with a clear error, keeps serving what does not need it, and recovers on its own when {dep} returns.",
     "Stop or isolate {dep} in the test environment (stop the instance, drop its network route, or break its name resolution), "
     "then restore it.",
     "Fast failures (no long hangs), unaffected endpoints keep working, automatic recovery within the agreed time.",
     "Impact outside the experiment scope, or no recovery 5 minutes after {dep} is restored."),
    ("C5", "Partial failure", "When one instance or zone of {dep} (or of the API) fails, the rest absorbs the load "
     "without user-visible errors beyond the agreed budget.",
     "Remove one node or zone of {dep} from rotation, or make a fraction of its calls fail.",
     "Load balancing and retries route around the failure; error rate stays within the budget; no thundering herd on recovery.",
     "Error rate above the budget for 2 minutes."),
    ("C6", "Upstream rate limiting", "When {dep} answers 429 with Retry-After, the API honours the delay, sheds load, "
     "and does not amplify the problem with retries.",
     "Make {dep} answer 429 (with and without Retry-After) for a fixed share of calls.",
     "Backoff that respects Retry-After, a documented 429/503 to API clients, no retry storm.",
     "Retry traffic to {dep} exceeding 2x the normal request rate."),
    ("C7", "Malformed or truncated responses", "When {dep} returns a broken, truncated or unexpected-schema response, the "
     "API treats it as an error and does not leak the raw payload or crash.",
     "Return an empty body, a truncated body, invalid JSON, or a changed field type from {dep} (use a stand-in service).",
     "A controlled error response, no stack trace or upstream payload in the answer, an error log with a correlation id.",
     "Any crash/restart of the API process."),
    ("C8", "Resource exhaustion", "When CPU, memory, disk or connection slots run short on a node, the API sheds load "
     "or fails over instead of corrupting data.",
     "Apply bounded CPU, memory, disk-space or file-descriptor pressure to one node of the API or {dep}.",
     "Health checks fail the node out of rotation, autoscaling or failover reacts, no partial writes.",
     "Pressure affecting nodes outside the experiment, or the node not recovering after the pressure is removed."),
    ("C9", "Network partition", "When the API cannot reach {dep} but clients can reach the API, behaviour is the same "
     "as in C4 and reconciles cleanly after the partition heals.",
     "Block traffic between the API and {dep} in one direction, then both, for a bounded time.",
     "Fast failure, no split-brain writes, consistent state after healing.",
     "Data divergence detected, or recovery taking longer than the agreed time."),
]

PREFLIGHT = [
    "The target environment is NOT production, and the name of the environment is written at the top of this plan.",
    "You own the target or have written authorization to run fault-injection experiments against it.",
    "A named owner approves each experiment and can stop it at any time (the abort conditions below are agreed).",
    "Monitoring and alerting for the affected services are working, and someone is watching during the run.",
    "A rollback is prepared and was rehearsed: how the fault is removed, and how the service is restored.",
    "Dependent teams and on-call people were told about the time window.",
    "A steady-state baseline was recorded first (see 'Steady state' below).",
    "Backups or reset procedures exist for the data the experiment can touch.",
]


def safe(text: str, limit: int = 80) -> str:
    """Spec-derived token for the plan: only characters that cannot form Markdown, HTML or instructions."""
    return SAFE_CHARS.sub("_", str(text))[:limit]


def documented_codes(ep: dict) -> list[str]:
    return sorted(c for c in ep.get("responses", {}) if str(c).isdigit())


def pick_endpoints(doc: dict, only: list[str] | None, limit: int) -> list[dict]:
    eps = doc["endpoints"]
    if only is not None:
        chosen = [e for e in eps if e["id"] in only]
        missing = [x for x in only if x not in {e["id"] for e in eps}]
        if missing:
            raise _common.IntakeError("Unknown endpoint id(s): " + ", ".join(safe(m, 40) for m in missing))
        return chosen[:limit]

    def score(e: dict) -> tuple:  # writes and authenticated endpoints first, since failures there cost the most
        return (0 if e["method"] in ("POST", "PUT", "PATCH", "DELETE") else 1, 0 if e.get("auth_required") else 1, e["path"])
    return sorted(eps, key=score)[:limit]


def render(doc: dict, env: str | None, deps: list[str], endpoints: list[dict]) -> str:
    env_line = env if env else "<fill in: the non-production environment you will test>"
    lines = [
        "# Chaos plan",
        "",
        "> This is a PLAN. gen_chaos_plan.py does not inject faults and does not contact the target.",
        "> Experiments are run by the people who own the platform, with their own tools and change process;",
        "> some of them can be run with chaos_run.py (see references/chaos.md), after the same safety gate.",
        "",
        "## Safety gate (read first)",
        f"- Target environment: **{env_line}**",
        "- Status: **NOT CONFIRMED**. Do not start any experiment until every pre-flight item is ticked by a person.",
        "- Production is out of scope. Do not run these experiments against a production system, even a quiet one.",
        "- An AI assistant must not tick these boxes or set the environment variables on the user's behalf.",
        "",
        "### Pre-flight checklist",
    ]
    lines += [f"- [ ] {p}" for p in PREFLIGHT]
    lines += ["", "## Scope", "", "Dependencies under test (edit this list to match your architecture): "
              + ", ".join(f"`{d}`" for d in deps) + ".", "", "Endpoints used to observe user impact:"]
    for e in endpoints:
        codes = documented_codes(e)
        has_5xx = any(c.startswith("5") for c in codes)
        has_429 = "429" in codes
        gaps = []
        if not has_5xx:
            gaps.append("no 5xx documented")
        if not has_429:
            gaps.append("no 429 documented")
        lines.append(f"- `{safe(e['method'], 8)} {safe(e['path'])}` (id `{safe(e['id'], 40)}`; documented: "
                     f"{', '.join(codes) or 'none'}{'; gaps: ' + ', '.join(gaps) if gaps else ''})")
    lines += ["", "Where the spec documents no 5xx/429, decide the intended contract (for example 503 with Retry-After) "
              "before the experiment, so the expected behaviour below can be judged.", "",
              "## Steady state (measure first, keep it running during every experiment)",
              "- Functional probe: the generated smoke tests against the endpoints above (read-only calls).",
              "- Traffic probe: a low-rate k6 load script (for example 2 req/s) from `gen_k6.py`, run through `run_k6.py` "
              "so the same safety gate applies.",
              "- Record before the experiment: p50/p95/p99 latency, error rate, saturation (CPU, memory, connections, queue depth).",
              "- Define the steady-state limits you will compare against (the abort conditions below use them).", ""]
    lines += ["## Experiments", "",
              "Run each experiment once per dependency listed in Scope, one at a time, smallest step first. "
              "`the dependency` below means the one under test in that run.", ""]
    for sid, name, hyp, inj, exp, abort in SCENARIOS:
        dep = "the dependency"
        lines += [
            f"### {sid}: {name}",
            f"- **Hypothesis:** {hyp.format(dep=dep)}",
            f"- **Injection (what to do, not how):** {inj.format(dep=dep)}",
            "- **Blast radius:** one environment, one dependency, one fault at a time; start with the smallest step.",
            f"- **Expected behaviour:** {exp}",
            "- **Observe:** latency percentiles and error rate per endpoint above, retries and circuit-breaker state, "
            "queue depth, saturation, logs with correlation ids, alerts that fired (and whether they should have).",
            f"- **Abort when:** {abort.format(dep=dep)} Or when the owner says stop.",
            "- **Rollback:** remove the fault, confirm steady state returns, record the time to recover.",
            "- **Result per dependency:** " + ", ".join(f"`{d}`: ______" for d in deps),
            "",
        ]
    lines += ["## After the experiments",
              "- Record what was found, what surprised you, and which alerts or runbooks were missing.",
              "- Turn each finding into a fix or an accepted risk with an owner.",
              "- Rerun the failed scenarios after the fix; keep this plan under version control.",
              "- Executable fault injection is deferred to v2 (DECISIONS.md): use your platform's chaos tooling for now.", ""]
    return "\n".join(lines)


def generate(doc: dict, *, env: str | None = None, dependencies: list[str] | None = None, only: list[str] | None = None,
             max_endpoints: int = 8) -> str:
    if env is not None:
        e = env.strip().lower()
        if e in safety_gate.PROD_ENVS:
            raise _common.IntakeError("chaos plans are for non-production environments only; production is refused")
        if e not in safety_gate.NON_PROD_ENVS:
            raise _common.IntakeError(f"--env must be one of: {', '.join(sorted(safety_gate.NON_PROD_ENVS))}")
        env = e
    deps = dependencies or DEFAULT_DEPENDENCIES
    bad = [d for d in deps if not DEP_RE.match(d)]
    if bad:
        raise _common.IntakeError("--dependency names may contain letters, digits, spaces, '.', '_' and '-' (max 40 characters)")
    if not 1 <= max_endpoints <= 20:
        raise _common.IntakeError("--max-endpoints must be 1..20")
    if len(deps) > 8:
        raise _common.IntakeError("at most 8 dependencies per plan; split the plan")
    endpoints = pick_endpoints(doc, only, max_endpoints)
    if not endpoints:
        raise _common.IntakeError("no endpoints in the intake file")
    return render(doc, env, deps, endpoints)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gen_chaos_plan.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("intake")
    ap.add_argument("-o", "--out", default="chaos-plan.md")
    ap.add_argument("--env", help="the non-production environment the plan is for (local/dev/staging/qa/...)")
    ap.add_argument("--dependency", action="append", help="a dependency to inject faults into (repeatable); defaults to a generic set")
    ap.add_argument("--endpoints", help="comma-separated endpoint ids to observe (default: the most critical, up to --max-endpoints)")
    ap.add_argument("--max-endpoints", type=int, default=8)
    args = ap.parse_args(argv)
    try:
        doc = _common.load_intake_json(args.intake)
        text = generate(doc, env=args.env, dependencies=args.dependency,
                        only=[e.strip() for e in args.endpoints.split(",") if e.strip()] if args.endpoints else None,
                        max_endpoints=args.max_endpoints)
        Path(args.out).write_text(text, encoding="utf-8")
    except (OSError, ValueError, _common.IntakeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {args.out} (a plan only: nothing was injected and the target was not contacted).")
    print("Review it with the platform owner and tick the pre-flight checklist before any experiment.")
    print(f"Regenerate with: python scripts/gen_chaos_plan.py {args.intake} -o {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
