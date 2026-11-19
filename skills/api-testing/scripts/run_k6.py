#!/usr/bin/env python3
"""Run a generated k6 script after the safety gate passes. Stdlib only.

Usage:
  APITEST_TARGET_ENV=staging APITEST_AUTHORIZED=yes python run_k6.py k6_tests/load.js [--base-url URL]
                             [--results-dir results] [--k6 "/path/to/k6"] [--docker [--docker-image IMAGE]] [--dry-run]
  python run_k6.py --check [--k6 ...] [--docker]        # can k6 start here? prints its version; sends nothing

What it does, in order:
  1. Resolve the target (--base-url, APITEST_BASE_URL, or the default recorded in manifest.json).
  2. Enforce the safety gate (scripts/safety_gate.py): production is refused; non-local targets need
     APITEST_TARGET_ENV (non-production) and APITEST_AUTHORIZED=yes. Nothing is sent when the gate fails.
  3. Find k6 (PATH, --k6 or K6_BIN). --k6 may be a command with arguments (split like a shell would, but run without
     a shell). --docker runs the grafana/k6 image instead, for machines where the binary cannot be installed or started.
     If k6 is missing, print the install hint and the exact command; exit 4.
  4. Print the exact rerun command (no secrets), run k6 with --summary-export, and exit with k6's status. When k6 exits
     99 the exit code says which kind: 91 error rate or checks, 92 latency only (99 when the summary cannot say), and the
     last line names them.

Credentials are inherited from the environment, never passed on the command line, never printed.
Arbitrary k6 flags are not forwarded: caps and ramp limits live in the script and must not be bypassed.
Exit codes: 0 ok, 2 bad input, 3 gate refused, 4 k6 not installed, 5 k6 is installed but cannot start (--check),
otherwise k6's own code (107 = script/init error such as a gate refusal inside the script). A crossed threshold is reported
as 91 (error rate or checks, including when latency also crossed), 92 (latency only) or 99 (kind unknown).
"""
from __future__ import annotations

import argparse
import http.server
import json
import math
import os
import re
import shlex
import shutil
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import safety_gate  # noqa: E402

INSTALL_HINT = "install k6 from https://grafana.com/docs/k6/latest/set-up/install-k6/ (single binary), then rerun"
PROFILE_RE = re.compile(r"^[a-z]+\.js$")
DOCKER_IMAGE = "grafana/k6"
IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,100}(:[A-Za-z0-9._-]{1,128})?(@sha256:[0-9a-f]{64})?$")
ENV_NAME_RE = re.compile(r"^(APITEST_[A-Z0-9_]+|K6_[A-Z0-9_]+)$")


def resolve_base_url(arg: str | None, script: Path) -> str | None:
    if arg:
        return arg
    env = os.environ.get("APITEST_BASE_URL", "").strip()
    if env:
        return env
    manifest = script.parent / "manifest.json"
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("defaultBaseUrl") or None
    except (OSError, ValueError):
        return None


def build_command(k6: str | list[str], script: Path, summary: Path) -> list[str]:
    prefix = [k6] if isinstance(k6, str) else list(k6)
    return [*prefix, "run", "--quiet", "--no-usage-report", "--summary-export", str(summary), str(script)]


INPUT_FILE_VARS = ("APITEST_IDS_FILE", "APITEST_EXAMPLES_FILE")


def input_files(env: dict) -> dict[str, Path]:
    """The ids and examples files named in the environment, when they exist (a container needs them mounted)."""
    out = {}
    for var in INPUT_FILE_VARS:
        raw = (env.get(var) or "").strip()
        if raw and Path(raw).is_file():
            out[var] = Path(raw)
    return out


def docker_command(image: str, script: Path, results: Path, env_names: list[str], files: dict[str, Path] | None = None) -> list[str]:
    """k6 in a container. Variables are passed by name only (`-e NAME`), so values never appear on the command line.
    Host networking reaches a target on the host's localhost; it works on Linux (Docker Desktop needs the
    `host.docker.internal` address in --base-url instead)."""
    cmd = ["docker", "run", "--rm", "-i", "--network", "host",
           "-v", f"{script.resolve().parent}:/scripts:ro", "-v", f"{results.resolve()}:/results"]
    for var, path in (files or {}).items():  # read-only mounts; the variable then holds the path inside the container
        cmd += ["-v", f"{path.resolve()}:/inputs/{var}.json:ro", "-e", f"{var}=/inputs/{var}.json"]
    for name in env_names:
        if name not in (files or {}):
            cmd += ["-e", name]
    return [*cmd, image, "run", "--quiet", "--no-usage-report", "--summary-export", f"/results/{script.stem}-summary.json",
            f"/scripts/{script.name}"]


def passthrough_env(env: dict) -> list[str]:
    return sorted(k for k in env if ENV_NAME_RE.match(k))


def k6_prefix(arg: str | None, use_docker: bool) -> list[str] | None:
    """The command that starts k6, without its arguments. None when nothing is found."""
    if use_docker:
        return None  # built per run by docker_command
    raw = arg or os.environ.get("K6_BIN")
    if raw:
        try:
            parts = shlex.split(raw)
        except ValueError:
            return None
        return parts or None
    found = shutil.which("k6")
    return [found] if found else None


def check_k6(arg: str | None, use_docker: bool, image: str) -> int:
    """Print whether k6 can start here. Nothing is sent to any target."""
    if use_docker and not shutil.which("docker"):
        print("docker not found; install it or drop --docker")
        return 4
    cmd = ["docker", "run", "--rm", image, "version"] if use_docker else (k6_prefix(arg, False) or [])
    if not use_docker and cmd:
        cmd = [*cmd, "version"]
    if not cmd:
        print(f"k6 not found; {INSTALL_HINT}, or use --docker")
        return 4
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"k6 cannot start: {type(exc).__name__}: {exc}")
        print("In a sandboxed agent the person may need to approve running it; or use --docker if Docker works here.")
        return 5
    if proc.returncode != 0:
        print(f"k6 cannot start (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[:300]}")
        return 5
    print(f"k6 can start: {(proc.stdout or proc.stderr).strip().splitlines()[0][:200]}")
    return 0


TOKEN_RE = re.compile(r"^[\x21-\x7e]{1,4096}$")   # one printable token, no spaces or line breaks
TOKEN_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,80}$")
TOKEN_MIN_GAP_S = 30.0                            # the command runs at most this often, however many VUs ask (APITEST_TOKEN_MIN_GAP_S)
TOKEN_MAX_REFRESHES = 200                         # and at most this many times after the first run (APITEST_TOKEN_MAX_REFRESHES)
TOKEN_CMD_TIMEOUT_S = 30


class TokenSource:
    """Runs APITEST_TOKEN_CMD (split like a shell would, run without a shell) and hands out the token it prints.
    Nothing about the output is ever printed: a failure says only the exit status.

    Single flight: every token has a generation number. A caller that says which generation it holds gets the current token
    without a new sign-in when the current one is newer, so many VUs that were rejected together cause one command run, not one
    each. Only a rejection of the current token runs the command again, and then at most once per `min_gap` seconds and at most
    `max_refreshes` times in the run. This matters for APIs with one live session per user (each sign-in ends the previous one) and
    for sign-in rate limits."""

    def __init__(self, command: list[str], env: dict, min_gap: float = TOKEN_MIN_GAP_S, max_refreshes: int = TOKEN_MAX_REFRESHES):
        self.command, self.env = command, env
        self.min_gap, self.max_refreshes = min_gap, max_refreshes
        self._lock = threading.Lock()
        self._token: str | None = None
        self._at = 0.0
        self.generation = 0     # 0: no token yet
        self.refreshes = 0      # command runs after the first one

    def _run(self) -> str:
        try:
            proc = subprocess.run(self.command, env=self.env, capture_output=True, text=True, timeout=TOKEN_CMD_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"APITEST_TOKEN_CMD could not run ({type(exc).__name__})") from None
        token = proc.stdout.strip()
        if proc.returncode != 0:
            raise RuntimeError(f"APITEST_TOKEN_CMD exited with status {proc.returncode}")
        if not TOKEN_RE.match(token):
            raise RuntimeError("APITEST_TOKEN_CMD must print one token (printable characters, no spaces or line breaks); its output is not shown")
        return token

    def issue(self, have: int | None = None) -> tuple[str, int]:
        """The token and its generation. `have`: the generation the caller holds and found rejected (or wants renewed);
        None asks for whatever is current."""
        with self._lock:
            if self._token is not None:
                newer = have is None or have < self.generation
                if newer or time.monotonic() - self._at < self.min_gap or self.refreshes >= self.max_refreshes:
                    return self._token, self.generation
            token = self._run()
            if self._token is not None:
                self.refreshes += 1
            self._token, self._at = token, time.monotonic()
            self.generation += 1
            return token, self.generation

    def fresh(self, force: bool = False) -> str:
        """The current token; with `force`, a renewed one if the gap and the refresh limit allow it."""
        return self.issue(self.generation if force else None)[0]


def serve_tokens(source: TokenSource) -> tuple[http.server.ThreadingHTTPServer, str]:
    """A loopback-only server for the script to fetch tokens from, on a random port behind a random path."""
    nonce = secrets.token_urlsafe(24)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            path, _, query = self.path.partition("?")
            if path != f"/{nonce}":
                self.send_error(404)
                return
            have = None
            for part in query.split("&"):
                if part.startswith("have=") and part[5:].isdigit() and len(part) < 14:
                    have = int(part[5:])
            try:
                token, generation = source.issue(have)
                body = token.encode()
            except RuntimeError:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header("X-Token-Gen", str(generation))
            self.send_header("Content-Type", "text/plain")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:  # no access log: the path is the secret
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.token_source = source   # type: ignore[attr-defined]  # read by the launcher for its end-of-run line
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/{nonce}"


def manifest_token_env(script: Path) -> list[str]:
    try:
        names = json.loads((script.parent / "manifest.json").read_text(encoding="utf-8")).get("tokenEnv") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [n for n in names if isinstance(n, str) and TOKEN_NAME_RE.match(n)]


def _env_limit(name: str, default: float, lo: float, hi: float) -> float:
    """A number from the environment, kept within [lo, hi]; anything unreadable gives the default."""
    try:
        value = float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default
    return min(max(value, lo), hi) if math.isfinite(value) else default


def start_token_refresh(script: Path, env: dict) -> tuple[http.server.ThreadingHTTPServer | None, str | None]:
    """When APITEST_TOKEN_CMD is set: run it once, put the token in the variable(s) the script reads, and start the
    loopback server that gives the script fresh tokens. Returns (server, error message)."""
    raw = os.environ.get("APITEST_TOKEN_CMD", "").strip()
    if not raw:
        return None, None
    try:
        source = TokenSource(shlex.split(raw), dict(os.environ), min_gap=_env_limit("APITEST_TOKEN_MIN_GAP_S", TOKEN_MIN_GAP_S, 1, 3600),
                             max_refreshes=int(_env_limit("APITEST_TOKEN_MAX_REFRESHES", TOKEN_MAX_REFRESHES, 0, 100000)))
        first = source.fresh()
    except ValueError:
        return None, "APITEST_TOKEN_CMD is not a valid command line"
    except RuntimeError as exc:
        return None, str(exc)
    wanted = os.environ.get("APITEST_TOKEN_ENV", "")
    names = [n.strip() for n in wanted.split(",")] if wanted else manifest_token_env(script)
    names = [n for n in names if TOKEN_NAME_RE.match(n)]
    if not names:
        return None, ("APITEST_TOKEN_CMD is set but no token variable is known (the spec has no bearer scheme): "
                      "set APITEST_TOKEN_ENV to the variable the script reads, e.g. APITEST_BEARERAUTH_TOKEN")
    for n in names:
        env[n] = first
    server, env["APITEST_TOKEN_URL"] = serve_tokens(source)
    every = os.environ.get("APITEST_TOKEN_REFRESH_S")
    print(f"token refresh: APITEST_TOKEN_CMD (output not shown) feeds {', '.join(names)}" + (f"; every {every} s and after a 401" if every else "; after a 401"))
    return server, None


COVERAGE_MARKER = "APITEST_COVERAGE "
MAX_COVERAGE_BYTES = 1_000_000


def parse_coverage_line(line: str) -> dict | None:
    """The JSON after the marker in one line of k6 output, or None. k6 wraps console.log in a logfmt line
    (`time=... level=info msg="APITEST_COVERAGE {\\"tested\\":[...]}" source=console`), so both the bare and the
    quoted form are accepted. Only the expected keys survive, each cut down to plain names and short text."""
    at = line.find(COVERAGE_MARKER)
    if at == -1 or len(line) > MAX_COVERAGE_BYTES:
        return None
    rest = line[at + len(COVERAGE_MARKER):].rstrip()
    start, end = rest.find("{"), rest.rfind("}")
    if start == -1 or end < start:
        return None
    raw = rest[start:end + 1]
    doc = None
    for text in (raw, raw.replace('\\"', '"').replace("\\\\", "\\")):
        try:
            doc = json.loads(text)
            break
        except ValueError:
            continue
    if not isinstance(doc, dict):
        return None

    def short(v: object, n: int = 300) -> str:
        return str(v)[:n]
    tested = [short(t, 80) for t in doc.get("tested") or [] if isinstance(t, str)][:500]
    skipped = [{"endpoint": short(s.get("endpoint"), 80), "reason": short(s.get("reason")),
                "set": [short(v, 80) for v in (s.get("set") or []) if isinstance(v, str)][:10]}
               for s in doc.get("skipped") or [] if isinstance(s, dict)][:500]
    failed = [{"endpoint": short(s.get("endpoint"), 80), "status": s.get("status") if isinstance(s.get("status"), int) else 0}
              for s in doc.get("baselineFailed") or [] if isinstance(s, dict)][:500]
    return {"tested": tested, "skipped": skipped, "baseline_failed": failed, "baseline_probe": doc.get("baselineProbe") is not False}


def run_and_collect(cmd: list[str], env: dict, coverage_path: Path) -> int:
    """Run k6, pass its error stream through unchanged, and keep the coverage line it prints from setup()."""
    coverage = None
    proc = subprocess.Popen(cmd, env=env, stderr=subprocess.PIPE, text=True, errors="replace", bufsize=1)
    assert proc.stderr is not None
    for line in proc.stderr:
        sys.stderr.write(line)
        if COVERAGE_MARKER in line:
            coverage = parse_coverage_line(line) or coverage
    code = proc.wait()
    if coverage is not None:
        try:
            coverage_path.write_text(json.dumps(coverage, indent=2), encoding="utf-8")
        except OSError:
            pass
    return code


def coverage_path_says_all_failed(path: Path) -> bool:
    """True when the coverage record shows a baseline probe that left no endpoint to test."""
    try:
        cov = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(cov, dict) and not cov.get("tested") and bool(cov.get("baseline_failed"))


EXIT_ERRORS = 91     # error rate or checks threshold crossed (also when latency was crossed too: failures come first)
EXIT_LATENCY = 92    # only a latency threshold was crossed
EXIT_THRESHOLD = 99  # k6's own code, kept when the summary cannot say which kind


def threshold_exit_code(families: list[str]) -> int:
    if "error rate" in families or "checks" in families:
        return EXIT_ERRORS
    if families == ["latency"]:
        return EXIT_LATENCY
    return EXIT_THRESHOLD


def crossed_families(summary: Path) -> list[str]:
    """Which kinds of threshold were crossed, read from the --summary-export file (true = crossed)."""
    try:
        metrics = json.loads(summary.read_text(encoding="utf-8")).get("metrics") or {}
    except (OSError, ValueError, AttributeError):
        return []
    found: set[str] = set()
    for name, m in metrics.items():
        th = m.get("thresholds") if isinstance(m, dict) else None
        if not isinstance(th, dict) or not any(v is True for v in th.values()):
            continue
        base = str(name).split("{")[0]
        found.add("error rate" if base == "http_req_failed" else "latency" if base == "http_req_duration"
                  else "checks" if base == "checks" else str(base)[:40])
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run_k6.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("script", nargs="?", help="a script generated by gen_k6.py, e.g. k6_tests/load.js")
    ap.add_argument("--base-url", help="target base URL (overrides APITEST_BASE_URL and the generated default)")
    ap.add_argument("--results-dir", default="results", help="where the k6 summary JSON is written (default: results/)")
    ap.add_argument("--k6", help="the k6 binary or a command that starts it (default: k6 on PATH, or K6_BIN)")
    ap.add_argument("--docker", action="store_true", help=f"run k6 in a container ({DOCKER_IMAGE}) instead of a local binary")
    ap.add_argument("--docker-image", default=DOCKER_IMAGE, help="image for --docker (pin a tag or digest for repeatable runs)")
    ap.add_argument("--check", action="store_true", help="print whether k6 can start here (its version) and exit; sends nothing")
    ap.add_argument("--dry-run", action="store_true", help="check the gate and print the command; start nothing")
    args = ap.parse_args(argv)

    if not IMAGE_RE.match(args.docker_image):
        print("error: --docker-image must be a plain image name, optionally with a tag or sha256 digest", file=sys.stderr)
        return 2
    if args.check:
        return check_k6(args.k6, args.docker, args.docker_image)
    if not args.script:
        print("error: pass a generated k6 script, or --check", file=sys.stderr)
        return 2
    script = Path(args.script)
    if not script.is_file() or not PROFILE_RE.match(script.name):
        print(f"error: {args.script} is not a generated k6 script (expected load.js, stress.js, spike.js or soak.js)", file=sys.stderr)
        return 2
    base = resolve_base_url(args.base_url, script)
    if not base:
        print("error: no target. Pass --base-url or set APITEST_BASE_URL.", file=sys.stderr)
        return 2
    base = base.rstrip("/")

    reason = safety_gate.gate_reason(base)
    if reason:
        print(f"REFUSED: {reason}", file=sys.stderr)
        print("Ask the user to confirm the target is non-production and authorized, then have them export the variables. "
              "Do not set them yourself.", file=sys.stderr)
        return 3

    results = Path(args.results_dir)
    summary = results / f"{script.stem}-summary.json"
    if args.docker:
        k6 = ["docker"] if shutil.which("docker") else None
        cmd = docker_command(args.docker_image, script, results, passthrough_env({**os.environ, "APITEST_BASE_URL": base}),
                             input_files(os.environ))
    else:
        k6 = k6_prefix(args.k6, False)
        cmd = build_command(k6 or ["k6"], script, summary)
    rerun = f"APITEST_BASE_URL={shlex.quote(base)} python {shlex.quote(sys.argv[0])} {shlex.quote(args.script)}" + (
        f" --docker --docker-image {shlex.quote(args.docker_image)}" if args.docker and args.docker_image != DOCKER_IMAGE
        else " --docker" if args.docker else "")
    print(f"target: {base}  (gate passed)")
    print(f"k6 command: {' '.join(shlex.quote(c) for c in cmd)}")
    print(f"Rerun with: {rerun}")
    if args.dry_run:
        return 0
    if not k6:
        print("error: docker not found; install it or drop --docker" if args.docker else f"error: k6 not found; {INSTALL_HINT}, or use --docker",
              file=sys.stderr)
        return 4
    results.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["APITEST_BASE_URL"] = base
    server, problem = start_token_refresh(script, env)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    if args.docker and server is not None:   # the container must also get the token variables and the refresh URL (by name)
        cmd = docker_command(args.docker_image, script, results, passthrough_env(env),
                             input_files(os.environ))
    try:
        code = run_and_collect(cmd, env, results / f"{script.stem}-coverage.json")
    except OSError as exc:
        print(f"error: could not start k6 ({type(exc).__name__}). Run `run_k6.py --check` to see why.", file=sys.stderr)
        return 5
    finally:
        if server is not None:
            server.shutdown()
            src = getattr(server, "token_source", None)
            if src is not None:
                print(f"token refreshed {src.refreshes} time(s) during the run (minimum gap {src.min_gap:g} s, limit {src.max_refreshes})")
    if code == 99:
        families = crossed_families(summary)
        code = threshold_exit_code(families)
        print("thresholds crossed: " + (", ".join(families) if families else "see the k6 output above") + f" (exit {code})")
    if code == 108 and coverage_path_says_all_failed(results / f"{script.stem}-coverage.json"):
        print("every endpoint failed the baseline probe (exit 108): nothing was load tested. Fix the target or its data, or set "
              "APITEST_K6_BASELINE=off to load test them anyway.")
    return code


if __name__ == "__main__":
    sys.exit(main())
