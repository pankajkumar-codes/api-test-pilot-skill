#!/usr/bin/env python3
"""Eval driver: intake -> generate -> run -> report against an eval target, then score expected vs found.

Two modes (never mixed up in the output; every result is labelled with its mode):

  mock    start a local stand-in for the target (or the real local FastAPI app), run each seeded flaw ALONE and a clean
          variant, and score: a flaw is "found" when one of its expected generated tests fails; the clean variant must
          have no failing test. Fully offline, no secrets.
  real    run the same pipeline against a target the person started themselves (`--base-url`). No scoring, because the
          real behaviour is unknown until it runs: it writes the report and lists the failing tests so the person can
          fill the "found" column in docs/evals.md. Credentials come from APITEST_* environment variables only.

Examples (run from the repository root):

  python evals/run_evals.py --list
  python evals/run_evals.py --target all --markdown            # mock mode, all targets, prints the tables
  python evals/run_evals.py --target petstore --variant noauth  # one variant
  python evals/run_evals.py --target petstore --base-url http://localhost:8080/api/v3 --real
  python evals/run_evals.py --target vampi --base-url http://localhost:5000 --real

Safety: the real mode only prints/uses what the generated suite itself allows. Intrusive checks (injection, BOLA, mass
assignment, SSRF, rate limits) run on loopback targets or when YOU exported APITEST_TARGET_ENV (non-production value) and
APITEST_AUTHORIZED=yes; this script never sets those variables and refuses --real without a base URL.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "api-testing" / "scripts"
sys.path[:0] = [str(ROOT / "evals"), str(ROOT / "evals" / "mocks"), str(ROOT / "tests"), str(SCRIPTS)]

import petstore_mock  # noqa: E402
import vampi_mock  # noqa: E402
from mock_graphql import running as gql_running  # noqa: E402

OUT = ROOT / "evals" / "out"  # git-ignored scratch (generated suites, junit, reports)
SPECS = ROOT / "evals" / "specs"


# --------------------------------------------------------------------------------------- targets
# expect: regexes matched against "<file>::<test>" of FAILED generated tests; a flaw is found when any matches.
def flaw(desc: str, owasp: str, *expect: str, gap: str = "") -> dict:
    """`gap` = a known limitation of the skill: the flaw is not detected today and the run documents it instead of failing."""
    return {"desc": desc, "owasp": owasp, "expect": list(expect), "gap": gap}


PETSTORE_FLAWS = {
    "noauth": flaw("protected routes answer without credentials", "API2", r"rejects_missing_credentials", r"invalid_credentials_rejected"),
    "noval": flaw("invalid pet bodies and enum values accepted", "API3/API8", r"test_addPet_rejects_invalid_input\[missing_name\]",
                  r"test_findPetsByStatus_rejects_invalid_input\[query_status_not_in_enum\]"),
    "badid": flaw("non-numeric id is a 500, unknown pet is a 200", "API8", r"test_getPetById_unknown_id_not_found", r"test_getPetById_rejects_invalid_input\[path_petId_wrong_type\]"),
    "trace": flaw("stack trace in error bodies", "API8", r"test_api8_errors_do_not_leak_internals"),
    "crash": flaw("quote in a name causes a database error (500)", "API8/injection", r"test_injection_addPet\[body_name\[0\]\]"),
}
VAMPI_FLAWS = {
    "sqli": flaw("SQL injection through the username path parameter", "API8/injection", r"test_injection_.*get_by_username"),
    "bola": flaw("books of other users readable", "API1 (BOLA)", r"test_api1_bola_.*get_by_title"),
    "pwchange": flaw("any user can change another user's password", "API1/API5", r"test_api1_bola_.*update_password", r"test_api5_.*update_password"),
    "mass": flaw("`admin` accepted at registration", "API3 (mass assignment)", r"test_api3_mass_assignment_.*register_user",
                 gap="the register response does not echo the stored fields and no documented read endpoint exposes `admin`; "
                     "the skill checks the write response only (no read-back of the created record)"),
    "debug": flaw("debug endpoint lists passwords", "API3 (excessive data exposure)", r"test_api3_no_sensitive_fields_.*debug"),
    "enum": flaw("login reveals whether the user exists", "API2", r"test_api2_login_does_not_reveal_valid_usernames"),
    "norate": flaw("login is never throttled", "API4", r"test_api4_rate_limiting_present"),
    "noauthdel": flaw("non-admin can delete users", "API5 (BFLA)", r"test_api5_bfla_.*delete_user"),
}
SEEDED_FLAWS = {
    "offbyone": flaw("limit=N returns N+1 items", "logic", r"test_listItems_limit_one_returns_at_most_one"),
    "noval": flaw("documented limits not enforced on create", "API3/API8", r"test_createItem_rejects_invalid_input\[name_above_max_length\]",
                  r"test_createItem_rejects_invalid_input\[price_below_minimum\]"),
    "status": flaw("wrong status codes (201 documented, 200 sent; missing delete is 200, not 404)", "contract", r"test_createItem_response_contract",
                   r"test_deleteItem_unknown_id_not_found"),
    "bola": flaw("items of other users readable", "API1 (BOLA)", r"test_api1_bola_getItem"),
    "noauth": flaw("no authentication", "API2", r"rejects_missing_credentials"),
}
GRAPHQL_FLAWS = {
    "introspection": flaw("introspection enabled", "GraphQL", r"test_introspection_disabled"),
    "nodepth": flaw("no query depth limit", "GraphQL", r"test_query_depth_is_limited"),
    "noalias": flaw("no alias/field-count limit", "GraphQL", r"test_alias_abuse_is_limited"),
    "batching": flaw("array batching allowed", "GraphQL", r"test_batching_is_disabled_or_limited"),
    "fieldauth": flaw("field-level authorization missing", "API5/GraphQL", r"test_field_authz_User_email"),
    "bola": flaw("object-level authorization missing", "API1 (BOLA)", r"test_bola_pet"),
    "suggest": flaw("schema suggestions in errors", "GraphQL", r"test_error_messages_do_not_suggest_schema_names"),
    "verbose": flaw("stack traces in errors", "API8", r"test_errors_do_not_leak_internals"),
    "crash": flaw("injection crashes the resolver", "injection", r"test_injection_pets"),
}

TARGETS = {
    "petstore": {
        "title": "Swagger Petstore 3 (mock of the documented behaviour)", "stands_in_for": "Swagger Petstore (eval-apis.md row 1)",
        "kind": "rest", "spec": SPECS / "petstore-openapi.yaml", "flaws": PETSTORE_FLAWS,
        "env": {"APITEST_API_KEY_KEY": "eval-key", "APITEST_PETSTORE_AUTH_TOKEN": "eval-token", "APITEST_ALLOW_DESTRUCTIVE": "yes",
                "APITEST_PATH_PETID": "1", "APITEST_PATH_ORDERID": "1", "APITEST_PATH_USERNAME": "theUser"},
        "real_hint": "docker run -d --name petstore -p 127.0.0.1:8080:8080 swaggerapi/petstore3:1.0.29@sha256:97e5aa178dd4d160af9e31a900a685a943cd715cab7ba6ca6377fe976e95d1cf   # then --base-url http://localhost:8080/api/v3",
    },
    "vampi": {
        "title": "VAmPI (mock of the documented vulnerabilities)", "stands_in_for": "deliberately vulnerable API (eval-apis.md row 2; VAmPI)",
        "kind": "rest", "spec": SPECS / "vampi-openapi.yaml", "flaws": VAMPI_FLAWS, "login": ("name1", "pass1"),
        "clean_known": [(r"test_api_views_users_login_user_response_contract",
                         "login needs real credentials; the suite never sends spec-example passwords, so its success-path contract check gets a 400")],
        "env": {"APITEST_ALLOW_DESTRUCTIVE": "yes", "APITEST_PATH_USERNAME": "name1", "APITEST_VICTIM_USERNAME": "name2",
                "APITEST_PATH_BOOK_TITLE": "bookTitle77", "APITEST_VICTIM_BOOK_TITLE": "bookTitle3",
                "APITEST_EXPECT_RATE_LIMIT": "yes", "APITEST_RATE_BURST": "20", "APITEST_ASSUME_NON_ADMIN": "yes", "APITEST_KNOWN_USERNAME": "name2"},
        "real_hint": "docker run -d --name vampi -p 127.0.0.1:5000:5000 erev0s/vampi@sha256:0a5a224b6e14ae7da6a6ea265178ff71286ff903aec74adee98f660bb0e4ca12   # then GET /createdb and log in for a token (see evals/README.md)",
    },
    "seeded": {
        "title": "Seeded FastAPI app (REAL server, local)", "stands_in_for": "small custom FastAPI app with seeded bugs (eval-apis.md row 3)",
        "kind": "fastapi", "flaws": SEEDED_FLAWS,
        "env": {"APITEST_BEARERAUTH_TOKEN": "eval-token-a", "APITEST_PATH_ITEM_ID": "1", "APITEST_VICTIM_ITEM_ID": "2",
                "APITEST_ALLOW_DESTRUCTIVE": "yes", "APITEST_ASSUME_NON_ADMIN": "yes"},
        "real_hint": "SEEDED_BUGS=all python -m uvicorn seeded_fastapi:app --app-dir evals/targets --port 8101   # needs: pip install -r evals/requirements.txt",
    },
    "graphql": {
        "title": "GraphQL shop API (mock; DVGA stand-in)", "stands_in_for": "self-hosted GraphQL API with seeded flaws (eval-apis.md row 4; DVGA)",
        "kind": "graphql", "spec": ROOT / "tests" / "fixtures" / "shop.graphql", "flaws": GRAPHQL_FLAWS,
        "env": {"APITEST_BEARERAUTH_TOKEN": "tokA", "APITEST_GQL_ID": "1", "APITEST_GQL_VICTIM_ID": "2", "APITEST_GQL_EXPECT_FIELD_AUTH": "yes"},
        "real_hint": "docker run -d --name dvga -p 127.0.0.1:5013:5013 -e WEB_HOST=0.0.0.0 dolevf/dvga:2.2.0@sha256:040aa33c199d99f3380c9ff9a1ee5d725e9abca7b189c63a35a2a73bda79c957   # then --base-url http://localhost:5013/graphql",
    },
}


# --------------------------------------------------------------------------------------- servers
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def serve(name: str, variant: str):
    """Yields (base_url, extra_env, spec_path_or_None). variant: 'clean' or a flaw name or 'all'."""
    cfg = TARGETS[name]
    flaws = set(cfg["flaws"]) if variant == "all" else (set() if variant == "clean" else {variant})
    if cfg["kind"] == "fastapi":
        port = free_port()
        env = dict(os.environ, SEEDED_BUGS=",".join(sorted(flaws)) or "none")
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "seeded_fastapi:app", "--app-dir", str(ROOT / "evals" / "targets"),
                                 "--port", str(port), "--log-level", "warning"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(100):
                try:
                    spec = OUT / "seeded-openapi.json"
                    spec.parent.mkdir(parents=True, exist_ok=True)
                    spec.write_bytes(urllib.request.urlopen(base + "/openapi.json", timeout=2).read())
                    break
                except OSError:
                    if proc.poll() is not None:
                        raise RuntimeError("uvicorn exited: " + (proc.stderr.read() or b"").decode()[-500:])
                    time.sleep(0.1)
            else:
                raise RuntimeError("uvicorn did not start")
            yield base, {}, spec
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        return
    if cfg["kind"] == "graphql":
        with gql_running(flaws) as url:
            yield url, {}, None
        return
    if name == "petstore":
        with petstore_mock.running(flaws) as url:
            yield url, {}, None
    else:
        with vampi_mock.running(flaws) as url:
            body = json.dumps({"username": cfg["login"][0], "password": cfg["login"][1]}).encode()
            req = urllib.request.Request(url + "/users/v1/login", data=body, headers={"Content-Type": "application/json"})
            token = json.load(urllib.request.urlopen(req, timeout=5))["auth_token"]
            yield url, {"APITEST_BEARERAUTH_TOKEN": token}, None


# --------------------------------------------------------------------------------------- pipeline
def sh(args: list[str], env: dict | None = None, check: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess:
    p = subprocess.run([sys.executable, *args], capture_output=True, text=True, env=env, cwd=cwd or ROOT, timeout=900)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:2])} failed ({p.returncode}): {(p.stderr or p.stdout)[-800:]}")
    return p


def read_junit(path: Path) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    if path.exists():
        for tc in ET.parse(path).getroot().iter("testcase"):
            key = f"{tc.get('classname', '').split('.')[-1]}::{tc.get('name')}"
            status, msg = "passed", ""
            for child in tc:
                if child.tag in ("failure", "error"):
                    status, msg = "failed", (child.get("message") or "")[:300]
                elif child.tag == "skipped":
                    status, msg = "skipped", (child.get("message") or "")[:200]
            out[key] = (status, msg)
    return out


class EvalError(RuntimeError):
    """A problem the person can fix; reported as one line, exit code 2."""


def run_pipeline(name: str, variant: str, base_url: str, spec: Path, extra_env: dict, work: Path, real: bool) -> dict:
    cfg = TARGETS[name]
    work.mkdir(parents=True, exist_ok=True)
    intake, suite, junit, report = work / "intake.json", work / "suite", work / "junit.xml", work / "report.md"
    if suite.exists():
        shutil.rmtree(suite)
    for stale in (junit, report):  # a file left over from an earlier run must not pass for this run's result
        stale.unlink(missing_ok=True)
    iargs = [str(SCRIPTS / "intake.py"), str(spec), "-o", str(intake)]
    if cfg["kind"] == "graphql":
        iargs += ["--url", base_url.rstrip("/") + "/graphql" if not base_url.rstrip("/").endswith("graphql") else base_url]
    sh(iargs)
    gen = sh([str(SCRIPTS / "gen_pytest.py"), str(intake), "-o", str(suite), "--types", "all"])
    env = {k: v for k, v in os.environ.items() if not k.startswith("APITEST_") or real}
    env.update(cfg["env"] if not real else {})
    env.update(extra_env)
    env["APITEST_BASE_URL"] = base_url
    # pytest exits 1 when tests fail; that is a result here, not an error
    pt = sh(["-m", "pytest", str(suite), "-q", "-p", "no:cacheprovider", "--tb=short", f"--junitxml={junit}"], env=env, check=False)
    if not junit.exists() and pt.returncode == 4 and real:  # preflight refusal (remote target without APITEST_TARGET_ENV)
        raise EvalError((pt.stderr or pt.stdout).strip().removeprefix("ERROR:").strip())
    if not junit.exists():
        raise RuntimeError("pytest produced no JUnit file: " + (pt.stdout + pt.stderr)[-800:])
    sh([str(SCRIPTS / "summarize_results.py"), "--junit", "junit.xml", "--intake", "intake.json", "--tests-dir", "suite",
        "--base-url", base_url, "--title", f"{cfg['title'].split(' (')[0] if real else cfg['title']} / {variant}", "-o", "report.md"],
       env=env, cwd=work)
    results = read_junit(junit)
    gate = sorted({m for st, m in results.values() if st == "skipped" and "safety gate" in m})
    return {"gate_refused": gate,"failed": sorted(k for k, (s, _) in results.items() if s == "failed"),
            "passed": sum(s == "passed" for s, _ in results.values()), "skipped": sum(s == "skipped" for s, _ in results.values()),
            "report": str(report.relative_to(ROOT)) if report.is_relative_to(ROOT) else str(report), "generated": gen.stdout.splitlines()[0] if gen.stdout else ""}


def score(name: str, variant: str, res: dict) -> dict:
    cfg = TARGETS[name]
    if variant == "clean":
        known = cfg.get("clean_known", [])
        res["known_limits"] = [t for t in res["failed"] if any(re.search(p, t) for p, _ in known)]
        res["unexpected"] = [t for t in res["failed"] if t not in res["known_limits"]]
        res["ok"] = not res["unexpected"]
        res["found"] = None
        return res
    spec = cfg["flaws"][variant]
    hits = [t for t in res["failed"] if any(re.search(p, t) for p in spec["expect"])]
    res["found"], res["matched"] = bool(hits), hits[:3]
    res["ok"] = res["found"] or bool(spec.get("gap"))
    return res


def run_target(name: str, variants: list[str], real_url: str | None, spec_override: str | None) -> dict:
    cfg = TARGETS[name]
    out: dict = {"target": name, "title": cfg["title"], "mode": "real" if real_url else ("real-local" if cfg["kind"] == "fastapi" else "mock"),
                 "variants": {}}
    if real_url:
        spec = Path(spec_override) if spec_override else cfg.get("spec")
        if spec is None:
            sys.exit(f"{name}: pass --spec (the seeded app serves /openapi.json)")
        out["variants"]["real"] = run_pipeline(name, "real", real_url, Path(spec), {}, OUT / name / "real", True)
        return out
    for v in variants:
        with serve(name, v) as (base, extra, spec_path):
            spec = spec_path or cfg["spec"]
            res = run_pipeline(name, v, base, Path(spec), extra, OUT / name / v, False)
        out["variants"][v] = score(name, v, res)
        print(f"  {name}/{v}: {res['passed']} passed, {len(res['failed'])} failed, {res['skipped']} skipped"
              + ("" if v == "clean" else f" -> {'FOUND' if res['found'] else 'MISSED'}"), file=sys.stderr)
    return out


def markdown(result: dict) -> str:
    cfg = TARGETS[result["target"]]
    label = {"mock": "mock-based, real target not run", "real-local": "real local server (self-hosted, seeded by us)", "real": "real target run by the person"}[result["mode"]]
    title = result["title"].split(" (")[0] if result["mode"] == "real" else result["title"]
    lines = [f"### {title} ({label})", ""]
    v = result["variants"]
    if "real" in v:
        lines += [f"Failing generated tests: {len(v['real']['failed'])} (see report `{v['real']['report']}`).", ""]
        return "\n".join(lines)
    lines += ["| Seeded flaw | Class | Expected: failing test matches | Found | Matched test |", "|---|---|---|---|---|"]
    for name, spec in cfg["flaws"].items():
        r = v.get(name)
        if r is None:
            continue
        mark = "yes" if r["found"] else ("**NO** (known gap)" if spec.get("gap") else "**NO**")
        lines.append(f"| `{name}`: {spec['desc']} | {spec['owasp']} | `{'`, `'.join(spec['expect'])}` | {mark} | `{(r.get('matched') or ['-'])[0]}` |")
    gaps = [f"- `{n}`: {sp['gap']}" for n, sp in cfg["flaws"].items() if sp.get("gap") and n in v and not v[n]["found"]]
    if gaps:
        lines += ["", "Known gaps (flaw not detected today):", *gaps]
    c = v.get("clean")
    if c:
        lines += ["", f"Clean variant: {c['passed']} passed, {len(c['failed'])} failed, {c['skipped']} skipped"
                      + (f"; unexpected failures: {', '.join(c['unexpected'][:5])}" if c.get("unexpected") else "")]
        for pat, why in cfg.get("clean_known", []):
            if any(re.search(pat, t) for t in c["failed"]):
                lines.append(f"- known limitation, fails on the clean target: `{pat}` ({why})")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="all", help="petstore, vampi, seeded, graphql or all (mock/local mode)")
    ap.add_argument("--variant", action="append", help="clean or a flaw name (repeatable; default: clean + every flaw)")
    ap.add_argument("--base-url", help="REAL mode: URL of a target you started and are allowed to test")
    ap.add_argument("--real", action="store_true", help="REAL mode marker (requires --base-url)")
    ap.add_argument("--spec", help="REAL mode: spec file or URL-downloaded file to use instead of the vendored one")
    ap.add_argument("--markdown", action="store_true", help="print the result tables as Markdown")
    ap.add_argument("--json", help="write the raw results to this file")
    ap.add_argument("--list", action="store_true", help="list targets, flaws and the real-target commands")
    a = ap.parse_args()
    if a.list:
        for n, c in TARGETS.items():
            print(f"{n}: {c['title']}\n  stands in for: {c['stands_in_for']}\n  flaws: {', '.join(c['flaws'])}\n  real: {c['real_hint']}")
        return 0
    if a.real != bool(a.base_url):
        ap.error("--real and --base-url go together (real mode never guesses a target)")
    names = list(TARGETS) if a.target == "all" else [a.target]
    if a.base_url and len(names) != 1:
        ap.error("real mode needs exactly one --target")
    for n in names:
        if n not in TARGETS:
            ap.error(f"unknown target {n}")
    if any(TARGETS[n]["kind"] == "fastapi" for n in names) and not a.base_url:
        import importlib.util
        if not all(importlib.util.find_spec(m) for m in ("fastapi", "uvicorn")):
            ap.error("the 'seeded' target needs fastapi and uvicorn: pip install -r evals/requirements.txt "
                     "(or pick other targets with --target petstore|vampi|graphql)")
    results = []
    for n in names:
        variants = a.variant or ["clean", *TARGETS[n]["flaws"]]
        print(f"[{n}]", file=sys.stderr)
        try:
            results.append(run_target(n, variants, a.base_url, a.spec))
        except EvalError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except RuntimeError as exc:  # a pipeline step failed: show the cause without a traceback
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=2) + "\n")
    if a.markdown:
        print("\n\n".join(markdown(r) for r in results))
    for r in results:  # real mode: skipped intrusive checks mean the gate refused the target, which must not look like a clean run
        for v in r["variants"].values():
            if r["mode"] == "real" and v.get("gate_refused"):
                print("ERROR: the safety gate refused the intrusive checks, so this run is INCOMPLETE (no injection, BOLA, mass assignment, SSRF "
                      "or rate-limit results). Reason: " + v["gate_refused"][0] + "\nExport APITEST_TARGET_ENV (non-production value) and "
                      "APITEST_AUTHORIZED=yes yourself if you own or may test this target, then run again.", file=sys.stderr)
                return 2
    bad = [(r["target"], v) for r in results for v, x in r["variants"].items() if not x.get("ok", True) and r["mode"] != "real"]
    for t, v in bad:
        print(f"FAIL: {t}/{v} did not meet its expectation", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
