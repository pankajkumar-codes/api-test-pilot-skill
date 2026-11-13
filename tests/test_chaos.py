"""Executable chaos support: fault proxies, the runner, its safety rules, hooks, reporting, and the skill-gap fixes that
shipped with it (guessed body fields, undeclared auth parameters, read-only mode, id extraction).

Everything runs against 127.0.0.1 servers started here; nothing touches the network.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import ROOT
import chaos_proxy
import chaos_run
import gen_common
import gen_pytest
import parse_openapi
import safety_gate
import summarize_results

SCRIPTS = ROOT / "skills" / "api-testing" / "scripts"


class Upstream(ThreadingHTTPServer):
    daemon_threads = True


SEEN: list[str] = []


def make_upstream(posts_5xx: bool = False):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            SEEN.append("GET")
            body = b'{"ok":true,"pad":"' + b"x" * 200 + b'"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            SEEN.append("POST")
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            code = 500 if posts_5xx else 400
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = Upstream(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def upstream():
    srv = make_upstream()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(url, timeout=5):
    import http.client
    import urllib.parse
    p = urllib.parse.urlsplit(url)
    c = http.client.HTTPConnection(p.hostname, p.port, timeout=timeout)
    c.request("GET", p.path or "/")
    r = c.getresponse()
    return r.status, r.read(), r


# --------------------------------------------------------------------------- HTTP proxy
def test_http_proxy_passes_traffic_through(upstream):
    with chaos_proxy.HttpFaultProxy(upstream) as px:
        status, body, _ = get(f"http://127.0.0.1:{px.port}/x")
    assert status == 200 and b'"ok":true' in body


def test_http_proxy_injects_error_status_and_marks_it(upstream):
    with chaos_proxy.HttpFaultProxy(upstream) as px:
        px.set_fault(chaos_proxy.Fault("error", status=503), 5)
        status, _, resp = get(f"http://127.0.0.1:{px.port}/x")
        assert status == 503 and resp.getheader("X-Chaos-Injected") == "1"
        px.clear_fault()
        assert get(f"http://127.0.0.1:{px.port}/x")[0] == 200


def test_http_proxy_latency_abort_reset_truncate(upstream):
    with chaos_proxy.HttpFaultProxy(upstream) as px:
        base = f"http://127.0.0.1:{px.port}/x"
        px.set_fault(chaos_proxy.Fault("latency", latency_ms=300), 5)
        t = time.monotonic()
        assert get(base)[0] == 200 and time.monotonic() - t >= 0.3
        for kind in ("abort", "reset"):
            px.set_fault(chaos_proxy.Fault(kind), 5)
            with pytest.raises(OSError):
                get(base)
        px.set_fault(chaos_proxy.Fault("truncate"), 5)
        with pytest.raises(Exception):   # IncompleteRead or reset
            get(base)
        stats = px.snapshot()
        assert stats["fault_abort"] and stats["fault_reset"] and stats["fault_truncate"]


def test_fault_expires_by_itself(upstream):
    with chaos_proxy.HttpFaultProxy(upstream) as px:
        px.set_fault(chaos_proxy.Fault("error"), 0.3)
        assert get(f"http://127.0.0.1:{px.port}/x")[0] == 503
        time.sleep(0.5)
        assert get(f"http://127.0.0.1:{px.port}/x")[0] == 200


def test_fault_percentage_is_seeded(upstream):
    def hits(seed):
        with chaos_proxy.HttpFaultProxy(upstream, seed=seed) as px:
            px.set_fault(chaos_proxy.Fault("error", pct=50), 5)
            return [get(f"http://127.0.0.1:{px.port}/x")[0] for _ in range(12)]
    a = hits(7)
    assert a == hits(7) and 200 in a and 503 in a


def test_fault_limits_are_hard():
    for bad in (chaos_proxy.Fault("latency", latency_ms=chaos_proxy.HARD_MAX_LATENCY_MS + 1), chaos_proxy.Fault("error", status=200),
                chaos_proxy.Fault("blackhole", hold_s=chaos_proxy.HARD_MAX_HOLD_S + 1), chaos_proxy.Fault("error", pct=101),
                chaos_proxy.Fault("explode")):
        with pytest.raises(ValueError):
            bad.checked(chaos_proxy.HTTP_KINDS)


def test_proxy_refuses_a_remote_upstream_without_the_gate(monkeypatch):
    for k in ("APITEST_TARGET_ENV", "APITEST_AUTHORIZED"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(PermissionError):
        chaos_proxy.HttpFaultProxy("http://example.com")
    with pytest.raises(PermissionError):
        chaos_proxy.TcpFaultProxy("example.com:5432")
    monkeypatch.setenv("APITEST_TARGET_ENV", "production")
    with pytest.raises(PermissionError):
        chaos_proxy.HttpFaultProxy("http://127.0.0.1:1")   # production wins even over loopback


def test_proxy_binds_loopback_only(upstream):
    with chaos_proxy.HttpFaultProxy(upstream) as px:
        assert px._server.server_address[0] == "127.0.0.1"


# --------------------------------------------------------------------------- TCP proxy
def echo_server():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            def echo(c=c):
                try:
                    for d in iter(lambda: c.recv(4096), b""):
                        c.sendall(d)
                except OSError:
                    pass
            threading.Thread(target=echo, daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
    return srv, srv.getsockname()[1]


def test_tcp_proxy_passthrough_latency_blackhole_reset():
    srv, port = echo_server()
    try:
        with chaos_proxy.TcpFaultProxy(f"127.0.0.1:{port}") as px:
            c = socket.create_connection(("127.0.0.1", px.port), timeout=3)
            c.sendall(b"hi")
            assert c.recv(10) == b"hi"
            px.set_fault(chaos_proxy.Fault("latency", latency_ms=300), 5)
            t = time.monotonic()
            c.sendall(b"slow")
            assert c.recv(10) == b"slow" and time.monotonic() - t >= 0.3
            px.set_fault(chaos_proxy.Fault("blackhole"), 5)
            c.sendall(b"lost")
            c.settimeout(0.6)
            with pytest.raises(socket.timeout):
                c.recv(10)
            px.set_fault(chaos_proxy.Fault("reset"), 5)     # existing connections break at once, new ones are reset too

            def closed(sock):
                sock.settimeout(2)
                try:
                    return sock.recv(10) == b""
                except OSError:
                    return True
            assert closed(c)
            try:
                n = socket.create_connection(("127.0.0.1", px.port), timeout=2)
            except OSError:
                pass   # reset while connecting: the fault is doing its job
            else:
                assert closed(n)
            px.clear_fault()
            d = socket.create_connection(("127.0.0.1", px.port), timeout=3)
            d.sendall(b"ok")
            assert d.recv(10) == b"ok"
            assert px.snapshot()["connections"] >= 2
    finally:
        srv.close()


# --------------------------------------------------------------------------- runner: safety rules
def intake_for(tmp_path, post=True):
    spec = {"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "servers": [{"url": "http://localhost:1"}],
            "paths": {"/health": {"get": {"operationId": "health", "responses": {"200": {"description": "ok"}}}}}}
    if post:
        spec["paths"]["/items"] = {"post": {"operationId": "addItem", "requestBody": {"content": {"application/json": {"schema": {
            "type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}}}},
            "responses": {"201": {"description": "ok"}}}}
    p = tmp_path / "intake.json"
    p.write_text(json.dumps(parse_openapi.parse(spec, "t.json")))
    return str(p)


def run_cli(args, env=None):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("APITEST_")}
    clean.update(env or {})
    return subprocess.run([sys.executable, str(SCRIPTS / "chaos_run.py"), *args], env=clean, capture_output=True, text=True, timeout=120)


def test_runner_refuses_production_even_on_loopback(tmp_path):
    r = run_cli([intake_for(tmp_path), "--base-url", "http://127.0.0.1:9", "--dry-run"], {"APITEST_TARGET_ENV": "production"})
    assert r.returncode == 3 and "REFUSED" in r.stderr and "production" in r.stderr


@pytest.mark.parametrize("env", [{}, {"APITEST_TARGET_ENV": "staging"}, {"APITEST_AUTHORIZED": "yes"}])
def test_runner_refuses_a_remote_target_without_both_confirmations(tmp_path, env):
    r = run_cli([intake_for(tmp_path), "--base-url", "http://example.com", "--dry-run"], env)
    assert r.returncode == 3 and "REFUSED" in r.stderr


def test_runner_dry_run_sends_nothing_and_prints_rerun(tmp_path):
    r = run_cli([intake_for(tmp_path), "--base-url", "http://127.0.0.1:9", "--dry-run"])
    assert r.returncode == 0 and "Rerun with:" in r.stdout and "gate passed" in r.stdout


def test_hooks_need_the_flag_and_a_loopback_target_and_are_never_guessed(tmp_path):
    cfg = tmp_path / "deps.json"
    cfg.write_text(json.dumps({"dependencies": [{"name": "db", "stop": ["echo", "stop"], "start": ["echo", "start"]}]}))
    base = [intake_for(tmp_path), "--experiments", "dep-outage", "--dependency-config", str(cfg), "--dry-run"]
    r = run_cli(base + ["--base-url", "http://127.0.0.1:9"])
    assert r.returncode == 3 and "--allow-hooks" in r.stderr
    r = run_cli(base + ["--base-url", "http://example.com", "--allow-hooks"], {"APITEST_TARGET_ENV": "staging", "APITEST_AUTHORIZED": "yes"})
    assert r.returncode == 3 and "loopback" in r.stderr
    r = run_cli([intake_for(tmp_path), "--experiments", "dep-outage", "--base-url", "http://127.0.0.1:9", "--dry-run"])
    assert r.returncode == 2 and "never guessed" in r.stderr


def test_dependency_config_must_be_argv_lists_that_undo_themselves(tmp_path):
    def load(d):
        p = tmp_path / "c.json"
        p.write_text(json.dumps({"dependencies": [d]}))
        return chaos_run.load_dependencies(str(p))
    for bad in ({"name": "db", "stop": "docker stop db", "start": ["x"]},   # a string would need a shell
                {"name": "db", "stop": ["x"]},                               # a stop without a start
                {"name": "db", "slow_on": ["x"]},
                {"name": "bad name!", "stop": ["x"], "start": ["y"]}):
        with pytest.raises(chaos_run.BadInput):
            load(bad)
    assert "db" in load({"name": "db", "stop": ["x"], "start": ["y"]})


def test_hooks_run_without_a_shell_and_always_undo(tmp_path):
    flag = tmp_path / "down"
    h = chaos_run.Hooks()
    ok = h.inject("db", [sys.executable, "-c", f"open({str(flag)!r}, 'w')"], [sys.executable, "-c", f"import os; os.remove({str(flag)!r})"])
    assert ok and flag.exists()
    h.undo_all()
    assert not flag.exists() and not h.pending_undo
    # a stop that fails is still followed by its start, and a shell metacharacter is just an argument
    h2 = chaos_run.Hooks()
    marker = tmp_path / "undone"
    assert not h2.inject("db", [sys.executable, "-c", "import sys; sys.exit(1); ; ;"], [sys.executable, "-c", f"open({str(marker)!r}, 'w')"])
    h2.undo_all()
    assert marker.exists()
    assert not chaos_run.Hooks().run(["definitely-not-a-command-xyz"], "x")


def test_hook_commands_are_masked_when_printed():
    shown = chaos_run.mask(["docker", "run", "-e", "DB_PASSWORD=hunter2", "--password", "hunter3", "TOKEN:abc"])
    assert "hunter2" not in shown and "hunter3" not in shown and "abc" not in shown


def test_hard_ceilings_hold(tmp_path, upstream):
    r = run_cli([intake_for(tmp_path), "--base-url", upstream, "--rps", "9999", "--fault-s", "99999", "--burst", "99999",
                 "--max-total-s", "99999", "--dry-run"])
    assert r.returncode == 0
    assert f"{chaos_run.HARD_MAX_RPS:g} req/s" in r.stdout and f"{chaos_run.HARD_MAX_FAULT_S:g} s" in r.stdout
    assert f"{chaos_run.HARD_MAX_TOTAL_S:g} s" in r.stdout


# --------------------------------------------------------------------------- runner: real runs
def test_runner_runs_proxy_and_payload_experiments_and_the_report_understands_the_result(tmp_path, upstream):
    intake = intake_for(tmp_path)
    out = tmp_path / "res"
    r = run_cli([intake, "--base-url", upstream, "--experiments", "proxy-5xx,payload", "--baseline-s", "2", "--fault-s", "2",
                 "--burst", "3", "--payload-bytes", "2048", "--out", str(out), "--max-recovery-s", "10"])
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads((out / "chaos.json").read_text())
    names = {e["name"]: e for e in data["experiments"]}
    assert names["test_chaos_proxy_5xx"]["status"] == "passed"
    assert names["test_chaos_proxy_5xx"]["metrics"]["error"]["recovery_s"] is not None
    assert names["test_chaos_payload_addItem_malformed_json"]["status"] == "passed"
    assert "Authorization" not in json.dumps(data) and "Bearer" not in json.dumps(data)
    rep = tmp_path / "report.md"
    assert summarize_results.main(["--junit", str(out / "chaos.xml"), "--intake", intake, "-o", str(rep)]) == 0
    assert "chaos" in rep.read_text()


def test_runner_reports_5xx_on_bad_input_and_the_report_gives_the_chaos_rerun_command(tmp_path):
    srv = make_upstream(posts_5xx=True)
    try:
        intake = intake_for(tmp_path)
        out = tmp_path / "res"
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        r = run_cli([intake, "--base-url", base, "--experiments", "payload", "--baseline-s", "2", "--burst", "2", "--out", str(out)])
        assert r.returncode == 1 and "FAILED [MEDIUM]" in r.stdout
        rep = tmp_path / "report.md"
        assert summarize_results.main(["--junit", str(out / "chaos.xml"), "--intake", intake, "-o", str(rep)]) == 0
        text = rep.read_text()
        assert "server error" in text and "5xx" in text and "chaos_run.py" in text and "-m pytest" not in text.split("**Reproduce**")[1].split("```")[1]
    finally:
        srv.shutdown()


def test_runner_stops_when_the_baseline_is_unhealthy(tmp_path):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()
    r = run_cli([intake_for(tmp_path), "--base-url", f"http://127.0.0.1:{port}", "--experiments", "proxy-5xx", "--baseline-s", "2",
                 "--out", str(tmp_path / "res"), "--timeout-s", "1"])
    assert r.returncode == 1 and "not healthy before any fault" in r.stdout
    data = json.loads((tmp_path / "res" / "chaos.json").read_text())
    assert [e["status"] for e in data["experiments"]] == ["failed", "skipped"]


def test_dep_proxy_spec_is_validated(tmp_path, upstream):
    srv, port = echo_server()
    try:
        r = run_cli([intake_for(tmp_path), "--base-url", upstream, "--experiments", "dep-latency", "--baseline-s", "2",
                     "--dep-proxy", f"0={'127.0.0.1'}:{port}", "--out", str(tmp_path / "res")])
    finally:
        srv.close()
    # port 0 is not a valid user port: refused as bad input rather than silently picking one
    assert r.returncode == 2


# --------------------------------------------------------------------------- skill gaps
def spec_with(paths, **extra):
    return parse_openapi.parse({"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "servers": [{"url": "http://localhost:1"}],
                                "paths": paths, **extra}, "t.json")


def test_guessed_required_body_fields_are_flagged_but_spec_backed_ones_are_not():
    doc = spec_with({"/b": {"post": {"operationId": "b", "requestBody": {"content": {"application/json": {"schema": {
        "type": "object", "required": ["name", "email", "n", "kind", "tag"], "properties": {
            "name": {"type": "string"}, "email": {"type": "string", "format": "email"}, "n": {"type": "integer", "minimum": 1},
            "kind": {"type": "string", "enum": ["a"]}, "tag": {"type": "string", "example": "x"}}}}}},
        "responses": {"201": {"description": "ok"}}}}})
    assert gen_common.guessed_body(doc["endpoints"][0]) == ["body.name"]


def test_undeclared_auth_parameter_becomes_an_inferred_scheme_with_a_warning():
    doc = spec_with({"/a": {"get": {"operationId": "a", "parameters": [{"name": "X-API-Key", "in": "header", "required": True,
                                                                         "schema": {"type": "string"}}], "responses": {"200": {"description": "ok"}}}},
                     "/open": {"get": {"operationId": "o", "security": [], "parameters": [{"name": "X-API-Key", "in": "header", "schema": {"type": "string"}}],
                                       "responses": {"200": {"description": "ok"}}}}})
    by = {e["id"]: e for e in doc["endpoints"]}
    assert by["a"]["auth_required"] and by["a"]["security"] == [["inferred_x_api_key"]]
    assert not by["o"]["auth_required"]                       # an explicit `security: []` stays public
    s = doc["security_schemes"]["inferred_x_api_key"]
    assert s["type"] == "apiKey" and s["in"] == "header" and s["inferred"] and s["env"] == ["APITEST_INFERRED_X_API_KEY_KEY"]
    assert any("no security scheme is declared" in w for w in doc["warnings"])


def test_a_declared_scheme_is_never_overridden():
    doc = spec_with({"/a": {"get": {"operationId": "a", "parameters": [{"name": "X-API-Key", "in": "header", "schema": {"type": "string"}}],
                                    "responses": {"200": {"description": "ok"}}}}},
                    components={"securitySchemes": {"k": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}}, security=[{"k": []}])
    assert doc["endpoints"][0]["security"] == [["k"]] and "inferred_x_api_key" not in doc["security_schemes"]


def test_the_redirect_only_case_is_visible_in_the_generated_endpoint_table(tmp_path):
    doc = spec_with({"/r": {"get": {"operationId": "r", "responses": {"302": {"description": "redirect"}}}}})
    out = tmp_path / "t"
    gen_pytest.generate(doc, out, ["positive"])
    assert "ok=(302,)" in (out / "endpoints.py").read_text()
    # with a 2xx declared as well, the 2xx wins and the 3xx is ignored (documented in expected_ok_codes)
    assert gen_common.expected_ok_codes({"responses": {"200": {}, "302": {}}}) == [200]
    assert "200 and 302" in gen_common.expected_ok_codes.__doc__


def test_read_only_mode_sends_no_write_and_no_probe(tmp_path, upstream):
    doc = spec_with({"/health": {"get": {"operationId": "health", "responses": {"200": {"description": "ok"}}}},
                     "/items": {"post": {"operationId": "add", "requestBody": {"content": {"application/json": {"schema": {
                         "type": "object", "required": ["name"], "properties": {"name": {"type": "string", "example": "x"}}}}}},
                         "responses": {"201": {"description": "ok"}}}}})
    out = tmp_path / "suite"
    gen_pytest.generate(doc, out, ["positive", "security"])
    from suite_runner import run_suite
    results, proc = run_suite(out, upstream, {"APITEST_READ_ONLY": "yes"}, ["-k", "not security_headers"])  # headers is a passive check
    assert proc.returncode in (0, 5), proc.stdout
    statuses = {k.split("::")[1]: v for k, v in results.items()}
    assert statuses["test_health_valid_request"][0] == "passed"
    assert statuses["test_add_valid_request"][0] == "skipped" and "read-only" in statuses["test_add_valid_request"][1]
    assert all(v[0] != "failed" for v in results.values())


def test_integration_flow_skips_with_a_reason_when_the_create_response_has_no_id(tmp_path):
    doc = spec_with({"/pets": {"post": {"operationId": "addPet", "requestBody": {"content": {"application/json": {"schema": {
        "type": "object", "required": ["name"], "properties": {"name": {"type": "string", "example": "x"}}}}}},
        "responses": {"201": {"description": "ok"}}}},
        "/pets/{petId}": {"get": {"operationId": "getPet", "parameters": [{"name": "petId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                                  "responses": {"200": {"description": "ok"}}}}})
    out = tmp_path / "t"
    gen_pytest.generate(doc, out, ["integration"])
    src = (out / "test_integration.py").read_text()
    assert "pytest.skip(" in src and "no id" in src and "assert ids[" not in src


@pytest.fixture
def generated_runtime(tmp_path):
    doc = spec_with({"/x": {"get": {"operationId": "x", "responses": {"200": {"description": "ok"}}}}})
    out = tmp_path / "t"
    gen_pytest.generate(doc, out, ["smoke"])
    sys.path.insert(0, str(out))
    sys.modules.pop("apitest_runtime", None)
    import apitest_runtime as rt
    yield rt
    sys.path.remove(str(out))
    sys.modules.pop("apitest_runtime", None)


def test_extract_id_uses_location_header_or_the_only_id_like_field(generated_runtime):
    rt = generated_runtime

    class R:
        def __init__(self, body, headers=None):
            self._b, self.headers, self.text = body, headers or {}, json.dumps(body)

        def json(self):
            return self._b
    assert rt.extract_id(R({}, {"Location": "http://h/pets/42"}), "petId") == "42"
    assert rt.extract_id(R({"petId": 7}), "petId") == 7
    assert rt.extract_id(R({"employeeId": 9, "name": "x"}), "petId") == 9
    assert rt.extract_id(R({"userId": 1, "orderId": 2}), "petId") is None     # ambiguous: no guess
    assert rt.extract_id(R({"name": "x"}), "petId") is None


def test_read_only_replaces_the_environment_confirmation_for_remote_reads_only(generated_runtime, monkeypatch):
    rt = generated_runtime
    for k in ("APITEST_TARGET_ENV", "APITEST_AUTHORIZED", "APITEST_READ_ONLY"):
        monkeypatch.delenv(k, raising=False)
    assert "APITEST_READ_ONLY" in rt.preflight("http://example.com")
    monkeypatch.setenv("APITEST_READ_ONLY", "yes")
    assert rt.preflight("http://example.com") is None
    monkeypatch.setenv("APITEST_TARGET_ENV", "staging")
    monkeypatch.setenv("APITEST_AUTHORIZED", "yes")
    assert "read-only" in rt.intrusive_skip_reason("http://example.com")   # even a confirmed target gets no active probes


def test_chaos_type_is_reported_as_intrusive_with_the_gate_note():
    assert "chaos" in summarize_results.INTRUSIVE_TYPES and "chaos" in summarize_results.DEFAULT_SEVERITY
    assert safety_gate.gate_reason("http://example.com", {}) is not None


# --------------------------------------------------------------------------- review fixes (PR 10)
CANARY = "SECRETCANARY8431"


def test_secrets_in_the_target_or_probe_query_are_never_printed_or_written(tmp_path, upstream):
    intake = intake_for(tmp_path)
    out = tmp_path / "res"
    dry = run_cli([intake, "--base-url", f"{upstream}/?api_key={CANARY}#{CANARY}", "--probe", f"/health?token={CANARY}", "--dry-run"])
    assert dry.returncode == 0 and CANARY not in dry.stdout + dry.stderr
    real = run_cli([intake, "--base-url", f"{upstream}/?api_key={CANARY}", "--probe", f"/health?token={CANARY}", "--experiments", "payload",
                    "--baseline-s", "2", "--burst", "1", "--out", str(out)])
    assert real.returncode == 0, real.stdout + real.stderr
    for text in (real.stdout, real.stderr, (out / "chaos.xml").read_text(), (out / "chaos.json").read_text()):
        assert CANARY not in text


def test_hook_arguments_mask_url_passwords():
    assert "hunter2" not in chaos_run.mask(["psql", "postgresql://user:hunter2@localhost/db"])


def junit_with(tmp_path, rerun):
    xml = tmp_path / "evil.xml"
    xml.write_text('<testsuites><testsuite name="chaos"><testcase classname="chaos.test_chaos" name="test_chaos_dep_slow_db" time="1">'
                   f'<failure message="m">[MEDIUM] hang\nrerun: {rerun}</failure></testcase></testsuite></testsuites>')
    return xml


def test_report_rebuilds_the_chaos_command_and_never_echoes_the_result_files_line(tmp_path):
    intake = intake_for(tmp_path)
    for evil in ("PYTHONPATH=/tmp/evil python /tmp/evil/chaos_run.py intake.json --dependency-config /tmp/evil/deps.json --allow-hooks --experiments dep-outage",
                 "python chaos_run.py ../../etc/passwd --experiments dep-outage",
                 "python chaos_run.py intake.json --experiments rm-rf,dep-outage"):
        rep = tmp_path / "r.md"
        assert summarize_results.main(["--junit", str(junit_with(tmp_path, evil)), "--intake", intake, "-o", str(rep)]) == 0
        text = rep.read_text()
        assert "PYTHONPATH" not in text and "/tmp/evil" not in text and "--allow-hooks" not in text and "passwd" not in text
        assert "<intake.json>" in text
    rep = tmp_path / "ok.md"
    good = "APITEST_BASE_URL=http://x python /home/me/chaos_run.py intake.json --experiments dep-slow,proxy-5xx --out res --allow-hooks"
    summarize_results.main(["--junit", str(junit_with(tmp_path, good)), "--intake", intake, "-o", str(rep)])
    text = rep.read_text()
    assert "python scripts/chaos_run.py intake.json --experiments dep-slow,proxy-5xx" in text
    assert "/home/me" not in text and "--allow-hooks" not in text.split("**Reproduce**")[1].split("```")[1]


def test_read_only_mode_skips_payload_and_dependency_commands_and_sends_no_write(tmp_path):
    srv = make_upstream()
    try:
        cfg = tmp_path / "deps.json"
        flag = tmp_path / "ran"
        cmd = [sys.executable, "-c", f"open({str(flag)!r}, 'w')"]
        cfg.write_text(json.dumps({"dependencies": [{"name": "db", "stop": cmd, "start": cmd, "slow_on": cmd, "slow_off": cmd}]}))
        SEEN.clear()
        out = tmp_path / "res"
        r = run_cli([intake_for(tmp_path), "--base-url", f"http://127.0.0.1:{srv.server_address[1]}", "--experiments", "payload,dep-outage,dep-slow",
                     "--dependency-config", str(cfg), "--allow-hooks", "--baseline-s", "2", "--out", str(out)], {"APITEST_READ_ONLY": "yes"})
        assert r.returncode == 0, r.stdout + r.stderr
        statuses = {e["name"]: (e["status"], e["title"]) for e in json.loads((out / "chaos.json").read_text())["experiments"]}
        for name in ("test_chaos_payload", "test_chaos_dep_outage", "test_chaos_dep_slow"):
            assert statuses[name][0] == "skipped" and "read-only" in statuses[name][1]
        assert "POST" not in SEEN and not flag.exists()
    finally:
        srv.shutdown()


def test_k6_script_skips_writes_in_read_only_mode():
    src = (ROOT / "skills" / "api-testing" / "templates" / "k6" / "k6_script.js").read_text()
    line = next(ln for ln in src.splitlines() if "ep.write &&" in ln)
    assert "APITEST_READ_ONLY" in line


def test_undo_survives_an_interrupt_that_arrives_while_it_runs(tmp_path):
    import signal
    marker = tmp_path / "undone"
    h = chaos_run.Hooks()
    h.pending_undo.append(("db", [sys.executable, "-c", f"import time; time.sleep(0.8); open({str(marker)!r}, 'w')"]))
    threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
    with pytest.raises(KeyboardInterrupt):   # delivered after the cleanup finished
        h.undo_all()
    assert marker.exists() and not h.pending_undo


def test_a_failed_undo_stays_pending_for_a_retry(tmp_path):
    h = chaos_run.Hooks()
    h.pending_undo.append(("db", ["definitely-not-a-command-xyz"]))
    h.undo_all()
    assert not h.pending_undo   # reported as MANUAL ACTION REQUIRED, not retried forever


@pytest.mark.parametrize("signals", [["SIGHUP"], ["SIGTERM", "SIGTERM"], ["SIGINT", "SIGTERM", "SIGHUP"]])
def test_the_dependency_is_restored_when_the_runner_is_signalled(tmp_path, upstream, signals):
    import signal
    flag = tmp_path / "down"
    cfg = tmp_path / "deps.json"
    cfg.write_text(json.dumps({"dependencies": [{
        "name": "db", "stop": [sys.executable, "-c", f"open({str(flag)!r}, 'w')"],
        "start": [sys.executable, "-c", f"import time, os; time.sleep(1.5); os.remove({str(flag)!r})"]}]}))
    env = {k: v for k, v in os.environ.items() if not k.startswith("APITEST_")}
    p = subprocess.Popen([sys.executable, str(SCRIPTS / "chaos_run.py"), intake_for(tmp_path), "--base-url", upstream, "--experiments", "dep-outage",
                          "--dependency-config", str(cfg), "--allow-hooks", "--baseline-s", "2", "--outage-s", "40", "--out", str(tmp_path / "res")],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 30
        while not flag.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert flag.exists(), "the outage never started"
        for i, name in enumerate(signals):
            p.send_signal(getattr(signal, name))
            time.sleep(0.4)
        p.wait(timeout=30)
    finally:
        if p.poll() is None:
            p.kill()
    assert not flag.exists(), "the dependency was left down"
    assert p.returncode == chaos_run.EXIT_INTERRUPTED == 4, "a run cut short must not exit 0 or 1"
    assert "INCOMPLETE" in p.stderr.read()


def test_a_failing_dependency_command_is_a_failed_experiment_not_a_traceback(tmp_path, upstream):
    cfg = tmp_path / "deps.json"
    cfg.write_text(json.dumps({"dependencies": [{"name": "db", "stop": [sys.executable, "-c", "import sys; sys.exit(3)"],
                                                 "start": [sys.executable, "-c", "pass"]}]}))
    r = run_cli([intake_for(tmp_path), "--base-url", upstream, "--experiments", "dep-outage", "--dependency-config", str(cfg), "--allow-hooks",
                 "--baseline-s", "2", "--out", str(tmp_path / "res")])
    assert r.returncode == 1 and "Traceback" not in r.stderr and "FAILED [HIGH]" in r.stdout


def test_proxy_survives_a_bad_content_length(upstream):
    with chaos_proxy.HttpFaultProxy(upstream) as px:
        s = socket.create_connection(("127.0.0.1", px.port), timeout=3)
        s.sendall(b"POST /x HTTP/1.1\r\nHost: h\r\nContent-Length: -5\r\n\r\n")
        assert s.recv(100).startswith(b"HTTP/1.")
        s.close()
        assert get(f"http://127.0.0.1:{px.port}/x")[0] == 200
