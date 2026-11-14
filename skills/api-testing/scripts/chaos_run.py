#!/usr/bin/env python3
"""Run chaos experiments against a non-production API and write JUnit XML + JSON that summarize_results.py understands. Stdlib only.

Usage:
  APITEST_TARGET_ENV=staging APITEST_AUTHORIZED=yes python chaos_run.py intake.json --base-url URL \
      [--experiments proxy-latency,proxy-5xx,proxy-drop,payload] [--probe /path ...] [--out results/chaos]
      [--dependency-config chaos-deps.json --allow-hooks]  [--dep-proxy LISTEN_PORT=HOST:PORT]  [--dry-run]

Every experiment runs a steady, low-rate GET load (the probes) while one fault is active, then removes the fault and measures
how long the API takes to serve normally again (recovery time).
  proxy-latency | proxy-5xx | proxy-drop | proxy-truncate | proxy-blackhole   faults in a local proxy in front of the API
  payload                                                                      oversized, malformed and truncated request bursts
  dep-outage | dep-slow                                                        user-supplied start/stop commands for a dependency
  dep-latency | dep-blackhole | dep-reset                                      a TCP fault proxy between the API and a dependency

Safety (CLAUDE.md, non-negotiable; scripts/safety_gate.py is the single gate):
  * Production is refused. A non-loopback target needs APITEST_TARGET_ENV (non-production) and APITEST_AUTHORIZED=yes.
    Never set them for the user.
  * Fault commands (dependency start/stop) are never guessed: they come from the user's --dependency-config file, run
    without a shell, only with --allow-hooks and only when the target is a loopback address. A stop is always followed by a start,
    also on error, Ctrl-C and SIGTERM.
  * Blast radius is capped in code: request rate, fault length, total run time, payload size and burst size have hard ceilings.
  * Output holds paths, statuses and timings only: no headers, bodies or credentials. Credentials come from the environment.
Exit codes: 0 every experiment passed or was skipped, 1 at least one experiment found a problem, 2 bad input, 3 gate refused,
4 a signal (Ctrl-C, SIGTERM, SIGHUP) cut the run short: faults were undone and results are partial (also when some experiments had failed).
"""
from __future__ import annotations

import argparse
import atexit
import http.client
import json
import os
import re
import shlex
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402
import chaos_proxy  # noqa: E402
import gen_common  # noqa: E402
import safety_gate  # noqa: E402

HARD_MAX_RPS = 50
HARD_MAX_TOTAL_S = 1800
HARD_MAX_FAULT_S = 120
HARD_MAX_PAYLOAD = 1024 * 1024
HARD_MAX_BURST = 100
HARD_MAX_OUTAGE_S = 60
HARD_MAX_RECOVERY_WAIT_S = 120
STREAK = 5            # healthy answers in a row that count as "recovered"
PROXY_EXPERIMENTS = ["proxy-latency", "proxy-5xx", "proxy-drop", "proxy-truncate", "proxy-blackhole"]
DEP_PROXY_EXPERIMENTS = ["dep-latency", "dep-blackhole", "dep-reset"]
HOOK_EXPERIMENTS = ["dep-outage", "dep-slow"]
ALL_EXPERIMENTS = PROXY_EXPERIMENTS + ["payload"] + HOOK_EXPERIMENTS + DEP_PROXY_EXPERIMENTS
DEFAULT_EXPERIMENTS = ["proxy-latency", "proxy-5xx", "proxy-drop", "payload"]
SAFE_RERUN_RE = re.compile(r"^[A-Za-z0-9_./:=,@%+\- ]{1,600}$")
SECRET_ARG_RE = re.compile(r"(?i)((?:pass(?:word)?|secret|token|key|auth)[A-Za-z0-9_]*)([=:])\S+")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,30}$")


class Refused(Exception):
    """A safety rule stopped the run."""


class BadInput(Exception):
    pass


# --------------------------------------------------------------------------- helpers
def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def mask(argv: list[str]) -> str:
    """Printable form of a user command with secret-looking arguments masked."""
    out, hide = [], False
    for a in argv:
        a = re.sub(r"(://[^/@:\s]*:)[^/@\s]+@", r"\1<redacted>@", a)   # user:password@host in a URL argument
        if hide:
            out.append("<redacted>")
            hide = False
        elif re.match(r"(?i)^--?(pass(word)?|secret|token|api-?key|auth\w*)$", a):
            out.append(a)
            hide = True
        else:
            out.append(SECRET_ARG_RE.sub(r"\1\2<redacted>", a))
    return " ".join(shlex.quote(a) for a in out)


def auth_headers(doc: dict) -> dict[str, str]:
    """Credentials for the probes, from the environment variables the intake names. Never printed or written."""
    headers: dict[str, str] = {}
    for s in (doc.get("security_schemes") or {}).values():
        for name in s.get("env") or []:
            val = os.environ.get(name, "")
            if not val or name.endswith("_B"):
                continue
            if s.get("type") == "http" and (s.get("scheme") or "").lower() == "bearer":
                headers.setdefault("Authorization", f"Bearer {val}")
            elif s.get("type") == "apiKey" and s.get("in") == "header" and s.get("name"):
                headers.setdefault(s["name"], val)
    return headers


def safe_target(base: str) -> str:
    """The target as it may be printed or written: no query, fragment or credentials (they can carry secrets)."""
    p = urllib.parse.urlsplit(base)
    host = p.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    return f"{p.scheme}://{host}{':' + str(p.port) if p.port else ''}{p.path}"


def split_base(base: str) -> tuple[str, str, int, str]:
    p = urllib.parse.urlsplit(base)
    return p.scheme, p.hostname or "", p.port or (443 if p.scheme == "https" else 80), p.path.rstrip("/")


# --------------------------------------------------------------------------- steady load
class Load:
    """Paced GET probes in a few threads. Records (start, failed_kind, status, ms). No headers or bodies are kept."""

    def __init__(self, base: str, probes: list[str], headers: dict[str, str], rps: float, timeout: float) -> None:
        self.scheme, self.host, self.port, self.prefix = split_base(base)
        self.probes, self.headers, self.timeout = probes, headers, timeout
        self.workers = max(1, min(4, int(rps)))
        self.gap = self.workers / max(rps, 0.1)
        self.records: list[tuple[float, str | None, int, float]] = []
        self.lock = threading.Lock()
        self.stop_ev = threading.Event()
        self.threads: list[threading.Thread] = []
        self.n = 0

    def one(self) -> tuple[float, str | None, int, float]:
        with self.lock:
            path = self.probes[self.n % len(self.probes)]
            self.n += 1
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        t0 = time.monotonic()
        kind, status = None, 0
        try:
            c = cls(self.host, self.port, timeout=self.timeout)
            c.request("GET", self.prefix + path, headers=self.headers)
            r = c.getresponse()
            r.read()
            status = r.status
            if status >= 500:
                kind = "injected" if r.getheader("X-Chaos-Injected") else "5xx"
            c.close()
        except (socket.timeout, TimeoutError):
            kind = "timeout"
        except (OSError, http.client.HTTPException):
            kind = "conn"
        return t0, kind, status, (time.monotonic() - t0) * 1000

    def _run(self) -> None:
        while not self.stop_ev.is_set():
            rec = self.one()
            with self.lock:
                self.records.append(rec)
            self.stop_ev.wait(self.gap)

    def start(self) -> "Load":
        for _ in range(self.workers):
            t = threading.Thread(target=self._run, daemon=True)
            t.start()
            self.threads.append(t)
        return self

    def stop(self) -> None:
        self.stop_ev.set()
        for t in self.threads:
            t.join(timeout=self.timeout + 1)

    def window(self, a: float, b: float = float("inf")) -> list[tuple[float, str | None, int, float]]:
        with self.lock:
            return sorted(r for r in self.records if a <= r[0] < b)


def stats(rs: list) -> dict:
    ms = [r[3] for r in rs]
    fails = [r for r in rs if r[1]]
    bad_ms = [r[3] for r in fails]
    kinds = {k: sum(1 for r in rs if r[1] == k) for k in ("5xx", "injected", "timeout", "conn")}
    return {"requests": len(rs), "failed": len(fails), "fail_rate": round(len(fails) / len(rs), 3) if rs else 0.0,
            "p50_ms": round(statistics.median(ms), 1) if ms else 0.0, "p95_ms": round(pct(ms, 95), 1),
            "p95_failed_ms": round(pct(bad_ms, 95), 1), **kinds}


def recovery_time(load: Load, t_revert: float, healthy_ms: float, wait_s: float) -> float | None:
    """Seconds from removing the fault until STREAK consecutive requests (started after the removal) are healthy and fast."""
    deadline = time.monotonic() + wait_s
    while True:
        rs = load.window(t_revert)
        run = 0
        for i, r in enumerate(rs):
            run = run + 1 if (r[1] is None and r[3] <= healthy_ms) else 0
            if run >= STREAK:
                return max(0.0, rs[i - STREAK + 1][0] - t_revert)
        if time.monotonic() > deadline:
            return None
        time.sleep(0.25)


# --------------------------------------------------------------------------- results
class Result:
    def __init__(self, name: str) -> None:
        self.name, self.status, self.severity, self.title, self.lines, self.metrics = name, "passed", "", "", [], {}
        self.seconds = 0.0

    def fail(self, severity: str, title: str, *lines: str) -> "Result":
        sev_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        if self.status != "failed" or sev_order.index(severity) < sev_order.index(self.severity):
            self.severity, self.title = severity, title
        else:
            self.lines.append(title)
        self.status = "failed"
        self.lines += list(lines)
        return self

    def skip(self, why: str) -> "Result":
        self.status, self.title = "skipped", why
        return self


def verdict_for_fault(res: Result, load: Load, t_apply: float, t_revert: float, base_stats: dict, args, *, dependency: bool,
                      proxy: bool, rec_s: float | None) -> None:
    during = stats(load.window(t_apply, t_revert))
    res.metrics.update({"baseline": base_stats, "during_fault": during, "recovery_s": None if rec_s is None else round(rec_s, 2)})
    res.lines.append(f"baseline: {base_stats['requests']} requests, p95 {base_stats['p95_ms']} ms, {base_stats['failed']} failed; "
                     f"during fault: {during['requests']} requests, {during['failed']} failed, p95 {during['p95_ms']} ms")
    if rec_s is None:
        res.fail("HIGH", f"did not recover within {args.max_recovery_s:g} s after the fault was removed",
                 f"no {STREAK} consecutive healthy answers in {args.recovery_wait_s:g} s")
    elif rec_s > args.max_recovery_s:
        res.fail("MEDIUM", f"recovery took {rec_s:.1f} s (limit {args.max_recovery_s:g} s)")
    else:
        res.lines.append(f"recovered {rec_s:.1f} s after the fault was removed")
    if dependency:
        if during["conn"]:
            res.fail("HIGH", "the API stopped accepting connections while a dependency was faulted",
                     f"{during['conn']} connection errors during the fault (process crash or restart?)")
        if during["timeout"] or during["p95_failed_ms"] > args.fail_fast_ms:
            res.fail("MEDIUM", "requests hang instead of failing fast while a dependency is faulted",
                     f"{during['timeout']} client timeouts; p95 of failed requests {during['p95_failed_ms']:.0f} ms "
                     f"(limit {args.fail_fast_ms:g} ms). Set connect/read timeouts on the dependency client.")
    if proxy and during["5xx"]:
        res.fail("MEDIUM", "the API itself answered 5xx while only the network path was faulted",
                 f"{during['5xx']} non-injected 5xx responses")


# --------------------------------------------------------------------------- dependency hooks
def load_dependencies(path: str | None) -> dict[str, dict]:
    if not path:
        return {}
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BadInput(f"cannot read --dependency-config {path}: {exc}") from exc
    deps: dict[str, dict] = {}
    for d in (doc.get("dependencies") if isinstance(doc, dict) else None) or []:
        if not isinstance(d, dict) or not NAME_RE.match(str(d.get("name", ""))):
            raise BadInput("each dependency needs a name of letters, digits, _ or - (max 31 characters)")
        entry: dict = {"name": d["name"]}
        for key in ("stop", "start", "slow_on", "slow_off"):
            if key in d:
                cmd = d[key]
                if (not isinstance(cmd, list) or not cmd or len(cmd) > 30 or not all(isinstance(x, str) and x and len(x) < 500 for x in cmd)):
                    raise BadInput(f"dependency {d['name']}: {key} must be a command as a JSON list of strings (no shell), e.g. "
                                   '["docker", "stop", "my-db"]')
                entry[key] = cmd
        if ("stop" in entry) != ("start" in entry) or ("slow_on" in entry) != ("slow_off" in entry):
            raise BadInput(f"dependency {d['name']}: stop needs start, and slow_on needs slow_off, so the fault is always undone")
        deps[d["name"]] = entry
    return deps


EXIT_INTERRUPTED = 4   # 0 ok, 1 failed experiments, 2 bad input, 3 refused by the safety gate, 4 cut short by a signal

STOP_SIGNALS = [getattr(signal, n) for n in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, n)]


class deferred_signals:
    """Hold SIGINT/SIGTERM/SIGHUP while cleanup runs; re-deliver the first one afterwards as KeyboardInterrupt."""

    def __enter__(self):
        self.seen = False
        self.old = {}
        if threading.current_thread() is threading.main_thread():
            for sig in STOP_SIGNALS:
                self.old[sig] = signal.signal(sig, self._note)
        return self

    def _note(self, *_a) -> None:
        self.seen = True

    def __exit__(self, exc_type, *_a):
        for sig, handler in self.old.items():
            signal.signal(sig, handler)
        if self.seen and exc_type is None:
            raise KeyboardInterrupt


def install_stop_handlers() -> None:
    def stop(*_a):
        raise KeyboardInterrupt
    if threading.current_thread() is threading.main_thread():
        for sig in STOP_SIGNALS:
            signal.signal(sig, stop)


class Hooks:
    """Runs user-supplied commands (no shell). Everything started is undone, also on error, Ctrl-C and SIGTERM."""

    def __init__(self) -> None:
        self.pending_undo: list[tuple[str, list[str]]] = []
        self.log: list[str] = []
        atexit.register(self.undo_all)

    def run(self, argv: list[str], what: str) -> bool:
        exe = shutil.which(argv[0])
        if not exe:
            self.log.append(f"{what}: command not found: {argv[0]}")
            return False
        self.log.append(f"{what}: {mask(argv)}")
        try:
            r = subprocess.run([exe, *argv[1:]], shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=90, start_new_session=True)
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            self.log.append(f"{what}: failed ({type(exc).__name__})")
            return False

    def inject(self, name: str, inject_cmd: list[str], undo_cmd: list[str]) -> bool:
        self.pending_undo.append((name, undo_cmd))
        return self.run(inject_cmd, f"inject {name}")

    def undo_all(self) -> None:
        """Run every pending undo. Signals are held until it is done, an entry leaves the list only after its command ran
        (so a retry from `finally` or atexit still finds it), and the commands run in their own session so a Ctrl-C in the
        terminal does not reach them."""
        with deferred_signals():
            while self.pending_undo:
                name, cmd = self.pending_undo[-1]
                ok = self.run(cmd, f"undo {name}") or self.run(cmd, f"undo {name} (retry)")
                if self.pending_undo and self.pending_undo[-1][0] == name:
                    self.pending_undo.pop()
                if not ok:
                    print(f"MANUAL ACTION REQUIRED: could not undo the fault for dependency {name!r}. Run: {mask(cmd)}", file=sys.stderr)


# --------------------------------------------------------------------------- the runner
class Runner:
    def __init__(self, args, doc: dict, base: str, probes: list[str], headers: dict[str, str]) -> None:
        self.args, self.doc, self.base, self.probes, self.headers = args, doc, base, probes, headers
        self.results: list[Result] = []
        self.hooks = Hooks()
        self.t_start = time.monotonic()
        self.dep_proxy: chaos_proxy.TcpFaultProxy | None = None

    def budget_left(self) -> float:
        return self.args.max_total_s - (time.monotonic() - self.t_start)

    def load(self, base: str | None = None) -> Load:
        return Load(base or self.base, self.probes, self.headers, self.args.rps, self.args.timeout_s)

    # one fault window, with steady load before and after
    def fault_window(self, res: Result, *, apply, revert, fault_s: float, base: str | None = None,
                     dependency: bool = False, proxy: bool = False, settle_s: float = 0.0) -> Load | None:
        a = self.args
        fault_s = min(fault_s, a.fault_s)
        need = a.baseline_s + fault_s + a.recovery_wait_s
        if self.budget_left() < min(need, a.baseline_s + fault_s + 5):
            return res.skip(f"not enough of the {a.max_total_s:g} s run budget left") and None
        t0 = time.monotonic()
        load = self.load(base).start()
        t_apply = t_revert = time.monotonic()
        try:
            time.sleep(a.baseline_s)
            base_stats = stats(load.window(0))
            if base_stats["requests"] == 0 or base_stats["fail_rate"] > 0.2:
                res.skip(f"baseline unhealthy before the fault ({base_stats['failed']} of {base_stats['requests']} probes failed): "
                         "fix the target or the --probe paths first")
                return load
            t_apply = time.monotonic()
            try:
                apply()
                time.sleep(fault_s)
            finally:
                revert()
                t_revert = time.monotonic()
            if settle_s:
                time.sleep(settle_s)
            healthy_ms = max(3 * base_stats["p95_ms"], 500.0)
            rec = recovery_time(load, t_revert, healthy_ms, a.recovery_wait_s)
        finally:
            load.stop()
            res.seconds = time.monotonic() - t0
        verdict_for_fault(res, load, t_apply, t_revert, base_stats, a, dependency=dependency, proxy=proxy, rec_s=rec)
        return load

    def add(self, res: Result) -> Result:
        self.results.append(res)
        print(f"  [{res.status.upper():7}] {res.name}" + (f": {res.title}" if res.title else ""), flush=True)
        return res

    # ---- proxy faults
    def proxy_experiment(self, kind: str) -> None:
        a = self.args
        specs = {
            "proxy-latency": [chaos_proxy.Fault("latency", latency_ms=ms) for ms in (250, 1000, 3000)],
            "proxy-5xx": [chaos_proxy.Fault("error", status=503, pct=50)],
            "proxy-drop": [chaos_proxy.Fault("abort", pct=50), chaos_proxy.Fault("reset", pct=50)],
            "proxy-truncate": [chaos_proxy.Fault("truncate", pct=50)],
            "proxy-blackhole": [chaos_proxy.Fault("blackhole", hold_s=min(6.0, a.timeout_s + 1), pct=50)],
        }[kind]
        res = Result("test_chaos_" + kind.replace("-", "_"))
        res.lines.append("fault between the client and the API (local proxy): " + ", ".join(
            f"{f.kind}{'' if f.kind != 'latency' else f' +{f.latency_ms} ms'} on {f.pct} % of requests" for f in specs))
        try:
            with chaos_proxy.HttpFaultProxy(self.base) as px:
                pbase = f"http://127.0.0.1:{px.port}" + split_base(self.base)[3].rstrip("/")
                for f in specs:
                    step = Result(res.name)
                    self.fault_window(step, apply=lambda f=f: px.set_fault(f, a.fault_s), revert=px.clear_fault,
                                      fault_s=a.fault_s, base=pbase, proxy=True)
                    if step.status == "skipped":
                        res.skip(step.title)
                        break
                    res.lines += [f"{f.kind}: {ln}" for ln in step.lines]
                    res.metrics[f.kind + (str(f.latency_ms) if f.latency_ms else "")] = step.metrics
                    res.seconds += step.seconds
                    if step.status == "failed":
                        res.fail(step.severity, f"{f.kind}: {step.title}")
                res.metrics["proxy"] = px.snapshot()
        except PermissionError as exc:
            raise Refused(str(exc)) from exc
        self.add(res)

    # ---- dependency faults through the TCP proxy
    def dep_proxy_experiment(self, kind: str) -> None:
        a = self.args
        res = Result("test_chaos_" + kind.replace("-", "_"))
        px = self.dep_proxy
        if px is None:
            self.add(res.skip("no --dep-proxy given: start the dependency proxy with --dep-proxy LISTEN_PORT=HOST:PORT and point the "
                              "API's connection address for that dependency at 127.0.0.1:LISTEN_PORT"))
            return
        if px.snapshot().get("connections", 0) == 0:
            self.add(res.skip("no traffic has gone through the dependency proxy: the API is not pointed at it, so the fault would test "
                              "nothing. Start the API with the dependency address set to the proxy, then rerun"))
            return
        fault = {"dep-latency": chaos_proxy.Fault("latency", latency_ms=2000), "dep-blackhole": chaos_proxy.Fault("blackhole"),
                 "dep-reset": chaos_proxy.Fault("reset")}[kind]
        res.lines.append(f"fault between the API and its dependency: {fault.kind}" + (f" +{fault.latency_ms} ms" if fault.latency_ms else ""))
        before = px.snapshot()
        self.fault_window(res, apply=lambda: px.set_fault(fault, a.fault_s), revert=px.clear_fault, fault_s=a.fault_s, dependency=True)
        if res.status != "skipped":
            after = px.snapshot()
            res.metrics["dependency_proxy"] = after
            if after.get("fault_" + fault.kind, 0) == 0 and fault.kind != "reset":
                res.lines.append("note: the probes never touched this dependency during the fault (their endpoints may not use it); "
                                 "choose --probe paths that do")
            _ = before
        self.add(res)

    # ---- dependency hooks
    def hook_experiment(self, kind: str, deps: dict[str, dict]) -> None:
        a = self.args
        if safety_gate.env_true("APITEST_READ_ONLY"):
            self.add(Result(f"test_chaos_{kind.replace('-', '_')}").skip(
                "read-only mode (APITEST_READ_ONLY=yes): commands that change a dependency are not run"))
            return
        key_on, key_off = ("stop", "start") if kind == "dep-outage" else ("slow_on", "slow_off")
        chosen = [d for d in deps.values() if key_on in d]
        if not chosen:
            self.add(Result(f"test_chaos_{kind.replace('-', '_')}").skip(
                f"no dependency in --dependency-config defines {key_on}/{key_off}"))
            return
        for d in chosen:
            res = Result(f"test_chaos_{kind.replace('-', '_')}_{d['name']}")
            on, off = d[key_on], d[key_off]
            res.lines.append(f"dependency {d['name']}: {kind} using the user-supplied commands in the dependency config")
            try:
                self.fault_window(res, apply=lambda d=d, on=on, off=off: self._inject(d["name"], on, off),
                                  revert=lambda: self.hooks.undo_all(), fault_s=min(a.outage_s, HARD_MAX_OUTAGE_S), dependency=True,
                                  settle_s=a.settle_s)
            except RuntimeError as exc:
                res.fail("HIGH", str(exc), "the undo command was still run")
            self.add(res)

    def _inject(self, name: str, on: list[str], off: list[str]) -> None:
        if not self.hooks.inject(name, on, off):
            raise RuntimeError(f"the command that creates the fault for {name} failed; nothing was left running")

    # ---- payload bursts
    def payload_experiment(self) -> None:
        a = self.args
        if safety_gate.env_true("APITEST_READ_ONLY"):
            self.add(Result("test_chaos_payload").skip("read-only mode (APITEST_READ_ONLY=yes): POST/PUT/PATCH bursts are not sent"))
            return
        eps = [e for e in self.doc.get("endpoints", []) if e["method"] in ("POST", "PUT", "PATCH") and gen_common.has_json_body(e)]
        if a.payload_endpoints:
            eps = [e for e in eps if e["id"] in a.payload_endpoints]
        eps = eps[:3]
        if not eps:
            self.add(Result("test_chaos_payload").skip("no POST/PUT/PATCH endpoint with a JSON body in the intake"))
            return
        size = min(a.payload_bytes, HARD_MAX_PAYLOAD)
        burst = min(a.burst, HARD_MAX_BURST)
        cases = {
            "oversized": lambda: (json.dumps({"x": "A" * size}).encode(), "application/json"),
            "malformed_json": lambda: (b'{"name": "x", "bad":', "application/json"),
            "deeply_nested": lambda: (b"[" * 800 + b"]" * 800, "application/json"),
            "wrong_content_type": lambda: (b"name=x", "text/plain"),
            "truncated_body": None,
        }
        scheme, host, port, prefix = split_base(self.base)
        for ep in eps:
            path = re.sub(r"\{[^}]+\}", "1", ep["path"])
            for cname, make in cases.items():
                res = Result(f"test_chaos_payload_{gen_common.ident(ep['id'])}_{cname}")
                if self.budget_left() < 20:
                    self.add(res.skip("run budget exhausted"))
                    continue
                t0 = time.monotonic()
                outcomes: list[str] = []
                lock = threading.Lock()

                def send(cname=cname, make=make, path=path, method=ep["method"]) -> None:
                    out = "ok"
                    try:
                        if make is None and scheme == "https":
                            out = "skipped_tls"
                        elif make is None:   # claim 5000 bytes, send 20, then close; the server should not hang or crash
                            s = socket.create_connection((host, port), timeout=a.timeout_s)
                            s.sendall(f"{method} {prefix}{path} HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\n"
                                      f"Content-Length: 5000\r\nConnection: close\r\n\r\n{{\"name\":\"trunc".encode())
                            s.close()
                            out = "closed"
                        else:
                            body, ct = make()
                            cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
                            c = cls(host, port, timeout=a.timeout_s)
                            c.request(method, prefix + path, body=body, headers={**self.headers, "Content-Type": ct})
                            r = c.getresponse()
                            r.read(2048)
                            out = str(r.status)
                            c.close()
                    except (socket.timeout, TimeoutError):
                        out = "timeout"
                    except (OSError, http.client.HTTPException):
                        out = "conn"
                    with lock:
                        outcomes.append(out)

                threads = []
                for i in range(burst):
                    t = threading.Thread(target=send, daemon=True)
                    t.start()
                    threads.append(t)
                    if len(threads) >= 10:      # at most 10 in flight
                        threads.pop(0).join(a.timeout_s + 2)
                for t in threads:
                    t.join(a.timeout_s + 2)
                res.seconds = time.monotonic() - t0
                counts: dict[str, int] = {}
                for o in outcomes:
                    counts[o] = counts.get(o, 0) + 1
                if counts.get("skipped_tls") == len(outcomes) and outcomes:
                    self.add(res.skip("the truncated-body case uses a plain socket and is not run against https targets"))
                    continue
                res.metrics = {"outcomes": counts, "burst": burst, "bytes": size if cname == "oversized" else None}
                res.lines.append(f"{ep['method']} {ep['path']} x{burst} ({cname}): " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
                five = sum(v for k, v in counts.items() if k.startswith("5"))
                if five:
                    res.fail("MEDIUM", f"server error (5xx) on {cname.replace('_', ' ')} input", f"{five} of {burst} requests got 5xx; "
                             "bad input must be rejected with a 4xx")
                if counts.get("timeout"):
                    res.fail("MEDIUM", f"requests hung on {cname.replace('_', ' ')} input", f"{counts['timeout']} client timeouts")
                if counts.get("conn"):
                    res.fail("HIGH", f"connections dropped or refused on {cname.replace('_', ' ')} input", f"{counts['conn']} connection errors")
                alive = self.load()
                ok = sum(1 for _ in range(3) if alive.one()[1] is None)
                if ok < 3:
                    res.fail("HIGH", "the API was not healthy right after the burst", f"{3 - ok} of 3 health probes failed")
                self.add(res)

    # ---- orchestration
    def run(self, experiments: list[str], deps: dict[str, dict]) -> None:
        a = self.args
        base = Result("test_chaos_baseline")
        l = self.load().start()
        time.sleep(a.baseline_s)
        l.stop()
        bs = stats(l.window(0))
        base.metrics = bs
        base.seconds = a.baseline_s
        base.lines.append(f"{bs['requests']} probes, {bs['failed']} failed, p95 {bs['p95_ms']} ms")
        if bs["requests"] == 0 or bs["fail_rate"] > 0.2:
            base.fail("HIGH", "the target is not healthy before any fault is injected",
                      "check the target and the --probe paths (credentials come from the environment variables the intake lists)")
            self.add(base)
            for e in experiments:
                self.results.append(Result(f"test_chaos_{e.replace('-', '_')}").skip("baseline unhealthy"))
            return
        self.add(base)
        for e in experiments:
            if self.budget_left() < 15:
                self.results.append(Result(f"test_chaos_{e.replace('-', '_')}").skip("run budget exhausted"))
                continue
            print(f"experiment {e} ...", flush=True)
            if e in PROXY_EXPERIMENTS:
                self.proxy_experiment(e)
            elif e == "payload":
                self.payload_experiment()
            elif e in HOOK_EXPERIMENTS:
                self.hook_experiment(e, deps)
            else:
                self.dep_proxy_experiment(e)


# --------------------------------------------------------------------------- output
def clean(s: str, n: int = 400) -> str:
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "?", str(s))[:n]


def write_outputs(out: Path, results: list[Result], rerun: str, target: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    cases = []
    for r in results:
        head = f'<testcase classname="chaos.test_chaos" name={quoteattr(clean(r.name, 200))} time="{r.seconds:.2f}">'
        if r.status == "skipped":
            head += f"<skipped message={quoteattr(clean(r.title))}/>"
        elif r.status == "failed":
            lines = [f"[{r.severity}] {clean(r.title)}"] + [clean(x) for x in r.lines] + [f"rerun: {clean(rerun, 700)}"]
            head += f"<failure message={quoteattr(clean(r.title))}>{escape(chr(10).join(lines))}</failure>"
        cases.append(head + "</testcase>")
    fails = sum(1 for r in results if r.status == "failed")
    skips = sum(1 for r in results if r.status == "skipped")
    xml = (f'<?xml version="1.0" encoding="utf-8"?>\n<testsuites><testsuite name="chaos" tests="{len(results)}" failures="{fails}" '
           f'errors="0" skipped="{skips}">\n' + "\n".join(cases) + "\n</testsuite></testsuites>\n")
    (out / "chaos.xml").write_text(xml, encoding="utf-8")
    doc = {"target": clean(safe_target(target)), "rerun": rerun, "experiments": [
        {"name": r.name, "status": r.status, "severity": r.severity, "title": clean(r.title), "seconds": round(r.seconds, 2),
         "details": [clean(x) for x in r.lines], "metrics": r.metrics} for r in results]}
    (out / "chaos.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")


def pick_probes(doc: dict, explicit: list[str]) -> list[str]:
    if explicit:
        bad = [p for p in explicit if not re.fullmatch(r"/[A-Za-z0-9._~/%:@!$&'()*+,;=?-]{0,300}", p)]
        if bad:
            raise BadInput("--probe must be a path starting with / (no host, spaces or fragments)")
        return explicit[:5]
    out = []
    for ep in doc.get("endpoints", []):
        if ep["method"] != "GET" or gen_common.path_params(ep) or gen_common.baseline(ep)["query"] or "{" in ep["path"]:
            continue
        out.append(ep["path"])
    return out[:3]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="chaos_run.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("intake")
    ap.add_argument("--base-url", help="target (default: APITEST_BASE_URL or the intake's first server)")
    ap.add_argument("--experiments", default=",".join(DEFAULT_EXPERIMENTS), help="comma list; 'all' runs every one that is configured. Choices: "
                    + ", ".join(ALL_EXPERIMENTS))
    ap.add_argument("--probe", action="append", default=[], help="GET path used as steady load (repeatable, max 5); default: parameterless GETs from the intake")
    ap.add_argument("--out", default="results/chaos")
    ap.add_argument("--dependency-config", help="JSON file with the dependency start/stop/slow commands (see references/chaos.md)")
    ap.add_argument("--allow-hooks", action="store_true", help="permit the commands in --dependency-config to run (loopback targets only)")
    ap.add_argument("--dep-proxy", metavar="LISTEN_PORT=HOST:PORT", help="TCP fault proxy between the API and a dependency; point the API at 127.0.0.1:LISTEN_PORT")
    ap.add_argument("--rps", type=float, default=5, help=f"steady probe rate (hard max {HARD_MAX_RPS})")
    ap.add_argument("--baseline-s", type=float, default=5)
    ap.add_argument("--fault-s", type=float, default=10, help=f"length of each fault (hard max {HARD_MAX_FAULT_S})")
    ap.add_argument("--outage-s", type=float, default=15, help=f"how long a dependency stays down/slow (hard max {HARD_MAX_OUTAGE_S})")
    ap.add_argument("--settle-s", type=float, default=0, help="extra wait after a dependency command before measuring recovery")
    ap.add_argument("--max-recovery-s", type=float, default=15)
    ap.add_argument("--recovery-wait-s", type=float, default=30, help=f"how long to wait for recovery (hard max {HARD_MAX_RECOVERY_WAIT_S})")
    ap.add_argument("--fail-fast-ms", type=float, default=5000, help="requests failing slower than this during a dependency fault are a finding")
    ap.add_argument("--timeout-s", type=float, default=10, help="client timeout of one probe")
    ap.add_argument("--max-total-s", type=float, default=600, help=f"whole-run budget (hard max {HARD_MAX_TOTAL_S})")
    ap.add_argument("--payload-endpoints", type=lambda s: s.split(","), default=[], help="endpoint ids for the payload experiment (default: first 3 writes)")
    ap.add_argument("--payload-bytes", type=int, default=256 * 1024, help=f"oversized body size (hard max {HARD_MAX_PAYLOAD})")
    ap.add_argument("--burst", type=int, default=10, help=f"requests per payload case (hard max {HARD_MAX_BURST})")
    ap.add_argument("--wait-for-target-s", type=float, default=0, help="wait up to this long for the API to answer (and, with --dep-proxy, to connect through the proxy) before starting")
    ap.add_argument("--dry-run", action="store_true", help="check the gate and print what would run; send nothing, run nothing")
    a = ap.parse_args(argv)

    try:
        doc = _common.load_intake_json(a.intake)
        base = (a.base_url or os.environ.get("APITEST_BASE_URL") or next(iter(doc.get("servers") or []), "")).rstrip("/")
        if not base:
            raise BadInput("no target. Pass --base-url or set APITEST_BASE_URL.")
        reason = safety_gate.gate_reason(base)
        if reason:
            raise Refused(reason)
        a.rps = min(max(a.rps, 0.5), HARD_MAX_RPS)
        a.fault_s = min(max(a.fault_s, 1), HARD_MAX_FAULT_S)
        a.outage_s = min(max(a.outage_s, 1), HARD_MAX_OUTAGE_S)
        a.recovery_wait_s = min(max(a.recovery_wait_s, 1), HARD_MAX_RECOVERY_WAIT_S)
        a.max_total_s = min(max(a.max_total_s, 30), HARD_MAX_TOTAL_S)
        a.baseline_s = min(max(a.baseline_s, 2), 60)
        a.timeout_s = min(max(a.timeout_s, 1), 60)
        a.settle_s = min(max(a.settle_s, 0), 60)
        names = ALL_EXPERIMENTS if a.experiments == "all" else [e.strip() for e in a.experiments.split(",") if e.strip()]
        unknown = [e for e in names if e not in ALL_EXPERIMENTS]
        if unknown or not names:
            raise BadInput(f"unknown experiment(s): {', '.join(unknown) or '(none given)'}. Choose from: {', '.join(ALL_EXPERIMENTS)}")
        deps = load_dependencies(a.dependency_config)
        wants_hooks = any(e in HOOK_EXPERIMENTS for e in names)
        if wants_hooks and deps:
            if not a.allow_hooks:
                raise Refused("dependency commands run only with --allow-hooks, which says you reviewed them")
            if not safety_gate.is_local_host(safety_gate.target_host(base)):
                raise Refused("dependency start/stop commands run on this machine, so they are limited to loopback targets "
                              "(localhost, 127.0.0.1, ::1). Remote environments: use the proxy experiments or your own tooling")
        if wants_hooks and not deps and a.experiments != "all":
            raise BadInput("dep-outage and dep-slow need --dependency-config; commands are never guessed")
        names = [e for e in names if not (e in HOOK_EXPERIMENTS and not deps)]
        probes = pick_probes(doc, a.probe)
        if not probes and any(e != "payload" for e in names):
            raise BadInput("no probe: the intake has no GET endpoint without parameters. Pass --probe /some/path")
        a.payload_bytes = min(max(a.payload_bytes, 1024), HARD_MAX_PAYLOAD)
        a.burst = min(max(a.burst, 1), HARD_MAX_BURST)
        dep_listen = None
        if a.dep_proxy:
            m = re.fullmatch(r"(\d{2,5})=([A-Za-z0-9.\[\]:-]{1,100}:\d{1,5})", a.dep_proxy)
            if not m:
                raise BadInput("--dep-proxy must look like 15432=127.0.0.1:5432")
            dep_listen = (int(m.group(1)), m.group(2))
    except (OSError, ValueError, _common.IntakeError, BadInput) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        print("Ask the user to confirm the target is non-production and authorized, then have them export the variables. "
              "Do not set them yourself.", file=sys.stderr)
        return 3

    script = shlex.quote(sys.argv[0])
    parts = [f"APITEST_BASE_URL={shlex.quote(safe_target(base))}", "python", script, shlex.quote(a.intake), "--experiments", ",".join(names), "--out", shlex.quote(a.out)]
    for flag, val in (("--dependency-config", a.dependency_config), ("--dep-proxy", a.dep_proxy)):
        if val:
            parts += [flag, shlex.quote(val)]
    if a.allow_hooks and a.dependency_config:
        parts.append("--allow-hooks")
    for p in a.probe:
        parts += ["--probe", shlex.quote(p.split("?")[0])]   # a probe's query string can hold a secret, so don't echo it
    parts += ["--fault-s", f"{a.fault_s:g}", "--rps", f"{a.rps:g}"]
    rerun = " ".join(parts)
    print(f"target: {safe_target(base)}  (gate passed; any query string of the target is not echoed)")
    print(f"experiments: {', '.join(names)}; probes: {', '.join(p.split('?')[0] for p in probes) or '(none)'}; "
          f"{a.rps:g} req/s, faults of {a.fault_s:g} s, whole run at most {a.max_total_s:g} s")
    for d in deps.values():
        for key in ("stop", "start", "slow_on", "slow_off"):
            if key in d:
                print(f"  dependency {d['name']} {key}: {mask(d[key])}")
    print(f"Rerun with: {rerun}")
    if SAFE_RERUN_RE.fullmatch(re.sub(r"'", "", rerun)) is None:
        rerun = f"python {script} {shlex.quote(a.intake)} --experiments {','.join(names)} --out {shlex.quote(a.out)}"
    if a.dry_run:
        return 0

    runner = Runner(a, doc, base, probes, auth_headers(doc))
    install_stop_handlers()
    interrupted = False
    try:
        if dep_listen:
            runner.dep_proxy = chaos_proxy.TcpFaultProxy(dep_listen[1], listen_port=dep_listen[0]).start()
            print(f"dependency proxy listening on 127.0.0.1:{dep_listen[0]} -> {dep_listen[1]}")
            deadline = time.monotonic() + min(a.wait_for_target_s, 300)
            while time.monotonic() < deadline and runner.dep_proxy.snapshot().get("connections", 0) == 0:
                time.sleep(0.5)
        deadline = time.monotonic() + min(a.wait_for_target_s, 300)
        while time.monotonic() < deadline and runner.load().one()[1] is not None:   # wait for the API to answer
            time.sleep(1)
        runner.run(names, deps)
    except PermissionError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        interrupted = True
        print("interrupted: undoing faults", file=sys.stderr)
    finally:
        # a second signal during cleanup must not skip the remaining steps; it only marks the run as cut short
        for step in (runner.hooks.undo_all, lambda: runner.dep_proxy and runner.dep_proxy.stop(),
                     lambda: write_outputs(Path(a.out), runner.results, rerun, base)):
            try:
                step()
            except KeyboardInterrupt:
                interrupted = True
                try:
                    step()
                except KeyboardInterrupt:
                    pass
    for line in runner.hooks.log:
        print(f"  hook: {line}")
    bad = [r for r in runner.results if r.status == "failed"]
    print(f"{len(runner.results)} experiments: {sum(1 for r in runner.results if r.status == 'passed')} passed, {len(bad)} failed, "
          f"{sum(1 for r in runner.results if r.status == 'skipped')} skipped. Wrote {a.out}/chaos.xml and chaos.json")
    for r in bad:
        print(f"  FAILED [{r.severity}] {r.name}: {r.title}")
    print(f"Report: python {shlex.quote(str(Path(sys.argv[0]).with_name('summarize_results.py')))} --junit {shlex.quote(a.out)}/chaos.xml "
          f"--intake {shlex.quote(a.intake)} -o reports/chaos-report.html")
    if interrupted:
        print(f"INCOMPLETE: a signal (Ctrl-C/SIGTERM/SIGHUP) cut the run short; faults were undone, results above are partial. "
              f"Exit code {EXIT_INTERRUPTED}.", file=sys.stderr)
        return EXIT_INTERRUPTED
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
