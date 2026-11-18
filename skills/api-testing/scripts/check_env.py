#!/usr/bin/env python3
"""Say what is missing before a run. Stdlib only, offline, changes nothing.

Usage:  python check_env.py [--needs k6,grpcurl,pyyaml] [--base-url URL]

Checks Python (3.11+), pytest and requests (needed to run generated suites), and the optional pieces: PyYAML (YAML specs without the
built-in reader), k6 (load, stress, spike, soak) and grpcurl (gRPC). `--needs` turns optional pieces into required ones for this run.
It also says whether the target needs the safety-gate variables. Exit codes: 0 everything required is there, 1 something required is missing.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

HINTS = {
    "pytest": "python -m pip install pytest",
    "requests": "python -m pip install requests",
    "pyyaml": "python -m pip install pyyaml   (optional: without it a built-in reader handles common YAML)",
    "k6": "install k6: https://grafana.com/docs/k6/latest/set-up/install-k6/   (or put the binary on PATH and pass --k6 to run_k6.py)",
    "grpcurl": "install grpcurl: https://github.com/fullstorydev/grpcurl#installation   (only for gRPC)",
}
MODULES = {"pytest": "pytest", "requests": "requests", "pyyaml": "yaml"}
BINARIES = {"k6": "k6", "grpcurl": "grpcurl"}
REQUIRED = ("pytest", "requests")


def check(needs: set[str]) -> list[tuple[str, str, str]]:
    """(name, status, detail): status is ok, missing (required) or optional (missing, not needed)."""
    rows: list[tuple[str, str, str]] = []
    ok = sys.version_info >= (3, 11)
    rows.append(("python", "ok" if ok else "missing", f"{sys.version.split()[0]}" + ("" if ok else " (3.11 or newer is needed)")))
    for name, mod in MODULES.items():
        found = importlib.util.find_spec(mod) is not None
        required = name in REQUIRED or name in needs
        rows.append((name, "ok" if found else ("missing" if required else "optional"), "" if found else HINTS[name]))
    for name, exe in BINARIES.items():
        path = shutil.which(exe)
        required = name in needs
        rows.append((name, "ok" if path else ("missing" if required else "optional"), path or HINTS[name]))
    return rows


def target_note(base_url: str | None) -> str:
    import safety_gate
    url = base_url or os.environ.get("APITEST_BASE_URL", "")
    if not url:
        return "target: APITEST_BASE_URL is not set (set it to the API you are allowed to test)"
    host = safety_gate.target_host(url) if hasattr(safety_gate, "target_host") else None
    if host and safety_gate.is_local_host(host):
        return f"target: {host} is local, so intrusive tests need no extra variables"
    return (f"target: {host or url} is not local: set APITEST_TARGET_ENV to a non-production value and APITEST_AUTHORIZED=yes for "
            "intrusive tests (or APITEST_READ_ONLY=yes for a public sandbox you only read from)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_env.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--needs", default="", help="comma list of optional pieces this run needs: k6, grpcurl, pyyaml")
    ap.add_argument("--base-url", help="target URL to describe (default: $APITEST_BASE_URL)")
    args = ap.parse_args(argv)
    needs = {n.strip().lower() for n in args.needs.split(",") if n.strip()}
    unknown = needs - set(HINTS)
    if unknown:
        print(f"error: unknown --needs value(s): {', '.join(sorted(unknown))} (choose from {', '.join(sorted(HINTS))})", file=sys.stderr)
        return 2
    rows = check(needs)
    for name, status, detail in rows:
        print(f"{status.upper():9} {name}" + (f"  {detail}" if detail else ""))
    print(target_note(args.base_url))
    bad = [n for n, s, _ in rows if s == "missing"]
    if bad:
        print(f"Missing: {', '.join(bad)}. Nothing was run.")
        return 1
    print("Everything required is installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
