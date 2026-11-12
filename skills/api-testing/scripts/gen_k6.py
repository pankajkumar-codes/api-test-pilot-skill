#!/usr/bin/env python3
"""Generate k6 load / stress / spike / soak scripts from normalized intake JSON (REST or GraphQL).

Usage:
  python gen_k6.py intake.json -o k6_tests [--profiles all|load,stress,spike,soak] [--base-url URL]
                   [--rate N] [--max-vus N] [--duration 5m] [--p95-ms 500] [--error-rate 0.01]
                   [--spike-multiplier 5] [--steps 4] [--endpoints id1,id2] [--include-writes] [--quick]

Output directory: <profile>.js per profile and manifest.json. Run them with scripts/run_k6.py (it enforces the
safety gate before k6 starts; the scripts enforce it again themselves).

Design rules (CLAUDE.md, references/load.md):
  * Safe by default: GET/HEAD only; POST/PUT/PATCH need --include-writes plus APITEST_ALLOW_WRITES=yes at run time;
    DELETE and GraphQL mutations are never generated.
  * Hard caps, enforced here and again inside each script: {HARD_MAX_RPS} req/s, {HARD_MAX_VUS} VUs, 4 h total,
    steady ramps at most {MAX_RAMP_RPS_PER_S} req/s per second. Defaults are far lower (see PROFILE_DEFAULTS).
  * An error-rate circuit breaker (50 % failures) aborts every run.
  * Spec strings never become code: every spec-derived value is placed in one JSON document that the script parses
    with JSON.parse; comments and file names use sanitized text only.
  * No credentials in output: tokens are read from environment variables at run time.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402
import gen_common  # noqa: E402
import safety_gate  # noqa: E402
from safety_gate import HARD_MAX_DURATION_S, HARD_MAX_RPS, HARD_MAX_VUS, MAX_RAMP_RPS_PER_S  # noqa: E402

__doc__ = (__doc__ or "").format(HARD_MAX_RPS=HARD_MAX_RPS, HARD_MAX_VUS=HARD_MAX_VUS, MAX_RAMP_RPS_PER_S=MAX_RAMP_RPS_PER_S)

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE = SKILL_DIR / "templates" / "k6" / "k6_script.js"

PROFILES = ("load", "stress", "spike", "soak")
# rate = req/s at "expected" traffic; hold/duration in seconds; max_vus caps concurrency.
PROFILE_DEFAULTS = {
    "load": {"rate": 5, "hold": 180, "max_vus": 20},
    "stress": {"rate": 5, "hold": 60, "steps": 4, "max_vus": 50},
    "spike": {"rate": 5, "multiplier": 5, "max_vus": 50},
    "soak": {"rate": 2, "hold": 1800, "max_vus": 10},
}
BREAKER_RATE = 0.5           # abort the run when more than half of the requests fail
SPIKE_MIN_RAMP_S = 5
SPIKE_MAX_MULTIPLIER = 10
SOAK_WINDOW_S = 600
MAX_LABELS = 40
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-\[\]]{1,64}$")
GQL_NAME_RE = re.compile(r"^[_A-Za-z][_0-9A-Za-z]{0,63}$")
GQL_TYPE_RE = re.compile(r"^[_A-Za-z0-9\[\]!]{1,80}$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/{}\-]*$")
DURATION_RE = re.compile(r"^(\d{1,5})([smh])$")


class PlanError(_common.IntakeError):
    """The requested load shape breaks a hard cap or ramp limit."""


# --------------------------------------------------------------------------- the load shape
def parse_duration(text: str) -> int:
    m = DURATION_RE.match(text.strip().lower())
    if not m:
        raise PlanError(f"duration must look like 90s, 10m or 2h, not {text!r}")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)]


def _ramp_s(frm: int, to: int) -> int:
    """Seconds for a steady ramp that respects MAX_RAMP_RPS_PER_S (at least 30)."""
    return max(30, math.ceil(abs(to - frm) / MAX_RAMP_RPS_PER_S))


def build_stages(profile: str, rate: int, *, hold: int | None = None, steps: int = 4, multiplier: int = 5,
                 scale: float = 1.0) -> list[dict]:
    """Stages as {duration, target, label} (seconds, req/s). Labels become k6 `stage` tags (one per stage).

    `scale` < 1 shortens every stage (--quick: a plumbing check, not a capacity measurement); never below 3 s,
    and spike ramps never below SPIKE_MIN_RAMP_S."""
    d = PROFILE_DEFAULTS[profile]
    hold = d.get("hold") if hold is None else hold
    st: list[dict] = []
    floor = 3 if scale < 1 else 1

    def add(dur: float, target: int, label: str) -> None:
        dur = max(floor, round(dur * scale))
        if label in ("spike_up", "spike_down"):
            dur = max(dur, SPIKE_MIN_RAMP_S)
        st.append({"duration": int(dur), "target": int(target), "label": label})

    if profile == "load":
        add(_ramp_s(0, rate), rate, "ramp_up")
        add(hold, rate, "steady")
        add(30, 0, "ramp_down")
    elif profile == "stress":
        prev = 0
        for i in range(1, steps + 1):
            tgt = rate * i
            add(_ramp_s(prev, tgt), tgt, f"step{i}_ramp")
            add(hold, tgt, f"step{i}_hold")
            prev = tgt
        add(_ramp_s(prev, rate), rate, "recovery_ramp")
        add(max(hold, 90), rate, "recovery")
        add(30, 0, "ramp_down")
    elif profile == "spike":
        peak = rate * multiplier
        add(_ramp_s(0, rate), rate, "baseline_ramp")
        add(60, rate, "baseline")
        add(max(SPIKE_MIN_RAMP_S, 10), peak, "spike_up")
        add(60, peak, "spike")
        add(max(SPIKE_MIN_RAMP_S, 10), rate, "spike_down")
        add(120, rate, "recovery")
        add(30, 0, "ramp_down")
    elif profile == "soak":
        add(_ramp_s(0, rate), rate, "ramp_up")
        left, i = hold, 1
        while left > 0:
            chunk = min(SOAK_WINDOW_S, left)  # windows are scaled in add()
            add(chunk, rate, f"w{i:02d}")
            left -= chunk
            i += 1
        add(30, 0, "ramp_down")
    else:  # pragma: no cover - guarded by callers
        raise PlanError(f"unknown profile {profile}")
    return st


def validate_plan(profile: str, stages: list[dict], *, max_vus: int, rate: int, multiplier: int = 1) -> dict:
    """Enforce the hard caps and ramp limits. Raises PlanError; returns summary numbers on success."""
    if not 1 <= rate <= HARD_MAX_RPS:
        raise PlanError(f"--rate must be 1..{HARD_MAX_RPS} req/s (hard cap), got {rate}")
    if not 1 <= max_vus <= HARD_MAX_VUS:
        raise PlanError(f"--max-vus must be 1..{HARD_MAX_VUS} (hard cap), got {max_vus}")
    if profile == "spike" and not 2 <= multiplier <= SPIKE_MAX_MULTIPLIER:
        raise PlanError(f"--spike-multiplier must be 2..{SPIKE_MAX_MULTIPLIER}, got {multiplier}")
    total = sum(s["duration"] for s in stages)
    if total > HARD_MAX_DURATION_S:
        raise PlanError(f"total duration {total}s exceeds the hard cap of {HARD_MAX_DURATION_S}s (4 h)")
    peak = max(s["target"] for s in stages)
    if peak > HARD_MAX_RPS:
        raise PlanError(f"peak {peak} req/s exceeds the hard cap of {HARD_MAX_RPS}; lower --rate"
                        + (f" or --steps/--spike-multiplier" if profile in ("stress", "spike") else ""))
    if len(stages) > MAX_LABELS:
        raise PlanError(f"{len(stages)} stages exceed the limit of {MAX_LABELS}; shorten --duration")
    prev = 0
    for s in stages:
        if s["duration"] < 1:
            raise PlanError("stage durations must be positive")
        slope = abs(s["target"] - prev) / s["duration"]
        if profile == "spike" and s["label"] in ("spike_up", "spike_down"):
            if s["duration"] < SPIKE_MIN_RAMP_S:
                raise PlanError(f"spike ramps must take at least {SPIKE_MIN_RAMP_S}s")
        elif slope > MAX_RAMP_RPS_PER_S + 1e-9:
            raise PlanError(f"stage {s['label']} ramps {slope:.1f} req/s per second; the limit is {MAX_RAMP_RPS_PER_S}")
        prev = s["target"]
    return {"peak_rps": peak, "total_s": total, "stages": len(stages)}


# --------------------------------------------------------------------------- thresholds
def thresholds_for(profile: str, stages: list[dict], p95_ms: int, error_rate: float) -> tuple[dict, list]:
    """(global thresholds, per-endpoint thresholds). Stage-tagged submetrics are added so that the summary shows
    the shape of the run (breaking point, spike recovery, soak degradation) instead of one averaged number."""
    breaker = {"threshold": f"rate<{BREAKER_RATE}", "abortOnFail": True, "delayAbortEval": "20s"}
    never_fails = "p(95)<86400000"  # informational: forces the submetric into the summary without asserting anything
    t: dict = {"http_req_failed": [breaker]}
    per_endpoint: list = []
    if profile == "load":
        t["http_req_failed"].insert(0, f"rate<{error_rate}")
        t["http_req_duration"] = [f"p(95)<{p95_ms}", f"p(99)<{p95_ms * 3}"]
        t["checks"] = [f"rate>{1 - error_rate:g}"]
        per_endpoint = [f"p(95)<{p95_ms}"]
    elif profile == "stress":
        # Failing is the point: only the circuit breaker asserts. Per-step numbers show where it degrades.
        t["http_req_duration"] = [never_fails]
        per_endpoint = [never_fails]
        for s in stages:
            if s["label"].endswith("_hold") or s["label"] == "recovery":
                t[f"http_req_duration{{stage:{s['label']}}}"] = [never_fails]
                t[f"http_req_failed{{stage:{s['label']}}}"] = ["rate<=1"]
        t["http_req_failed{stage:recovery}"] = [f"rate<{max(error_rate, 0.05):g}"]
    elif profile == "spike":
        t["http_req_duration"] = [never_fails]
        per_endpoint = [never_fails]
        for label in ("baseline", "spike"):
            t[f"http_req_duration{{stage:{label}}}"] = [never_fails]
            t[f"http_req_failed{{stage:{label}}}"] = ["rate<=1"]
        # The assertion that matters: after the surge the service is back to normal.
        t["http_req_duration{stage:recovery}"] = [f"p(95)<{p95_ms * 2}"]
        t["http_req_failed{stage:recovery}"] = [f"rate<{max(error_rate, 0.05):g}"]
    elif profile == "soak":
        t["http_req_failed"].insert(0, f"rate<{error_rate}")
        t["http_req_duration"] = [f"p(95)<{p95_ms}"]
        per_endpoint = [never_fails]
        for s in stages:
            if re.fullmatch(r"w\d\d", s["label"]):
                t[f"http_req_duration{{stage:{s['label']}}}"] = [f"p(95)<{p95_ms}"]  # degradation shows as a late window failing
    return t, per_endpoint


# --------------------------------------------------------------------------- endpoints -> JSON config
def _env_name(name: str) -> str:
    return "APITEST_PATH_" + re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()


def _tag(raw: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")[:40] or "endpoint"
    tag, n = base, 2
    while tag in used:
        tag, n = f"{base}_{n}", n + 1
    used.add(tag)
    return tag


def _clean_map(src: dict, notes: list[str], what: str) -> dict:
    """Keep only entries with plain names (no prototype keys, no odd characters)."""
    out = {}
    for k, v in src.items():
        if isinstance(k, str) and NAME_RE.match(k) and k not in ("__proto__", "constructor", "prototype"):
            out[k] = v
        else:
            notes.append(f"dropped {what} with an unusable name")
    return out


def rest_endpoints(doc: dict, include_writes: bool, only: list[str] | None, notes: list[str]) -> list[dict]:
    out, used = [], set()
    for ep in doc["endpoints"]:
        if only is not None and ep["id"] not in only:
            continue
        label = f"{ep['method']} {gen_common.doc_safe(ep['path'])[:60]}"
        method = ep["method"].upper()
        if method == "DELETE":
            notes.append(f"{label}: DELETE is never load-tested")
            continue
        write = method in gen_common.BODY_METHODS
        if method not in ("GET", "HEAD") and not write:
            continue
        if write and not include_writes:
            notes.append(f"{label}: skipped (writes need --include-writes)")
            continue
        if only is None and gen_common.is_admin_endpoint(ep):
            notes.append(f"{label}: admin endpoint skipped (name it in --endpoints to include it)")
            continue
        if not gen_common.supported_body(ep):
            notes.append(f"{label}: request body type is not generated (JSON and form only)")
            continue
        if not PATH_RE.match(ep["path"]):
            notes.append(f"{label}: path has unusual characters; skipped")
            continue
        base = gen_common.baseline(ep)
        has_body = write and ep.get("request_body")
        body = gen_common.sample_body(ep) if has_body else None
        ctype = (ep.get("request_body") or {}).get("content_type") if has_body else None
        sec = ep.get("security") or [] if ep.get("auth_required") else []
        out.append({
            "tag": _tag(ep["id"] or f"{method}_{ep['path']}", used),
            "method": method,
            "path": ep["path"],
            "write": write,
            "pathParams": [{"name": n, "env": _env_name(n), "default": (m.get("default") if not _common.is_secret_name(n) else None)}
                           for n, m in base["path_params"].items()],
            "query": _clean_map(base["query"], notes, "query parameter"),
            "headers": _clean_map(base["headers"], notes, "header"),
            "body": body,
            "contentType": ctype,
            "sec": [[s for s in alt if isinstance(s, str)] for alt in sec],
            "ok": gen_common.success_codes(ep),
            "graphql": False,
        })
    return out


def graphql_endpoints(doc: dict, only: list[str] | None, notes: list[str]) -> list[dict]:
    import gen_graphql
    sc = gen_graphql.Schema(doc)
    path = sc.path
    if not PATH_RE.match(path):
        raise PlanError("GraphQL endpoint path has unusual characters")
    ep0 = doc["endpoints"][0]
    sec = ep0.get("security") or [] if ep0.get("auth_required") else []
    out, used = [], set()
    for op in sc.ops:
        name = op.get("name", "")
        if op.get("kind") != "query":
            if op.get("kind") == "mutation":
                notes.append(f"mutation {gen_common.doc_safe(name)[:40]}: mutations are never load-tested")
            continue
        if only is not None and op["id"] not in only and name not in only:
            continue
        if not GQL_NAME_RE.match(name) or any(not GQL_NAME_RE.match(a["name"]) or not GQL_TYPE_RE.match(a["type"]) for a in op["args"]):
            notes.append("query with an unusual name skipped")
            continue
        safe_sel = [s for s in (op.get("selection") or []) if GQL_NAME_RE.match(s) and not _common.is_secret_name(s)]
        sel = " { " + " ".join(safe_sel or ["__typename"]) + " }" if op["return_kind"] in ("OBJECT", "INTERFACE") else (
            " { __typename }" if op["return_kind"] == "UNION" else "")
        variables, skip = {}, False
        for a in op["args"]:
            if not a["required"]:
                continue
            if a["named"] == "ID":
                names = [f"APITEST_GQL_{re.sub(r'[^A-Za-z0-9]+', '_', op['name']).strip('_').upper()}_{re.sub(r'[^A-Za-z0-9]+', '_', a['name']).strip('_').upper()}",
                         f"APITEST_GQL_{re.sub(r'[^A-Za-z0-9]+', '_', a['name']).strip('_').upper()}"]
                variables[a["name"]] = {"$env": names}
                continue
            v = sc.sample(a["named"], a["list"], a["name"])
            if v is None:
                skip = True
                break
            variables[a["name"]] = v
        if skip:
            notes.append(f"query {name}: required argument without a sample value; skipped")
            continue
        decl = "(" + ", ".join(f"${a['name']}: {a['type']}" for a in op["args"]) + ")" if op["args"] else ""
        call = "(" + ", ".join(f"{a['name']}: ${a['name']}" for a in op["args"]) + ")" if op["args"] else ""
        query = f"query ApitestLoad{re.sub(r'[^A-Za-z0-9]', '', name)} {decl} {{ {name}{call}{sel} }}"
        out.append({
            "tag": _tag("gql_" + name, used), "method": "POST", "path": path, "write": False,
            "pathParams": [], "query": {}, "headers": {},
            "body": {"query": query, "variables": variables, "operationName": "ApitestLoad" + re.sub(r"[^A-Za-z0-9]", "", name)},
            "contentType": "application/json", "sec": [[s for s in alt if isinstance(s, str)] for alt in sec],
            "ok": [200], "graphql": True,
            # a GraphQL schema rarely declares per-operation auth: send a token when one is configured, never skip for lack of one
            "softSec": [] if sec else [[n for n in schemes_config(doc)]] if schemes_config(doc) else [],
        })
    return out


def schemes_config(doc: dict) -> dict:
    out = {}
    for name, s in (doc.get("security_schemes") or {}).items():
        if isinstance(name, str) and NAME_RE.match(name) and name != "__proto__":
            out[name] = {k: v for k, v in s.items() if k in ("type", "scheme", "in", "name", "env")}
    return out


# --------------------------------------------------------------------------- rendering
def one_line(text: str) -> str:
    """Text for a `//` comment: printable ASCII on one line (no newline, U+2028/2029 or other line breaks)."""
    return re.sub(r"[^\x20-\x7e]", "_", str(text))[:300]


def render_script(profile: str, config: dict, rerun: str) -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(json.dumps(config, ensure_ascii=True, sort_keys=False), ensure_ascii=True)
    # str.replace of fixed placeholders only; the template contains each exactly once.
    values = {"CONFIG_JSON": payload, "PROFILE": profile, "RERUN": one_line(rerun)}
    return re.sub(r"__(CONFIG_JSON|PROFILE|RERUN)__", lambda m: values[m.group(1)], text)  # single pass: values are never rescanned


def js_syntax_error(text: str) -> str | None:
    """None if `node --check` passes (or node is missing); otherwise the error text (a generator bug)."""
    import shutil
    import subprocess
    import tempfile
    node = shutil.which("node")
    if not node:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        mjs = Path(tmp) / "check.mjs"
        mjs.write_text(text, encoding="utf-8")
        proc = subprocess.run([node, "--check", str(mjs)], capture_output=True, text=True, timeout=30)
    return None if proc.returncode == 0 else proc.stderr.strip()


def default_base_url(doc: dict, override: str | None) -> str:
    url = override or next(iter(doc.get("servers") or []), "")
    url, _ = _common.strip_userinfo(url)
    # strict charset (same as gen_pytest): no quotes, spaces or control characters can enter a generated file
    ok = re.fullmatch(r"https?://[A-Za-z0-9._~:/?#@!$&()*+,;=%\[\]-]+", url) and safety_gate.target_host(url)
    return url if ok else ""


def generate(doc: dict, out: Path, profiles: list[str], *, base_url: str | None = None, rate: int | None = None,
             max_vus: int | None = None, hold: int | None = None, p95_ms: int = 500, error_rate: float = 0.01,
             multiplier: int | None = None, steps: int | None = None, only: list[str] | None = None,
             include_writes: bool = False, quick: bool = False) -> dict:
    chosen = list(PROFILES) if profiles == ["all"] else profiles
    bad = [p for p in chosen if p not in PROFILES]
    if bad:
        raise PlanError(f"Unknown profile(s): {', '.join(bad)}. Choose from: {', '.join(PROFILES)}, all")
    if not 1 <= p95_ms <= 600000:
        raise PlanError("--p95-ms must be 1..600000")
    if not 0 < error_rate < 1:
        raise PlanError("--error-rate must be between 0 and 1 (exclusive)")
    is_gql = (doc.get("source") or {}).get("type") == "graphql"
    notes: list[str] = []
    endpoints = graphql_endpoints(doc, only, notes) if is_gql else rest_endpoints(doc, include_writes, only, notes)
    if only is not None:
        known = {e["id"] for e in doc["endpoints"]} | ({o["id"] for o in doc["graphql"]["operations"]} | {o["name"] for o in doc["graphql"]["operations"]} if is_gql else set())
        unknown = [x for x in only if x not in known]
        if unknown:
            raise PlanError("Unknown endpoint id(s): " + ", ".join(gen_common.doc_safe(x)[:40] for x in unknown))
    if not endpoints:
        raise PlanError("No endpoint is eligible for load testing (GET/HEAD only unless --include-writes). " + "; ".join(notes[:3]))
    base = default_base_url(doc, base_url)
    if base_url and not base:
        raise PlanError("--base-url must be a plain http(s) URL without credentials")
    files: dict[str, str] = {}
    plans: dict[str, dict] = {}
    for profile in chosen:
        d = PROFILE_DEFAULTS[profile]
        r = rate if rate is not None else d["rate"]
        vus = max_vus if max_vus is not None else d["max_vus"]
        mult = multiplier if multiplier is not None else d.get("multiplier", 1)
        stages = build_stages(profile, r, hold=hold, steps=steps if steps is not None else d.get("steps", 4), multiplier=mult,
                              scale=0.1 if quick else 1.0)
        summary = validate_plan(profile, stages, max_vus=vus, rate=r, multiplier=mult)
        glob_t, ep_t = thresholds_for(profile, stages, p95_ms, error_rate)
        rerun = f"APITEST_BASE_URL={shlex.quote(base) if base else '<target-url>'} python scripts/run_k6.py {shlex.quote(str(out / (profile + '.js')))}"
        cfg = {
            "profile": profile, "defaultBaseUrl": base, "timeout": "10s",
            "startRate": 1, "preAllocatedVus": max(1, math.ceil(summary["peak_rps"] / 2)),
            "peakRps": summary["peak_rps"], "caps": {"maxRps": min(HARD_MAX_RPS, summary["peak_rps"]), "maxVus": vus},
            "stages": [{"duration": f"{s['duration']}s", "target": s["target"]} for s in stages],
            "stageBounds": [{"label": s["label"], "end": sum(x["duration"] for x in stages[:i + 1]) * 1000} for i, s in enumerate(stages)],
            "thresholds": glob_t, "endpointThresholds": ep_t,
            "includeWrites": bool(include_writes), "schemes": schemes_config(doc), "endpoints": endpoints,
        }
        text = render_script(profile, cfg, rerun)
        name = f"{profile}.js"
        err = js_syntax_error(text)
        if err:
            raise _common.IntakeError(f"generator produced invalid JavaScript in {name}: {err}")
        files[name] = text
        plans[profile] = {**summary, "rate": r, "max_vus": vus, "rerun": rerun}
    out.mkdir(parents=True, exist_ok=True)  # only after every plan validated: a refused request leaves nothing behind
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    manifest = {"profiles": chosen, "defaultBaseUrl": base, "endpoints": len(endpoints), "plans": plans}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    envs = sorted({n for s in schemes_config(doc).values() for n in s.get("env", [])})
    return {"out": str(out), "files": sorted(files) + ["manifest.json"], "plans": plans, "notes": notes, "env": envs,
            "endpoints": len(endpoints), "base_url": base}


def report(result: dict) -> str:
    rows = [f"Generated {len(result['plans'])} k6 script(s) for {result['endpoints']} endpoint(s) under {result['out']}"]
    for p, s in result["plans"].items():
        rows.append(f"  {p}: peak {s['peak_rps']} req/s, max {s['max_vus']} VUs, {s['total_s']} s, {s['stages']} stages")
    rows += [f"NOTE: {n}" for n in result["notes"]]
    if result["env"]:
        rows.append("Credentials: export " + ", ".join(result["env"]) + " (read from the environment at run time; never written to disk)")
    rows += ["", "SAFETY GATE: these runs generate real traffic. Confirm the target is NOT production and that you own or are authorized",
             "to test it, then export APITEST_TARGET_ENV=<staging|dev|...> and APITEST_AUTHORIZED=yes (not needed for localhost).",
             "Production is refused even if the variables are set.", "", "Rerun with:"]
    rows += [f"  {s['rerun']}" for s in result["plans"].values()]
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gen_k6.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("intake", help="normalized intake JSON (from intake.py)")
    ap.add_argument("-o", "--out", default="k6_tests")
    ap.add_argument("--profiles", default="all", help="comma-separated: load,stress,spike,soak (default: all)")
    ap.add_argument("--base-url", help="default target baked into the scripts (APITEST_BASE_URL overrides at run time)")
    ap.add_argument("--rate", type=int, help=f"expected req/s (default per profile; hard cap {HARD_MAX_RPS})")
    ap.add_argument("--max-vus", type=int, help=f"concurrency cap (default per profile; hard cap {HARD_MAX_VUS})")
    ap.add_argument("--duration", help="steady/hold time, e.g. 5m (load, stress step, soak; soak default 30m; total capped at 4h)")
    ap.add_argument("--p95-ms", type=int, default=500, help="p95 latency threshold (default 500)")
    ap.add_argument("--error-rate", type=float, default=0.01, help="max error rate (default 0.01)")
    ap.add_argument("--spike-multiplier", type=int, help=f"spike height as a multiple of --rate (default 5, max {SPIKE_MAX_MULTIPLIER})")
    ap.add_argument("--steps", type=int, help="stress steps (default 4): rate x1, x2, ... then recovery")
    ap.add_argument("--endpoints", help="comma-separated endpoint ids (or GraphQL query names) to include")
    ap.add_argument("--quick", action="store_true", help="10x shorter stages (min 3 s): checks the plumbing, says nothing about capacity")
    ap.add_argument("--include-writes", action="store_true", help="also generate POST/PUT/PATCH (needs APITEST_ALLOW_WRITES=yes at run time)")
    args = ap.parse_args(argv)
    try:
        doc = json.loads(Path(args.intake).read_text(encoding="utf-8"))
        result = generate(
            doc, Path(args.out), [p.strip() for p in args.profiles.split(",") if p.strip()], base_url=args.base_url,
            rate=args.rate, max_vus=args.max_vus, hold=parse_duration(args.duration) if args.duration else None,
            p95_ms=args.p95_ms, error_rate=args.error_rate, multiplier=args.spike_multiplier, steps=args.steps,
            only=[e.strip() for e in args.endpoints.split(",") if e.strip()] if args.endpoints else None,
            include_writes=args.include_writes, quick=args.quick)
    except (OSError, ValueError, _common.IntakeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
