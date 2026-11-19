#!/usr/bin/env python3
"""One command from spec to report. Stdlib only; runs the other scripts in this folder.

Usage:
  python run_all.py <spec> [--out DIR] [--base-url URL] [--types all,fuzz] [--assume-bearer [--public-paths GLOBS]]
                    [--tenant-param NAME] [--check-env] [-- <extra pytest arguments>]

Steps: intake, generate the suites, run them in two passes, build the report, print what to set to run the skipped tests.
Pass one runs everything except tests marked `session_end` (sign-out, revoke, password change); pass two runs those last, so they
cannot end the session the other tests use. Credentials come from the environment (see the intake summary); the safety gate is the
generated suite's own: intrusive types (security, fuzz, resilience) skip unless the target is local or APITEST_TARGET_ENV and
APITEST_AUTHORIZED=yes are set by you. This driver never sets them. Chaos and k6 runs need their own configuration and are not started here;
the exact commands are printed at the end.

Exit codes: 0 unless the verdict is FAIL (INCOMPLETE and INCONCLUSIVE also exit 0, so read the verdict), 1 findings in the report,
2 a step could not run (bad input, missing tool). Extra pytest arguments go to both passes, so do not pass your own -m.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(cmd: list[str], env: dict | None = None) -> int:
    print("$ " + " ".join(shlex.quote(c) for c in cmd), flush=True)
    return subprocess.run(cmd, env=env).returncode


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    extra: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(prog="run_all.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("spec")
    ap.add_argument("--out", default="apitest_run", help="folder for intake.json, the suite, results and the report (default apitest_run)")
    ap.add_argument("--base-url", help="target; default $APITEST_BASE_URL, else the first server in the spec")
    ap.add_argument("--types", default="all", help="gen_pytest types; `all,fuzz` adds the opt-in fuzz suite (default all)")
    ap.add_argument("--assume-bearer", action="store_true", help="spec declares no security scheme: treat operations as needing a bearer token")
    ap.add_argument("--public-paths", help="with --assume-bearer: comma-separated path globs that stay public")
    ap.add_argument("--tenant-param", help="path parameter that names the tenant (default: detected)")
    ap.add_argument("--check-env", action="store_true", help="only report what is missing (python, pytest, requests, k6, grpcurl) and stop")
    args = ap.parse_args(argv)

    py = sys.executable
    if args.check_env:
        needs = ["grpcurl"] if args.spec.endswith(".proto") else []
        return run([py, str(HERE / "check_env.py"), "--needs", ",".join(needs)] + (["--base-url", args.base_url] if args.base_url else []))
    if run([py, str(HERE / "check_env.py")]) != 0:
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    intake, suite, results = out / "intake.json", out / "suite", out / "results"
    results.mkdir(exist_ok=True)
    cmd = [py, str(HERE / "intake.py"), args.spec, "-o", str(intake)]
    if args.assume_bearer:
        cmd += ["--assume-bearer"] + (["--public-paths", args.public_paths] if args.public_paths else [])
    if run(cmd) != 0:
        return 2
    gen = [py, str(HERE / "gen_pytest.py"), str(intake), "-o", str(suite), "--types", args.types]
    gen += (["--base-url", args.base_url] if args.base_url else []) + (["--tenant-param", args.tenant_param] if args.tenant_param else [])
    if run(gen) != 0:
        return 2

    env = dict(os.environ)
    if args.base_url:
        env["APITEST_BASE_URL"] = args.base_url
    pytest = [py, "-m", "pytest", str(suite), "-p", "no:cacheprovider", "-q"]
    run(pytest + ["-m", "not session_end", f"--junitxml={results / 'pass1.xml'}"] + extra, env)
    rc2 = run(pytest + ["-m", "session_end", f"--junitxml={results / 'pass2.xml'}"] + extra, env)
    junits = [results / "pass1.xml"] + ([results / "pass2.xml"] if rc2 != 5 and (results / "pass2.xml").exists() else [])
    if not junits[0].exists():
        print("error: the test run produced no results file", file=sys.stderr)
        return 2
    report = out / "report.html"
    rep = [py, str(HERE / "summarize_results.py"), "--intake", str(intake), "--tests-dir", str(suite), "--k6-dir", str(out / "k6_tests"), "--root", str(out.resolve()), "-o", str(report)]
    for j in junits:
        rep += ["--junit", str(j)]
    if args.base_url:
        rep += ["--base-url", args.base_url]
    if run(rep) != 0:
        return 2
    try:
        data = json.loads(report.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 2
    print("\n" + data["result"]["status"] + ": " + data["result"]["summary"])
    print(f"Report: {report} (and {report.with_suffix('.json')})")
    print("Next: k6 load profiles: gen_k6.py then run_k6.py; chaos: chaos_run.py with your dependency config (see references/chaos.md).")
    return 1 if data["result"]["status"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
