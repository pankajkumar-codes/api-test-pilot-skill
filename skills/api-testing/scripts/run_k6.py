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
     99 the last line names which thresholds were crossed (error rate, latency, checks).

Credentials are inherited from the environment, never passed on the command line, never printed.
Arbitrary k6 flags are not forwarded: caps and ramp limits live in the script and must not be bypassed.
Exit codes: 0 ok, 2 bad input, 3 gate refused, 4 k6 not installed, 5 k6 is installed but cannot start (--check),
otherwise k6's own code (99 = a threshold failed, 107 = script/init error such as a gate refusal inside the script).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
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


def docker_command(image: str, script: Path, results: Path, env_names: list[str]) -> list[str]:
    """k6 in a container. Variables are passed by name only (`-e NAME`), so values never appear on the command line.
    Host networking reaches a target on the host's localhost; it works on Linux (Docker Desktop needs the
    `host.docker.internal` address in --base-url instead)."""
    cmd = ["docker", "run", "--rm", "-i", "--network", "host",
           "-v", f"{script.resolve().parent}:/scripts:ro", "-v", f"{results.resolve()}:/results"]
    for name in env_names:
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
        cmd = docker_command(args.docker_image, script, results, passthrough_env({**os.environ, "APITEST_BASE_URL": base}))
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
    try:
        code = subprocess.run(cmd, env=env).returncode
    except OSError as exc:
        print(f"error: could not start k6 ({type(exc).__name__}). Run `run_k6.py --check` to see why.", file=sys.stderr)
        return 5
    if code == 99:
        families = crossed_families(summary)
        print("thresholds crossed: " + (", ".join(families) if families else "see the k6 output above") + " (exit 99)")
    return code


if __name__ == "__main__":
    sys.exit(main())
