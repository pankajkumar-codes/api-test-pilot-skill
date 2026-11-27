#!/usr/bin/env python3
"""One command from spec to report. Stdlib only; runs the other scripts in this folder.

Usage:
  python run_all.py <spec> [--out DIR] [--base-url URL] [--types all,fuzz] [--assume-bearer [--public-paths GLOBS]]
                    [--tenant-param NAME] [--expect-rate-limit] [--check-env] [--k6 [PROFILES] [--k6-docker]]
                    [--chaos [--chaos-experiments LIST] [--dependency-config FILE --allow-hooks]] [-- <extra pytest arguments>]

Steps: intake, generate the suites, run them in two passes, build the report, print what to set to run the skipped tests.
Pass one runs everything except tests marked `session_end` (sign-out, revoke, password change); pass two runs those last, so they
cannot end the session the other tests use. When you pace the run (APITEST_RPS or APITEST_DELAY_MS) the rate-limit tests cannot send
their burst, so on a local target they are left out of those passes and run in a burst pass of their own, without the pacing, after
pass one and before the session-ending tests; one report covers all passes. Pacing given as pytest options after `--` counts too. A target that is not local keeps your pacing: the tests skip and the command for the burst
pass is printed. `--expect-rate-limit` says the API is meant to throttle (sets APITEST_EXPECT_RATE_LIMIT=yes for the run). Credentials come from the environment (see the intake summary); the safety gate is the
generated suite's own: intrusive types (security, fuzz, resilience) skip unless the target is local or APITEST_TARGET_ENV and
APITEST_AUTHORIZED=yes are set by you. This driver never sets them.

k6 and chaos are opt-in and run after the tests, before the report, through the same scripts you would run by hand, so each applies its
own safety gate (a target that is not local needs your APITEST_TARGET_ENV and APITEST_AUTHORIZED=yes; production is refused) and the
driver stops that part when the gate refuses. `--k6` runs gen_k6.py and run_k6.py for the profiles you name (default load,stress,spike; soak
takes 30 minutes, so name it: `--k6 load,soak`) and `--k6-docker` runs k6 in a container. `--chaos` runs chaos_run.py with its default
proxy and payload experiments; dependency experiments need `--dependency-config FILE` and `--allow-hooks` (your own start and stop commands, loopback
targets only), which are never implied by one another. A threshold crossed or a chaos finding goes into the report and its verdict. Without these flags
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
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from safety_gate import is_local_host, target_host  # noqa: E402


def run(cmd: list[str], env: dict | None = None) -> int:
    print("$ " + " ".join(shlex.quote(c) for c in cmd), flush=True)
    return subprocess.run(cmd, env=env).returncode


PACING_VARS = ("APITEST_RPS", "APITEST_DELAY_MS")
PACING_OPTS = ("--apitest-rps", "--apitest-delay-ms")   # the same two settings as pytest options; they win over the variables
BURST_SETTLE_S = 2   # pause after the burst pass, so the sign-out pass does not start inside a throttled window


def _positive(value: str | None) -> bool:
    """A pacing value that paces: any number above 0. Text that is not a number counts too; the suite then stops with its own error."""
    v = (value or "").strip()
    if not v:
        return False
    try:
        return float(v) > 0
    except ValueError:
        return True


def split_pacing(extra: list[str]) -> tuple[list[str], list[str]]:
    """(extra without the pacing options, the values those options carried). Both spellings: `--apitest-rps 20`, `--apitest-rps=20`."""
    kept, values, i = [], [], 0
    while i < len(extra):
        arg = extra[i]
        if arg in PACING_OPTS:
            values.append(extra[i + 1] if i + 1 < len(extra) else "")
            i += 2
            continue
        if arg.startswith(tuple(o + "=" for o in PACING_OPTS)):
            values.append(arg.split("=", 1)[1])
        else:
            kept.append(arg)
        i += 1
    return kept, values


def is_paced(env: dict, extra: list[str] | None = None) -> bool:
    """True when the user throttles the run, through the variables or the pytest options. 0 or empty means no pacing, as in the suite."""
    return any(_positive(env.get(k)) for k in PACING_VARS) or any(_positive(v) for v in split_pacing(extra or [])[1])


def target_is_local(env: dict, intake: Path) -> bool:
    """Loopback target, judged the way the safety gate does. The URL is the one the suite will use: APITEST_BASE_URL (which
    --base-url sets), else the first server in the intake."""
    url = (env.get("APITEST_BASE_URL") or "").strip()
    if not url:
        try:
            servers = json.loads(intake.read_text(encoding="utf-8")).get("servers") or []
        except (OSError, ValueError):
            return False
        url = servers[0] if servers and isinstance(servers[0], str) else ""
    return is_local_host(target_host(url))   # a URL the gate cannot read is not local


K6_DEFAULT = "load,stress,spike"
K6_PROFILES = ("load", "stress", "spike", "soak")
GATE_REFUSED = 3   # run_k6.py and chaos_run.py both exit 3 when the safety gate refuses the target


def option_problem(args: argparse.Namespace) -> str | None:
    if args.k6:
        bad = [p for p in (x.strip() for x in args.k6.split(",")) if p not in K6_PROFILES]
        if bad or not args.k6.strip(","):
            return (f"--k6 takes a comma list of {', '.join(K6_PROFILES)}, got {args.k6!r}; put the spec file first, or write --k6=load,stress "
                    "(a bare --k6 takes the next word as its profiles)")
    if args.k6_writes and not args.k6:
        return "--k6-writes needs --k6"
    if args.k6_docker and not args.k6:
        return "--k6-docker needs --k6"
    if (args.chaos_experiments or args.dependency_config or args.allow_hooks) and not args.chaos:
        return "--chaos-experiments, --dependency-config and --allow-hooks need --chaos"
    if args.dependency_config and not args.allow_hooks:
        return ("--dependency-config runs the start and stop commands in that file, so it also needs --allow-hooks "
                "(loopback targets only); the two are never implied by each other")
    if args.allow_hooks and not args.dependency_config:
        return "--allow-hooks only applies together with --dependency-config"
    return None


def run_k6_profiles(args: argparse.Namespace, py: str, intake: Path, out: Path, results: Path) -> list[Path]:
    """Generate and run the chosen k6 profiles; return the summary files that exist. A refused gate or a launcher error stops the
    rest of k6 with a message; a crossed threshold does not (it is in the summary and so in the report)."""
    profiles = [p.strip() for p in args.k6.split(",") if p.strip()]
    k6_dir = out / "k6_tests"
    base = ["--base-url", args.base_url] if args.base_url else []
    if run([py, str(HERE / "gen_k6.py"), str(intake), "-o", str(k6_dir), "--profiles", ",".join(profiles)] + (["--include-writes"] if args.k6_writes else []) + base) != 0:
        print("k6: the scripts could not be generated (see above); k6 was skipped", file=sys.stderr)
        return []
    if args.k6_writes and os.environ.get("APITEST_ALLOW_WRITES", "").strip().lower() != "yes":
        print("k6: write requests were generated but APITEST_ALLOW_WRITES is not 'yes', so k6 will skip them. Set it yourself to send them; "
              "this driver never sets it.", file=sys.stderr)
    found: list[Path] = []
    for profile in profiles:
        cmd = [py, str(HERE / "run_k6.py"), str(k6_dir / f"{profile}.js"), "--results-dir", str(results)] + base
        summary = results / f"{profile}-summary.json"
        summary.unlink(missing_ok=True)  # a summary left by an earlier run must not pass for this one
        code = run(cmd + (["--docker"] if args.k6_docker else []))
        if summary.exists():
            found.append(summary)
        if code == GATE_REFUSED:
            print("k6: the safety gate refused this target, so no further k6 profile was run. Set APITEST_TARGET_ENV to a non-production "
                  "value and APITEST_AUTHORIZED=yes yourself if you own it and may load test it.", file=sys.stderr)
            break
        if code in (2, 4, 5):
            print(f"k6: {profile} could not run (exit {code}); the other profiles were skipped", file=sys.stderr)
            break
    return found


def run_chaos(args: argparse.Namespace, py: str, intake: Path, results: Path) -> Path | None:
    """Run chaos_run.py; return its JUnit file when it wrote one. Findings (exit 1) go to the report; a refused gate is said once."""
    chaos_out = results / "chaos"
    cmd = [py, str(HERE / "chaos_run.py"), str(intake), "--out", str(chaos_out)]
    cmd += ["--base-url", args.base_url] if args.base_url else []
    cmd += ["--experiments", args.chaos_experiments] if args.chaos_experiments else []
    cmd += ["--dependency-config", args.dependency_config, "--allow-hooks"] if args.dependency_config else []
    if args.k6:
        # a small server can still be working through the k6 backlog when chaos starts (DVGA was, after the spike profile)
        cmd += ["--wait-for-target-s", "120"]
    (chaos_out / "chaos.xml").unlink(missing_ok=True)  # same reason: only a file this run wrote may reach the report
    code = run(cmd)
    if code == GATE_REFUSED:
        print("chaos: the safety gate refused this target, so nothing was run. Set APITEST_TARGET_ENV to a non-production value and "
              "APITEST_AUTHORIZED=yes yourself if you own it and may inject faults into it.", file=sys.stderr)
    elif code == 2:
        print("chaos: bad input (see above); nothing was run", file=sys.stderr)
    xml = chaos_out / "chaos.xml"
    return xml if xml.exists() else None


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
    ap.add_argument("--expect-rate-limit", action="store_true",
                    help="the API is meant to rate-limit: a burst that is never throttled is a finding (sets APITEST_EXPECT_RATE_LIMIT=yes)")
    ap.add_argument("--check-env", action="store_true", help="only report what is missing (python, pytest, requests, k6, grpcurl) and stop")
    ap.add_argument("--k6", nargs="?", const=K6_DEFAULT, metavar="PROFILES",
                    help=f"after the tests, generate and run k6 (comma list of load,stress,spike,soak; default {K6_DEFAULT})")
    ap.add_argument("--k6-docker", action="store_true", help="with --k6: run k6 in a container instead of a local binary")
    ap.add_argument("--k6-writes", action="store_true",
                    help="with --k6: also generate POST/PUT/PATCH requests (gen_k6.py --include-writes); they still only run when you set APITEST_ALLOW_WRITES=yes")
    ap.add_argument("--chaos", action="store_true", help="after the tests (and k6), run the chaos experiments")
    ap.add_argument("--chaos-experiments", help="with --chaos: comma list for chaos_run.py (default: its proxy and payload experiments)")
    ap.add_argument("--dependency-config", help="with --chaos: JSON file with your dependency start/stop commands (needs --allow-hooks)")
    ap.add_argument("--allow-hooks", action="store_true", help="with --chaos --dependency-config: permit those commands to run (loopback targets only)")
    args = ap.parse_args(argv)

    py = sys.executable
    problem = option_problem(args)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    if args.check_env:
        needs = ["grpcurl"] if args.spec.endswith(".proto") else []
        return run([py, str(HERE / "check_env.py"), "--needs", ",".join(needs)] + (["--base-url", args.base_url] if args.base_url else []))
    if run([py, str(HERE / "check_env.py")]) != 0:
        return 2
    if args.k6 and run([py, str(HERE / "run_k6.py"), "--check"] + (["--docker"] if args.k6_docker else [])) != 0:
        print("error: --k6 was asked for but k6 cannot start here (see above); install it, set K6_BIN or add --k6-docker. Nothing was run.",
              file=sys.stderr)
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
    if args.expect_rate_limit:
        env["APITEST_EXPECT_RATE_LIMIT"] = "yes"
    pytest = [py, "-m", "pytest", str(suite), "-p", "no:cacheprovider", "-q"]
    # A paced run cannot send the burst the rate-limit tests need. On this machine they get a pass of their own without the
    # pacing; anywhere else the pacing is the user's limit on the target, so it is kept and the tests skip with the reason.
    paced = is_paced(env, extra)
    burst = paced and target_is_local(env, intake)
    first = "not session_end and not rate_limit" if burst else "not session_end"
    burst_xml = results / "pass_burst.xml"
    burst_xml.unlink(missing_ok=True)  # only a file this run wrote may reach the report
    run(pytest + ["-m", first, f"--junitxml={results / 'pass1.xml'}"] + extra, env)
    junits = [results / "pass1.xml"]
    if not junits[0].exists():
        print("error: the test run produced no results file", file=sys.stderr)
        return 2
    burst_cmd = pytest + ["-m", "rate_limit and not session_end", f"--junitxml={burst_xml}"] + split_pacing(extra)[0]
    if burst:
        # Before the sign-out pass: the burst needs the session those tests end.
        print("Burst pass: the rate-limit tests run now without the pacing (local target), because a paced run cannot send a burst.",
              flush=True)
        rc3 = run(burst_cmd, {k: v for k, v in env.items() if k not in PACING_VARS})
        if rc3 != 5 and burst_xml.exists():
            junits.append(burst_xml)
        time.sleep(BURST_SETTLE_S)
    rc2 = run(pytest + ["-m", "session_end", f"--junitxml={results / 'pass2.xml'}"] + extra, env)
    if rc2 != 5 and (results / "pass2.xml").exists():
        junits.append(results / "pass2.xml")
    k6_summaries = run_k6_profiles(args, py, intake, out, results) if args.k6 else []
    chaos_xml = run_chaos(args, py, intake, results) if args.chaos else None
    if chaos_xml:
        junits.append(chaos_xml)
    report = out / "report.html"
    rep = [py, str(HERE / "summarize_results.py"), "--intake", str(intake), "--tests-dir", str(suite), "--k6-dir", str(out / "k6_tests"), "--root", str(out.resolve()), "-o", str(report)]
    for j in junits:
        rep += ["--junit", str(j)]
    for k in k6_summaries:
        rep += ["--k6", str(k)]
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
    todo = []
    if paced and not burst:
        todo.append("rate-limit tests skipped because the run is paced and the target is not this machine; when a short burst is "
                    "acceptable there, run them without " + " and ".join(PACING_VARS) + " (or the matching pytest options): "
                    + " ".join(shlex.quote(c) for c in burst_cmd))
    if not args.k6:
        todo.append("k6 load profiles: add --k6 (or run gen_k6.py then run_k6.py)")
    if not args.chaos:
        todo.append("chaos: add --chaos (and --dependency-config FILE --allow-hooks for dependency faults; see references/chaos.md)")
    if todo:
        print("Next: " + "; ".join(todo) + ".")
    return 1 if data["result"]["status"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
