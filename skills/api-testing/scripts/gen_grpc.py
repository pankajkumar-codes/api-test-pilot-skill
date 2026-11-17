"""Generator for gRPC suites (unary methods only): smoke, positive, negative, edge, contract, security.

Tests call the service through grpcurl (see templates/pytest/grpc_runtime.py). The suite carries its own copy of the proto files
(`protos/`), a method table (`methods.py`) and the message definitions the contract tests compare answers with.
"""
from __future__ import annotations

import re
from pathlib import Path

import _common
from gen_common import Expr, Module, ident, py

GRPC_TYPES = ("smoke", "positive", "negative", "edge", "contract", "security")
READ_RE = re.compile(r"^(get|list|read|find|search|fetch|describe|check|query|lookup|show|watch|stat|count|exists|has|is)", re.I)
DESTRUCTIVE_RE = re.compile(r"^(delete|remove|destroy|drop|purge|reset|clear|revoke|cancel|terminate|wipe)", re.I)
ID_RE = re.compile(r"(^|_)id$|^id$|^uuid$", re.I)
INJECTION = ["' OR '1'='1", "1; SELECT 1", '{"$ne": null}', "$(id)", "../../../../etc/passwd", "<script>apitest</script>", "{{7*7}}"]
MAX_DEPTH = 3
MAX_EDGE_FIELDS = 4
INT32 = (2 ** 31 - 1, -2 ** 31)
INT64 = ("9223372036854775807", "-9223372036854775808")


class Intake:
    def __init__(self, doc: dict):
        g = doc["grpc"]
        self.g = g
        self.messages: dict[str, dict] = g["messages"]
        self.enums: dict[str, list[str]] = g["enums"]
        self.methods = [m for m in g["methods"] if not m["client_streaming"] and not m["server_streaming"]]

    # ---- sample values
    def scalar(self, ftype: str, name: str):
        if ftype == "string":
            return Expr(f"gr.EnvId({name!r})") if ID_RE.search(name) else f"sample-{name}"
        if ftype == "bool":
            return True
        if ftype == "bytes":
            return "c2FtcGxl"
        if ftype in ("float", "double"):
            return 1.5
        if ftype in ("int64", "sint64", "sfixed64", "uint64", "fixed64"):
            return "1"
        return 1

    def value(self, f: dict, depth: int):
        kind, ftype = f["kind"], f["type"]
        if kind == "scalar":
            v = self.scalar(ftype, f["name"])
        elif kind == "enum":
            vals = self.enums.get(ftype) or [""]
            v = next((x for x in vals if not x.upper().endswith("UNSPECIFIED")), vals[0])
        elif kind == "timestamp":
            v = "2024-01-01T00:00:00Z"
        elif kind == "duration":
            v = "1s"
        elif kind in ("string", "bool", "bytes", "int32", "int64", "uint32", "uint64", "float", "double"):
            v = self.scalar(kind, f["name"])      # wrapper types are plain values in JSON
        elif kind == "message":
            v = self.message(ftype, depth + 1)
        elif kind in ("struct", "empty"):
            v = {}
        elif kind in ("value", "any", "list", "fieldmask"):
            v = None
        else:
            v = None
        return None if v is None else [v] if f["repeated"] else v

    def message(self, name: str, depth: int = 0) -> dict | None:
        if depth > MAX_DEPTH or name not in self.messages:
            return {} if depth <= MAX_DEPTH else None
        out: dict = {}
        seen_oneofs: set[str] = set()
        for f in self.messages[name]["fields"]:
            if f.get("oneof"):
                if f["oneof"] in seen_oneofs:
                    continue
                seen_oneofs.add(f["oneof"])
            if f["kind"] == "map":
                out[f["json_name"]] = {"key": self._map_value(f, depth)}
                continue
            v = self.value(f, depth)
            if v is not None:
                out[f["json_name"]] = v
        return out

    def _map_value(self, f: dict, depth: int):
        vf = {"name": f["name"], "kind": f["value_kind"], "type": f["type"], "repeated": False}
        return self.value(vf, depth)

    def template(self, m: dict) -> dict:
        return self.message(m["request"]) or {}


def _ids(message_template) -> bool:
    return "EnvId" in py(message_template)


def methods_source(it: Intake) -> str:
    rows = []
    for m in it.methods:
        reads = bool(READ_RE.match(m["name"]))
        destructive = bool(DESTRUCTIVE_RE.match(m["name"]))
        rows.append("    gr.Method(" + ", ".join([
            f"id={m['id']!r}", f"name={m['name']!r}", f"service={m['service']!r}", f"request={m['request']!r}", f"response={m['response']!r}",
            f"template={py(it.template(m))}", f"destructive={destructive!r}", f"writes={(not reads)!r}"]) + ")")
    msgs = {n: {"fields": [{k: f[k] for k in ("name", "json_name", "kind", "type", "repeated")} for f in m["fields"]]} for n, m in it.messages.items()}
    return ('"""Method table generated from the proto intake. Contains no secrets: ids come from env vars."""\n'
            "import grpc_runtime as gr\n\nMETHODS = {m.id: m for m in [\n" + ",\n".join(rows) + ",\n]}\n"
            f"\n# what the proto says each message looks like, for the contract tests\nMESSAGES = {py(msgs)}\nENUMS = {py(it.enums)}\n"
            f"\nMESSAGE_OF = {py({m['id']: m['request'] for m in it.methods})}\n")


def _destructive_pre(m: dict) -> str:
    return "    gr.require_destructive()\n" if DESTRUCTIVE_RE.match(m["name"]) else ""


def _writes(m: dict) -> bool:
    return not READ_RE.match(m["name"])


def smoke(it: Intake, mod: Module) -> None:
    for m in it.methods:
        if _writes(m):
            continue
        mod.add(f"@pytest.mark.smoke\ndef {mod.unique('test_smoke_' + m['name'])}(grpc):\n    m = METHODS[{m['id']!r}]\n"
                "    res = grpc.call(m)\n    gr.expect_graceful(res, \"reachability\")\n")


def positive(it: Intake, mod: Module) -> None:
    for m in it.methods:
        mod.add(f"def {mod.unique('test_' + m['name'] + '_succeeds')}(grpc):\n    m = METHODS[{m['id']!r}]\n{_destructive_pre(m)}"
                "    res = grpc.call(m)\n    gr.expect_ok(res)\n")


def _string_fields(it: Intake, m: dict) -> list[str]:
    msg = it.messages.get(m["request"], {})
    return [f["json_name"] for f in msg.get("fields", []) if f["kind"] == "scalar" and f["type"] == "string" and not f["repeated"] and not f.get("oneof")]


def negative(it: Intake, mod: Module) -> None:
    mod.add("def test_requires_authentication(grpc):\n"
            "    gr.require_flag(\"APITEST_GRPC_REQUIRES_AUTH\", \"confirm the service must reject calls without a token\")\n"
            "    for m in READ_METHODS:\n        res = grpc.call(METHODS[m], auth=\"none\")\n"
            "        gr.expect_code(res, (\"UNAUTHENTICATED\", \"PERMISSION_DENIED\"), f\"{m} without a token\")\n")
    mod.add("def test_invalid_token_is_rejected(grpc):\n"
            "    gr.require_flag(\"APITEST_GRPC_REQUIRES_AUTH\", \"confirm the service must reject calls without a token\")\n"
            "    for m in READ_METHODS:\n        res = grpc.call(METHODS[m], auth=\"invalid\")\n"
            "        gr.expect_code(res, (\"UNAUTHENTICATED\", \"PERMISSION_DENIED\"), f\"{m} with an invalid token\")\n")
    for m in it.methods:
        if _writes(m):
            continue
        for fld in it.messages.get(m["request"], {}).get("fields", []):
            if fld["kind"] == "scalar" and fld["type"] == "string" and ID_RE.search(fld["name"]) and not fld["repeated"]:
                mod.add(f"def {mod.unique('test_' + m['name'] + '_unknown_id_is_rejected')}(grpc):\n    m = METHODS[{m['id']!r}]\n"
                        f"    res = grpc.call(m, override={{{fld['json_name']!r}: \"apitest-does-not-exist\"}})\n    gr.expect_rejected(res, \"unknown id\")\n")
                break


def edge(it: Intake, mod: Module) -> None:
    for m in it.methods:
        if DESTRUCTIVE_RE.match(m["name"]):
            continue
        msg = it.messages.get(m["request"], {})
        cases: list[tuple] = []
        for f in [f for f in msg.get("fields", []) if not f.get("oneof") and f["kind"] != "map"][:MAX_EDGE_FIELDS]:
            key = f["json_name"]
            if f["repeated"]:
                cases += [(f"{key}_empty_list", {key: []}), (f"{key}_200_items", {key: [it.value({**f, 'repeated': False}, 0) or "x"] * 200})]
            elif f["kind"] == "scalar" and f["type"] == "string" and not ID_RE.search(f["name"]):
                cases += [(f"{key}_empty", {key: ""}), (f"{key}_unicode", {key: "héllo € \U0001F600 ‮"}),
                          (f"{key}_control_chars", {key: "a\x00b\x1f\r\n"}), (f"{key}_100k_chars", {key: Expr('"A" * 100000')})]
            elif f["kind"] == "scalar" and f["type"] in ("int32", "sint32", "sfixed32", "uint32", "fixed32"):
                cases += [(f"{key}_zero", {key: 0}), (f"{key}_negative", {key: -1}), (f"{key}_int32_max", {key: INT32[0]}), (f"{key}_int32_min", {key: INT32[1]})]
            elif f["kind"] == "scalar" and f["type"] in ("int64", "sint64", "sfixed64", "uint64", "fixed64"):
                cases += [(f"{key}_zero", {key: "0"}), (f"{key}_int64_max", {key: INT64[0]}), (f"{key}_int64_min", {key: INT64[1]})]
            elif f["kind"] == "enum":
                cases += [(f"{key}_unknown_number", {key: 99}), (f"{key}_zero", {key: 0})]
            elif f["kind"] == "scalar" and f["type"] == "bool":
                cases += [(f"{key}_false", {key: False})]
            elif f["kind"] == "message":
                cases += [(f"{key}_empty_message", {key: {}})]
        cases.append(("empty_message", Expr("None")))
        if not cases:
            continue
        table = mod.unique("EDGE_" + ident(m["id"]))
        rows = ",\n".join(f"    ({c[0]!r}, {py(c[1])})" for c in cases)
        mod.add(f"{table} = [\n{rows},\n]\n", is_test=False)
        mod.add(f'@pytest.mark.parametrize("label,change", {table}, ids=[c[0] for c in {table}])\n'
                f"def {mod.unique('test_' + m['name'] + '_edge')}(grpc, label, change):\n    m = METHODS[{m['id']!r}]\n"
                "    res = grpc.call(m, message={}) if change is None else grpc.call(m, override=change)\n"
                "    gr.expect_graceful(res, label)\n")


def contract(it: Intake, mod: Module) -> None:
    for m in it.methods:
        if _writes(m) or m["response_kind"] != "message":
            continue
        mod.add(f"def {mod.unique('test_contract_' + m['name'] + '_matches_proto')}(grpc):\n    m = METHODS[{m['id']!r}]\n"
                f"    res = grpc.call(m)\n    gr.expect_shape(res, m.response, ENUMS, MESSAGES)\n")


def security(it: Intake, mod: Module) -> list[str]:
    notes: list[str] = []
    I = "@pytest.mark.intrusive\n"
    mod.add(I + "def test_reflection_disabled(grpc):\n"
            "    if gr.env_true(\"APITEST_ALLOW_REFLECTION\"):\n        pytest.skip(\"reflection is intentionally enabled here (APITEST_ALLOW_REFLECTION)\")\n"
            "    res = grpc.list_services()\n"
            "    if res.ok:\n        gr.gfail(res, \"server reflection is enabled: it lists every service to anyone who can connect (set APITEST_ALLOW_REFLECTION=yes if this environment is meant to be open)\", \"MEDIUM\", \"API8:2023 security misconfiguration\")\n")
    reads = [m for m in it.methods if not _writes(m)]
    ids = next((m for m in reads if any(ID_RE.search(f["name"]) for f in it.messages.get(m["request"], {}).get("fields", []))), None)
    probe = ids or (reads[0] if reads else None)
    if probe:
        fld = next((f for f in it.messages.get(probe["request"], {}).get("fields", []) if f["kind"] == "scalar" and f["type"] == "string"), None)
        override = f"{{{fld['json_name']!r}: \"apitest-not-an-id\"}}" if fld else "{}"
        mod.add(I + f"def test_errors_do_not_leak_internals(grpc):\n    m = METHODS[{probe['id']!r}]\n"
                f"    res = grpc.call(m, override={override})\n    gr.expect_no_leak(res)\n")
    for m in it.methods:
        if DESTRUCTIVE_RE.match(m["name"]):
            continue
        strs = _string_fields(it, m)[:3]
        cases = [(f"{s}[{i}]", {s: pl}) for s in strs for i, pl in enumerate(INJECTION)]
        if not cases:
            continue
        table = mod.unique("INJECT_" + ident(m["id"]))
        rows = ",\n".join(f"    ({c[0]!r}, {py(c[1])})" for c in cases)
        mod.add(f"{table} = [\n{rows},\n]\n", is_test=False)
        mod.add(I + f'@pytest.mark.parametrize("label,change", {table}, ids=[c[0] for c in {table}])\n'
                f"def {mod.unique('test_injection_' + m['name'])}(grpc, label, change):\n    m = METHODS[{m['id']!r}]\n"
                "    res = grpc.call(m, override=change)\n    gr.expect_injection_safe(res, label)\n")
    return notes


MODULES = {
    "smoke": ("test_grpc_smoke.py", "gRPC smoke tests", smoke),
    "positive": ("test_grpc_positive.py", "gRPC positive tests", positive),
    "negative": ("test_grpc_negative.py", "gRPC negative tests", negative),
    "edge": ("test_grpc_edge.py", "gRPC edge-case tests", edge),
    "contract": ("test_grpc_contract.py", "gRPC contract tests (the answer matches the proto)", contract),
    "security": ("test_grpc_security.py", "gRPC security tests (OWASP API Top 10)", security),
}


def generate(doc: dict, out: Path, types: list[str], base_url_override: str | None = None) -> dict:
    import gen_pytest as gp
    chosen = list(GRPC_TYPES) if types == ["all"] else types
    unsupported = [t for t in chosen if t not in GRPC_TYPES]
    if unsupported:
        raise _common.IntakeError(f"gRPC suites support: {', '.join(GRPC_TYPES)}, all. Not available for gRPC yet: {', '.join(unsupported)}")
    it = Intake(doc)
    if not it.methods:
        raise _common.IntakeError("The proto file has no unary method to test (streaming methods are not supported).")
    base_url = gp.default_base_url(doc, base_url_override)
    out.mkdir(parents=True, exist_ok=True)
    rerun = gp.rerun_command(out, base_url or "http://localhost:50051", chosen)
    proto = doc["grpc"]["proto_file"]
    fixture = f'''

@pytest.fixture(scope="session")
def grpc(base_url):
    import grpc_runtime as gr
    here = os.path.dirname(os.path.abspath(__file__))
    return gr.Grpc(base_url, SCHEMES, {proto!r}, os.path.join(here, "protos"))
'''
    files = {
        "apitest_runtime.py": gp.runtime_source(),
        "grpc_runtime.py": (gp.SKILL_DIR / "templates" / "pytest" / "grpc_runtime.py").read_text(encoding="utf-8"),
        "conftest.py": gp.conftest_source(doc, base_url, fixture),
        "methods.py": methods_source(it),
    }
    notes: list[str] = []
    counts: dict[str, int] = {}
    reads = [m["id"] for m in it.methods if not _writes(m)]
    for t in chosen:
        fname, title, fn = MODULES[t]
        mod = Module(title, t, rerun)
        extra = fn(it, mod)
        if isinstance(extra, list):
            notes += extra
        if mod.tests:
            head = "import grpc_runtime as gr  # noqa: F401\nfrom methods import ENUMS, MESSAGES, METHODS  # noqa: F401\n"
            if t == "negative":
                head += f"\nREAD_METHODS = {py(reads)}\n"
            files[fname] = mod.render(head).replace("from endpoints import EP\n", "")
            counts[t] = mod.tests
    for name, src in files.items():
        gp.check_compiles(name, src)
        (out / name).write_text(src, encoding="utf-8")
    protos = out / "protos"
    for name, text in doc["grpc"].get("sources", {}).items():
        target = (protos / name).resolve()
        if protos.resolve() not in target.parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    skipped = [m["id"] for m in doc["grpc"]["methods"] if m["client_streaming"] or m["server_streaming"]]
    if skipped:
        notes.append("grpc: streaming methods are not tested: " + ", ".join(skipped))
    notes.append("grpc: the tests run grpcurl (https://github.com/fullstorydev/grpcurl); target = APITEST_BASE_URL, http://host:port for plaintext or https://host:port for TLS")
    return {"out": str(out), "files": sorted(files) + ["protos/" + n for n in doc["grpc"].get("sources", {})], "counts": counts, "notes": notes,
            "rerun": rerun, "env": gp.env_names(doc), "base_url": base_url}
