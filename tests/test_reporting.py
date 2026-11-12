"""Phase 4 (reporting) validation: summarize_results.py on REAL pytest and k6 output, plus hostile result files.

  * real pytest: a generated suite is run against tests/mock_rest.py (clean and with seeded bugs); the JUnit XML it writes
    is summarized, and every seeded bug must show up as a ranked finding with a reproducible command;
  * real k6 (only when a binary exists: K6_BIN or PATH, like Phase 3): generated scripts run against the mock, healthy and
    with `down`; the --summary-export JSON is summarized (skips with a reason otherwise, never silently passed);
  * untrusted input: canary secrets in every field of result files, XXE / entity expansion / size / depth / non-regular files,
    Markdown and placeholder injection, NaN and wrong types in JSON.
"""
import json
import os
import re
import subprocess
import sys
import time

import pytest

from conftest import FIXTURES, ROOT
import _common
import gen_k6
import gen_pytest
import parse_openapi
import summarize_results as sr
from mock_rest import running as rest_server
from suite_runner import REST_ENV, run_suite
from test_nonfunctional import K6, _k6_env, needs_k6, run_launcher

SCRIPTS = ROOT / "skills" / "api-testing" / "scripts"
SEVS = sr.SEVERITIES


# ---------------------------------------------------------------- helpers
@pytest.fixture(scope="module")
def rest_doc():
    return parse_openapi.build(str(FIXTURES / "mockapi.yaml"))


@pytest.fixture(scope="module")
def intake_file(tmp_path_factory, rest_doc):
    p = tmp_path_factory.mktemp("intake") / "intake.json"
    p.write_text(json.dumps(rest_doc), encoding="utf-8")
    return p


@pytest.fixture(scope="module")
def rest_suite(tmp_path_factory, rest_doc):
    out = tmp_path_factory.mktemp("rep") / "api_tests"
    gen_pytest.generate(rest_doc, out, ["all"])
    return out


def summarize(*args, env=None, expect=0):
    cmd = [sys.executable, "-I", str(SCRIPTS / "summarize_results.py"), *map(str, args)]
    clean = {k: v for k, v in os.environ.items() if not k.startswith("APITEST_")}
    clean.update(env or {})
    proc = subprocess.run(cmd, capture_output=True, text=True, env=clean, timeout=120)
    assert proc.returncode == expect, proc.stdout[-1500:] + proc.stderr[-1500:]
    return proc


def report_of(tmp_path, *args, env=None, name="report.md"):
    out = tmp_path / name
    summarize(*args, "-o", out, "--date", "2026-11-12", env=env)
    return out.read_text(encoding="utf-8")


def finding_blocks(text):
    parts = re.split(r"(?m)^(?=### \[)", text)
    return [p for p in parts if p.startswith("### [")]


def junit(tmp_path, cases, name="r.xml", extra_root=""):
    """cases: (classname, name, kind, message, text); kind in pass|fail|error|skip."""
    from xml.sax.saxutils import escape, quoteattr
    rows = []
    for classname, tname, kind, message, body in cases:
        inner = {"pass": "", "fail": "failure", "error": "error", "skip": "skipped"}[kind]
        if inner:
            inner = f"<{inner} message={quoteattr(message)}>{escape(body)}</{inner}>"
        rows.append(f"<testcase classname={quoteattr(classname)} name={quoteattr(tname)} time=\"0.01\">{inner}</testcase>")
    p = tmp_path / name
    p.write_text(f'<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">{"".join(rows)}</testsuite>{extra_root}</testsuites>',
                 encoding="utf-8")
    return p


def k6_doc(**over):
    doc = {"root_group": {"name": "", "checks": {}, "groups": {}}, "metrics": {
        "http_reqs": {"count": 100, "rate": 5.0},
        "http_req_failed": {"passes": 0, "fails": 100, "value": 0, "thresholds": {"rate<0.01": False}},
        "http_req_duration": {"avg": 3.0, "p(95)": 9.0, "p(99)": 12.0, "max": 20.0, "thresholds": {"p(95)<500": False}},
        "checks": {"passes": 100, "fails": 0, "value": 1}}}
    doc.update(over)
    return doc


def md_outside_fences(text):
    """Yield the lines of a report that are NOT inside a fenced code block (CommonMark fence rules)."""
    fence = None
    for line in text.split("\n"):
        m = re.match(r"^ {0,3}(`{3,})\s*$", line)
        if fence is None:
            if m:
                fence = len(m.group(1))
                continue
            yield line
        elif m and len(m.group(1)) >= fence:
            fence = None
    assert fence is None, "a fenced block was left open"


# ---------------------------------------------------------------- real pytest output
def run_real_pytest(suite, tmp_path, bugs):
    junit_path = tmp_path / "pytest.xml"
    with rest_server(bugs) as url:
        results, proc = run_suite(suite, url, REST_ENV, junit=junit_path)
    assert junit_path.exists(), proc.stdout[-1000:] + proc.stderr[-1000:]
    return junit_path, results


def test_real_pytest_clean_server_gives_a_pass_report(rest_suite, tmp_path, intake_file):
    junit_path, results = run_real_pytest(rest_suite, tmp_path, set())
    text = report_of(tmp_path, "--junit", junit_path, "--intake", intake_file, "--base-url", "http://127.0.0.1:8080")
    assert "**Result: PASS**" in text and "## Findings (most severe first)\nNone." in text
    assert not finding_blocks(text)
    assert "# Test report — Mock Pets API — 2026-11-12" in text
    assert "Skipped tests, by reason:" in text, "skips are reported with their reasons, not hidden"
    assert f"of {len(results)} tests" in text


SEEDED = {"bola", "mass", "leak", "contract", "noauth", "cors", "stacktrace"}


def test_real_pytest_seeded_bugs_become_ranked_findings_with_repro_commands(rest_suite, tmp_path, intake_file):
    junit_path, results = run_real_pytest(rest_suite, tmp_path, SEEDED)
    n_failed = sum(1 for s, _ in results.values() if s == "failed")
    assert n_failed > 10
    text = report_of(tmp_path, "--junit", junit_path, "--intake", intake_file, "--base-url", "http://127.0.0.1:8080",
                     "--max-findings", "1000")
    blocks = finding_blocks(text)
    assert len(blocks) == n_failed, "one finding per failing test"
    sevs = [re.match(r"### \[(\w+)\]", b).group(1) for b in blocks]
    assert sevs == sorted(sevs, key=SEVS.index), "most severe first"
    assert sevs[0] == "HIGH" and "MEDIUM" in sevs
    for b in blocks:
        assert "**Reproduce**" in b and "python -m pytest" in b, "every finding has a runnable pytest command"
        cmds = b.split("**Reproduce**")[1].split("```")[1]
        assert re.search(r"api_tests/test_\w+\.py::", cmds)
        assert "APITEST_BASE_URL=http://127.0.0.1:8080" in cmds
    # seeded bugs by name: BOLA on getPet, mass assignment on createPet, contract violation on listPets
    assert re.search(r"(?s)### \[HIGH\].*?Endpoint: GET /pets/\\\{petId\\\}  \\\(getPet\\\)", text)
    assert "api1_bola_getPet" in text and "test_api3_mass_assignment_createPet" in text
    assert "test_listPets_response_contract" in text
    # curl repro lines from the runtime are carried over (already redacted by the runtime)
    assert re.search(r"curl -sS -i -X GET http://127\.0\.0\.1:\d+/pets", text)
    assert "tokA" not in text
    # tagged severities come from the test; untagged ones are labelled as defaults
    assert "Rating: tagged by the test" in text
    # coverage: every endpoint of the intake appears, failures counted
    for ep_id in ("listPets", "getPet", "createPet", "deleteToy", "adminStats"):
        assert re.search(rf"\\\({ep_id}\\\) \|", text), ep_id
    # reruns: pytest command for the first finding really reproduces that failure on the same server
    first = blocks[0]
    node = re.search(r"python -m pytest '?(api_tests/\S+?)'? -v", first).group(1)
    with rest_server(SEEDED) as url:
        env = {k: v for k, v in os.environ.items() if not k.startswith("APITEST_")} | REST_ENV | {"APITEST_BASE_URL": url}
        proc = subprocess.run([sys.executable, "-m", "pytest", node.replace("api_tests/", str(rest_suite) + "/"), "-p", "no:cacheprovider", "-q"],
                              env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1 and "1 failed" in proc.stdout, proc.stdout[-800:]


def test_without_intake_endpoints_come_from_request_lines(rest_suite, tmp_path):
    junit_path, _ = run_real_pytest(rest_suite, tmp_path, {"contract"})
    text = report_of(tmp_path, "--junit", junit_path)
    assert "No `--intake` file was given" in text
    assert re.search(r"Endpoint: (GET|POST) /pets", text)


def test_report_is_deterministic(rest_suite, tmp_path, intake_file):
    junit_path, _ = run_real_pytest(rest_suite, tmp_path, {"contract", "bola"})
    a = report_of(tmp_path, "--junit", junit_path, "--intake", intake_file, name="a.md")
    b = report_of(tmp_path, "--junit", junit_path, "--intake", intake_file, name="b.md")
    assert a == b


# ---------------------------------------------------------------- real k6 output
def _run_k6(rest_doc, tmp_path, bugs, profile="load", **kw):
    out = tmp_path / "k6_tests"
    gen_k6.generate(rest_doc, out, [profile], base_url="http://localhost:1", rate=3, quick=True, **kw)
    results = tmp_path / "results"
    with rest_server(bugs) as url:
        proc = run_launcher(out / f"{profile}.js", "--base-url", url, "--k6", K6, env=_k6_env(), results=results)
    return proc, results / f"{profile}-summary.json"


@needs_k6
def test_real_k6_healthy_run_has_numbers_and_no_findings(rest_doc, tmp_path, intake_file):
    proc, summary = _run_k6(rest_doc, tmp_path, set())
    assert proc.returncode == 0, proc.stdout[-1500:]
    text = report_of(tmp_path, "--k6", summary, "--intake", intake_file)
    assert "**Result: PASS**" in text and "### Profile load" in text
    assert re.search(r"Requests \| \d+", text) and "CROSSED" not in text
    assert "http\\_req\\_duration\\{endpoint:" in text, "per-endpoint submetrics are listed"


@needs_k6
def test_real_k6_failing_server_crosses_thresholds_and_is_reported_high(rest_doc, tmp_path, intake_file):
    proc, summary = _run_k6(rest_doc, tmp_path, {"down"})
    assert proc.returncode == 99, proc.stdout[-1500:]       # k6 itself says a threshold was crossed
    text = report_of(tmp_path, "--k6", summary, "--intake", intake_file, "--base-url", "http://staging.example.test:8080")
    assert "**Result: FAIL**" in text and "**CROSSED**" in text
    blocks = finding_blocks(text)
    assert blocks and all(b.startswith("### [HIGH]") for b in blocks if "http\\_req\\_failed" in b.split("\n", 1)[0])
    b = next(b for b in blocks if "http\\_req\\_failed" in b.split("\n", 1)[0])
    assert "APITEST_BASE_URL=http://staging.example.test:8080 python scripts/run_k6.py k6_tests/load.js" in b
    assert "APITEST_TARGET_ENV" in b and "yourself" in b, "the safety gate is explained, never pre-set"
    assert "APITEST_AUTHORIZED=yes" not in b.split("**Reproduce**")[1].split("```")[1], "the command never sets the gate variables"
    assert "observed 1" in b


@needs_k6
@pytest.mark.parametrize("profile,extra", [("spike", {}), ("stress", {"steps": 2})])
def test_real_k6_stage_tables_for_spike_and_stress(rest_doc, tmp_path, intake_file, profile, extra):
    proc, summary = _run_k6(rest_doc, tmp_path, set(), profile=profile, **extra)
    assert proc.returncode == 0, proc.stdout[-1500:]
    text = report_of(tmp_path, "--k6", summary)
    assert "| Stage | p95 latency | Failed-request rate |" in text
    assert "recovery" in text


# ---------------------------------------------------------------- k6 parsing without a binary (shape verified on real output above)
def test_k6_threshold_true_means_crossed_and_observed_values_are_shown(tmp_path):
    d = k6_doc()
    d["metrics"]["http_req_duration"]["thresholds"] = {"p(95)<5": True}
    d["metrics"]["http_req_failed"]["value"] = 0.2
    d["metrics"]["http_req_failed"]["thresholds"] = {"rate<0.01": True}
    p = tmp_path / "load-summary.json"
    p.write_text(json.dumps(d))
    text = report_of(tmp_path, "--k6", p)
    assert "### [HIGH] k6 threshold crossed: http\\_req\\_failed" in text and "### [MEDIUM] k6 threshold crossed: http\\_req\\_duration" in text
    assert text.index("[HIGH]") < text.index("[MEDIUM]")
    assert "observed 9 ms" in text and "observed 0.2" in text


def test_stress_threshold_findings_are_capped_at_medium(tmp_path):
    d = k6_doc()
    d["metrics"]["http_req_failed"]["thresholds"] = {"rate<0.5": True}
    p = tmp_path / "stress-summary.json"
    p.write_text(json.dumps(d))
    text = report_of(tmp_path, "--k6", p)
    assert "### [MEDIUM] k6 threshold crossed: http\\_req\\_failed" in text and "[HIGH]" not in text and "breaking point" in text


def test_failed_k6_checks_are_findings(tmp_path):
    d = k6_doc()
    d["root_group"]["checks"] = {"no errors": {"name": "no errors", "passes": 90, "fails": 10}}
    p = tmp_path / "load-summary.json"
    p.write_text(json.dumps(d))
    text = report_of(tmp_path, "--k6", p)
    assert "k6 check failed: no errors" in text and "10 of 100 checks failed" in text


@pytest.mark.parametrize("payload", ['{"metrics": {"http_reqs": {"count": NaN}}}', "[1,2]", "{}", '{"metrics": []}', "nope", "[" * 5000])
def test_bad_k6_json_is_a_clean_error(tmp_path, payload):
    p = tmp_path / "load-summary.json"
    p.write_text(payload)
    proc = summarize("--k6", p, "-o", tmp_path / "r.md", expect=2)
    assert proc.stderr.startswith("error:") and "Traceback" not in proc.stderr


def test_wrong_types_in_k6_json_become_n_a_not_crashes(tmp_path):
    d = k6_doc()
    d["metrics"]["http_reqs"] = {"count": "lots", "rate": True}
    d["metrics"]["http_req_duration"] = {"p(95)": None, "thresholds": {"p(95)<5": "yes", 3: True}}
    d["metrics"]["weird"] = 5
    d["metrics"]["http_req_duration{stage:x}"] = {"p(95)": [1]}
    p = tmp_path / "load-summary.json"
    p.write_text(json.dumps(d))
    text = report_of(tmp_path, "--k6", p)
    assert "n/a" in text


# ---------------------------------------------------------------- severity, endpoints, errors
def test_default_severity_and_tags_and_ranking(tmp_path):
    p = junit(tmp_path, [
        ("api_tests.test_edge", "test_x_edge", "fail", "Failed: plain assertion", "plain assertion"),
        ("api_tests.test_security", "test_api1_bola_getPet", "fail", "m", "[HIGH][API1:2023] getPet: request was accepted (200)"),
        ("api_tests.test_security", "test_untagged", "fail", "m", "something odd"),
        ("api_tests.test_contract", "test_c", "fail", "m", "[LOW] minor\n  GET http://h/pets -> 200 (2 ms)\n  repro: curl -sS -i -X GET http://h/pets"),
        ("api_tests.test_smoke", "test_ok", "pass", "", ""),
    ])
    text = report_of(tmp_path, "--junit", p)
    order = re.findall(r"(?m)^### \[(\w+)\] (.*)$", text)
    assert [s for s, _ in order] == ["HIGH", "HIGH", "LOW", "LOW"]
    assert "OWASP: API1:2023" in text
    assert text.count("Rating: default for this test type") == 2 and text.count("Rating: tagged by the test") == 2
    assert "curl -sS -i -X GET http://h/pets" in text


def test_endpoint_mapping_by_name_and_by_templated_request_path(tmp_path, intake_file):
    p = junit(tmp_path, [
        ("t.test_negative", "test_getPet_rejects_missing_credentials", "fail", "m", "unexpected status\n  GET http://h/pets/7 -> 200 (1 ms)"),
        ("t.test_security", "test_api8_headers", "fail", "m", "[LOW] headers\n  GET http://h/pets/42/toys/9?x=1 -> 200 (1 ms)"),
        ("t.test_security", "test_no_hint", "fail", "m", "[LOW] nothing to map"),
    ])
    text = report_of(tmp_path, "--junit", p, "--intake", intake_file)
    assert re.search(r"Endpoint: GET /pets/\\\{petId\\\}  \\\(getPet\\\)", text)
    assert re.search(r"Endpoint: GET /pets/\\\{petId\\\}/toys/\\\{toyId\\\}  \\\(getToy\\\)", text)
    assert "Endpoint: \\(API\\-wide\\)" in text or "Endpoint: \\(API-wide\\)" in text
    assert "Endpoints in the intake with no test result" in text


def test_errors_are_run_problems_not_findings(tmp_path):
    p = junit(tmp_path, [("t.test_a", f"test_{i}", "error", "fixture 'x' not found", "E fixture 'x' not found") for i in range(3)]
              + [("t.test_a", "test_f", "fail", "m", "[HIGH] real")])
    text = report_of(tmp_path, "--junit", p)
    assert len(finding_blocks(text)) == 1
    assert "**Result: FAIL**" in text and "3 errored" in text
    assert "## Run problems\n- " in text


def test_only_errors_is_incomplete_not_pass(tmp_path):
    p = junit(tmp_path, [("t.test_a", "test_1", "error", "boom", "boom")])
    assert "**Result: INCOMPLETE**" in report_of(tmp_path, "--junit", p)


def test_max_findings_truncates_least_severe_first(tmp_path):
    p = junit(tmp_path, [("t.test_a", f"test_{i}", "fail", "m", f"[{'LOW' if i else 'HIGH'}] f{i}") for i in range(6)])
    text = report_of(tmp_path, "--junit", p, "--max-findings", "2")
    assert len(finding_blocks(text)) == 2 and "[HIGH]" in text and "4 more finding(s) not shown" in text


# ---------------------------------------------------------------- secrets
CANARY = {k: f"CANARY_{k}_{'x' * 6}" for k in "ABCDEFGHIJKLMNOPQRST"}


def test_canary_secrets_in_result_files_never_reach_the_report(tmp_path):
    c = CANARY
    message = f"[HIGH] leaked {c['A']} in body"
    body = "\n".join([
        f"[HIGH] token echoed: {c['B']}",
        f"  POST http://bob:{c['C']}@h.example/x?token={c['D']}&api_key={c['E']}&ok=1 -> 200 (3 ms)",
        f'  body: {{"password": "{c["F"]}", "client_secret": "{c["G"]}", "name": "fine"}}',
        f"  body2: {{\\\"access_token\\\": \\\"{c['H']}\\\"}}",
        f"  repro: curl -sS -u alice:{c['I']} -H 'Authorization: Bearer {c['J']}' -H 'Cookie: sid={c['K']}; a=b' "
        f"-H 'X-Api-Key: {c['L']}' --data-raw '{{\"refresh_token\": \"{c['M']}\"}}' 'http://h.example/x?session={c['N']}'",
        f"  jwt: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.{c['O']}",
        f"  exact env value somewhere in prose: {c['P']}",
    ])
    p = junit(tmp_path, [(f"api_tests.test_security_{c['Q']}", f"test_{c['R']}", "fail", message, body),
                         ("api_tests.test_edge", "test_skipped", "skip", f"skipped because password={c['S']}", "")])
    # system-out is never read
    raw = p.read_text().replace("</testcase>", f"<system-out>{c['T']}</system-out></testcase>", 1)
    p.write_text(raw)
    k6 = k6_doc()
    k6["metrics"][f"http_req_duration{{endpoint:tok_{c['A']}}}"] = {"p(95)": 1, "thresholds": {f"p(95)<5 // {c['B']}": True}}
    k6["root_group"]["checks"] = {"k": {"name": f"password={c['C']}", "passes": 1, "fails": 2}}
    kp = tmp_path / "load-summary.json"
    kp.write_text(json.dumps(k6))
    intake = tmp_path / "i.json"
    intake.write_text(json.dumps({"info": {"title": f"API token={c['D']}"}, "endpoints": [
        {"id": f"ep_{c['E']}", "method": "GET", "path": f"/x/{c['F']}"}]}))
    text = report_of(tmp_path, "--junit", p, "--k6", kp, "--intake", intake, "--title", f"My API secret={c['G']}",
                     env={"APITEST_BEARERAUTH_TOKEN": c["P"], "APITEST_AUTHORIZED": "yes"})
    leaked = [k for k, v in c.items() if v in text and k not in ("A", "B", "Q", "R", "T")]
    assert not leaked, f"secret canaries reached the report: {leaked}"
    assert c["T"] not in text, "system-out must never be read"
    # canaries inside k6 names/expressions are scrubbed only when they sit in a secret-looking value; names are shown escaped otherwise
    assert "<redacted>" in text
    # the redacted forms keep the report useful
    assert "ok=1" in text and "name\": \"fine\"" in text
    # JUnit class/test names that merely contain secret-ish words are data, but must still never carry the env secret value
    assert c["P"] not in text


def test_redact_text_unit_cases_and_shared_regex():
    r = _common.redact_text
    assert r("Authorization: Bearer abcdef123456") == "Authorization: <redacted>"
    assert r("see http://u:p@h/x") == "see http://<redacted>@h/x"
    assert r("GET /a?token=zzzz&x=1") == "GET /a?token=<redacted>&x=1"
    assert r("{'password': 'hunter22'}") == "{'password': <redacted>}"
    assert r("value is s3cr3tvalue here", ["s3cr3tvalue"]) == "value is <redacted> here"
    assert r("curl -u me:pw https://h") == "curl -u <redacted> https://h"
    assert r("status: 401 expected: 200") == "status: 401 expected: 200"
    assert _common.secret_values_from_env({"APITEST_X_TOKEN": "abcd1234", "APITEST_AUTHORIZED": "yes", "APITEST_BASE_URL": "http://h",
                                           "OTHER_TOKEN": "nope", "APITEST_Y_KEY": "ab"}) == ["abcd1234"]
    assert re.search(r"_common\.SECRET_KEY_RE|SECRET_KEY_RE", (SCRIPTS / "_common.py").read_text())


@pytest.mark.parametrize("text", ["a=" * 60000, "token=" * 30000, '"' + "\\" * 100000, "password=\\\"" + "a" * 150000,
                                  "authorization:" + " " * 150000, "-u" + " " * 150000, "x" * 200000, "http://" + "a@" * 50000])
def test_redaction_is_linear_on_pathological_input(text):
    t0 = time.time()
    _common.redact_text(text)
    assert time.time() - t0 < 3


# ---------------------------------------------------------------- hostile XML and files
def test_xml_with_doctype_or_entities_is_refused_without_expansion(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-FILE-CONTENT")
    bad = {
        "xxe_file": f'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file://{secret}">]><testsuites><testsuite><testcase classname="a" name="&x;"><failure message="&x;">&x;</failure></testcase></testsuite></testsuites>',
        "xxe_http": '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "http://127.0.0.1:9/x">]><testsuites><testcase classname="a" name="&x;"/></testsuites>',
        "laughs": '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><testsuites><testcase classname="&c;" name="n"/></testsuites>',
        "param_entity": '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY % p SYSTEM "file:///etc/passwd">%p;]><testsuites/>',
        "bare_doctype": '<?xml version="1.0"?><!DOCTYPE testsuites><testsuites/>',
    }
    for name, xml in bad.items():
        p = tmp_path / f"{name}.xml"
        p.write_text(xml)
        proc = summarize("--junit", p, "-o", tmp_path / f"{name}.md", expect=2)
        assert "not accepted" in proc.stderr, (name, proc.stderr)
        assert "TOP-SECRET" not in proc.stdout + proc.stderr
        assert not (tmp_path / f"{name}.md").exists()


def test_doctype_hidden_by_utf16_encoding_is_still_refused(tmp_path):
    p = tmp_path / "u16.xml"
    p.write_bytes('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE r [<!ENTITY x "y">]><testsuites><testcase classname="a" name="&x;"/></testsuites>'.encode("utf-16"))
    proc = summarize("--junit", p, "-o", tmp_path / "o.md", expect=2)
    assert "not accepted" in proc.stderr


def test_undefined_entities_and_malformed_xml_are_clean_errors(tmp_path):
    for i, xml in enumerate(['<testsuites><testcase name="&nope;"/></testsuites>', "<testsuites>", "", "\x00\x01", "not xml"]):
        p = tmp_path / f"m{i}.xml"
        p.write_text(xml)
        proc = summarize("--junit", p, "-o", tmp_path / "o.md", expect=2)
        assert proc.stderr.startswith("error:") and "Traceback" not in proc.stderr


def test_size_depth_and_count_limits(tmp_path, monkeypatch):
    big = tmp_path / "big.xml"
    big.write_text("<testsuites>" + "<testcase classname='a' name='n'/>" * 50 + "</testsuites>")
    monkeypatch.setattr(sr, "MAX_XML_BYTES", 100)
    with pytest.raises(sr.ReportError, match="larger than"):
        sr.parse_junit(str(big))
    monkeypatch.setattr(sr, "MAX_XML_BYTES", 25 * 1024 * 1024)
    monkeypatch.setattr(sr, "MAX_CASES", 10)
    with pytest.raises(sr.ReportError, match="more than"):
        sr.parse_junit(str(big))
    deep = tmp_path / "deep.xml"
    deep.write_text("<a>" * 500 + "</a>" * 500)
    with pytest.raises(sr.ReportError, match="nested too deeply"):
        sr.parse_junit(str(deep))
    long_msg = junit(tmp_path, [("t.test_a", "test_long", "fail", "m", "[HIGH] " + "A" * 500_000)], name="long.xml")
    text = report_of(tmp_path, "--junit", long_msg)
    assert len(text) < 20_000, "one huge failure cannot blow up the report"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_non_regular_files_are_refused_not_read(tmp_path):
    fifo = tmp_path / "pipe.xml"
    os.mkfifo(fifo)
    proc = summarize("--junit", fifo, "-o", tmp_path / "o.md", expect=2)
    assert "not a regular file" in proc.stderr
    proc = summarize("--junit", tmp_path / "missing.xml", "-o", tmp_path / "o.md", expect=2)
    assert "cannot read" in proc.stderr
    proc = summarize("--junit", "/dev/zero", "-o", tmp_path / "o.md", expect=2)
    assert "not a regular file" in proc.stderr


# ---------------------------------------------------------------- Markdown and placeholder injection
HOSTILE = [
    "](javascript:alert(1)) <script>alert(1)</script> ![x](http://evil.example/p.png) <img src=x onerror=alert(1)>",
    "```\n## FAKE HEADING\n```\n~~~\n# another\n~~~",
    "| a | b |\n|---|---|\n| injected | row |",
    "{{title}} {{findings}} {{unknown}} {{provenance}}",
    "http://evil.example/phish www.evil.example mail@evil.example @mention <!-- hidden -->",
    "bidi \u202e reversed \u2066 zero\u200bwidth \u2028 line-sep \u0085 next-line \u00ad soft-hyphen",
    "[click](http://evil.example) *bold* _it_ `code` ~~strike~~ > quote\n- list\n1. item\n# h1",
]


@pytest.mark.parametrize("payload", HOSTILE)
def test_markdown_and_placeholder_injection_is_neutralised(tmp_path, payload):
    p = junit(tmp_path, [(f"t.test_{i}", f"test_{payload}", "fail", payload, payload) for i in range(1)]
              + [("t.test_x", "test_s", "skip", payload, ""), ("t.test_y", "test_e", "error", payload, payload)])
    text = report_of(tmp_path, "--junit", p, "--title", payload, "--base-url", "http://127.0.0.1:8080")
    outside = "\n".join(md_outside_fences(text))
    assert not re.search(r"(?<!\\)<", outside), "raw HTML outside code fences"
    assert not re.search(r"(?<!\\)\]\(", outside) and not re.search(r"(?<!\\)!\[", outside), "link or image syntax outside fences"
    assert "http://evil" not in outside and "www.evil" not in outside
    headings = re.findall(r"(?m)^#{1,6} .*$", outside)
    allowed = re.compile(r"^(# Test report — .*|## (Summary|Coverage per endpoint|Load and performance \(k6\)|Findings \(most severe first\)|"
                         r"Run problems|Not tested / limitations|How this report was produced)|### \[(HIGH|MEDIUM|LOW|CRITICAL|INFO)\] .*)$")
    assert all(allowed.match(h) for h in headings), headings
    assert "{{" not in outside, "placeholder-looking text is escaped"
    for ch in "\u202e\u2066\u200b\u2028\u0085\u00ad":
        assert ch not in text
    assert len(finding_blocks(text)) == 1
    assert text.count("## Findings (most severe first)") == 1 and text.count("## Summary") == 1


def test_placeholders_in_values_are_not_substituted_again(tmp_path):
    p = junit(tmp_path, [("t.test_a", "test_{{title}}", "fail", "{{provenance}}", "[LOW] {{findings}} {{limitations}}")])
    text = report_of(tmp_path, "--junit", p, "--title", "{{verdict}}")
    assert text.count("## How this report was produced") == 1 and text.count("## Not tested / limitations") == 1
    assert sr.fill_template("{{a}} {{b}}", {"a": "{{b}}", "b": "x"}) == "{{b}} x"
    with pytest.raises(sr.ReportError, match="unknown placeholders"):
        sr.fill_template("{{nope}}", {"a": "1"})
    tpl = (ROOT / "skills" / "api-testing" / "templates" / "report.md").read_text()
    names = set(re.findall(r"\{\{(\w+)\}\}", tpl))
    assert names == {"title", "date", "verdict", "summary", "coverage", "k6", "findings", "problems", "limitations", "provenance"}


def test_fence_cannot_be_closed_by_content():
    for body in ["```", "````", "a\n```\nb", "`" * 40]:
        out = sr.fence(body)
        assert list(md_outside_fences(out)) == []
    assert sr.esc("a|b") == "a\\|b" and "<" not in sr.esc("<b>").replace("\\<", "")


# ---------------------------------------------------------------- CLI
def test_cli_errors_and_output_modes(tmp_path):
    p = junit(tmp_path, [("t.test_a", "test_ok", "pass", "", "")])
    summarize(expect=2)                                                       # nothing to summarize
    for bad in (["--base-url", "http://user:pw@h"], ["--base-url", "ftp://h"], ["--base-url", "http://h/?token=x"], ["--base-url", "http://h x"],
                ["--tests-dir", "../x"], ["--tests-dir=-rf"], ["--tests-dir", "a b"], ["--date", "yesterday"], ["--max-findings", "0"]):
        proc = summarize("--junit", p, "-o", tmp_path / "o.md", *bad, expect=2)
        assert proc.stderr.startswith("error:"), bad
    proc = summarize("--junit", p, "-o", "-", "--date", "2026-11-12")
    assert proc.stdout.startswith("# Test report — API — 2026-11-12") and proc.stderr == ""
    proc = summarize("--junit", p, "-o", tmp_path / "sub" / "dir" / "r.md")
    assert (tmp_path / "sub" / "dir" / "r.md").exists() and "Rerun with:" in proc.stderr and "summarize_results.py --junit" in proc.stderr


def test_base_url_is_syntax_checked_only_never_classified(tmp_path):
    # the report neither decides production vs non-production nor trusts host spellings; it only refuses unsafe syntax
    p = junit(tmp_path, [("t.test_a", "test_f", "fail", "m", "[LOW] x")])
    text = report_of(tmp_path, "--junit", p, "--base-url", "http://127.0.0.1.nip.io:8080/v1/")
    assert "APITEST_BASE_URL=http://127.0.0.1.nip.io:8080/v1 python -m pytest" in text
    text = report_of(tmp_path, "--junit", p)
    assert 'APITEST_BASE_URL="${APITEST_BASE_URL:?set the target URL}"' in text


def test_commands_are_shell_quoted_for_odd_test_names(tmp_path):
    name = "test_x[a b;touch HACKED'\"$(id)]"
    p = junit(tmp_path, [("t.test_a", name, "fail", "m", "[LOW] odd")])
    text = report_of(tmp_path, "--junit", p, "--tests-dir", "api_tests")
    cmd = re.search(r"python -m pytest (.*) -v", text).group(1)
    import shlex
    assert shlex.split(cmd) == [f"api_tests/test_a.py::{name}"], "one argument, nothing for the shell to interpret"


def test_skill_docs_point_at_the_report_step():
    skill = (ROOT / "skills" / "api-testing" / "SKILL.md").read_text()
    assert "summarize_results.py" in skill and "references/reporting.md" in skill
    assert (ROOT / "skills" / "api-testing" / "references" / "reporting.md").is_file()


def test_control_characters_in_json_names_are_stripped(tmp_path):
    d = k6_doc()
    d["root_group"]["checks"] = {"k": {"name": "bad\x1b[31m\x07name\u202e\n## HEAD", "passes": 1, "fails": 2}}
    p = tmp_path / "load-summary.json"
    p.write_text(json.dumps(d))
    text = report_of(tmp_path, "--k6", p)
    assert "\x1b" not in text and "\x07" not in text and "\u202e" not in text
    assert not re.search(r"(?m)^## HEAD", text)


def test_fuzz_findings_take_the_most_severe_tag_and_all_repro_lines(tmp_path):
    body = ("fuzzing POST /pets: 2 finding(s) in 40 request(s) (seed=1337)\n"
            "[MEDIUM] server error 500 on malformed input -- case a\n  repro: curl -sS -i -X POST http://h/pets --data-raw 'x'\n"
            "[HIGH] response leaks internals -- case b\n  repro: curl -sS -i -X POST http://h/pets --data-raw 'y'\n"
            "[LOW] other\n  repro: curl -sS -i -X POST http://h/pets --data-raw 'x'")
    p = junit(tmp_path, [("fuzz_tests.test_fuzz", "test_fuzz[createPet]", "fail", "m", body)])
    text = report_of(tmp_path, "--junit", p, "--tests-dir", "fuzz_tests")
    blk = finding_blocks(text)[0]
    assert blk.startswith("### [HIGH]") and "Rating: tagged by the test" in blk
    cmds = blk.split("**Reproduce**")[1]
    assert cmds.count("curl -sS") == 2, "distinct repro lines only"
    assert "'fuzz_tests/test_fuzz.py::test_fuzz[createPet]'" in cmds
    assert "repro:" not in blk.split("**Reproduce**")[0], "repro lines live under Reproduce"


def test_real_fuzz_findings_are_reported(tmp_path, rest_doc):
    out = tmp_path / "fuzz_tests"
    gen_pytest.generate(rest_doc, out, ["fuzz"])
    with rest_server({"crash"}) as url:
        results, proc = run_suite(out, url, {**REST_ENV, "APITEST_FUZZ_CASES": "40"}, ["-k", "createPet"], junit=tmp_path / "fuzz.xml")
    assert any(s == "failed" for s, _ in results.values()), proc.stdout[-800:]
    text = report_of(tmp_path, "--junit", tmp_path / "fuzz.xml", "--tests-dir", "fuzz_tests")
    assert "### [HIGH] fuzzing POST /pets" in text and "test_fuzz[createPet]" in text and "curl -sS -i -X POST" in text
    assert "tokA" not in text
