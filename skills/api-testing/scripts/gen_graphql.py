"""Generator for GraphQL pytest suites (see references/graphql.md).

Input: intake JSON produced by parse_graphql.py. Output: conftest.py, apitest_runtime.py, gql_runtime.py,
operations.py and test_graphql_<type>.py files. Reuses the REST runtime (auth from env vars, redaction,
curl reproductions, safety gate). Active probes (introspection, depth/alias/batch abuse, field-level authz,
BOLA, injection) are marked `intrusive` and need the same gate as REST security tests.
"""
from __future__ import annotations

import re
from pathlib import Path

import _common
from gen_common import Expr, Module, ident, py, var_type

GQL_TYPES = ("smoke", "positive", "negative", "edge", "security", "contract", "integration", "data-validation", "resilience", "fuzz")
OPT_IN = {"fuzz"}  # intrusive: chosen by name, never part of `all`
DESTRUCTIVE_RE = re.compile(r"^(delete|remove|destroy|drop|purge|reset|clear|revoke|cancel|terminate|wipe)", re.I)
PAGING_ARGS = {"first", "last", "limit", "count", "take", "size", "offset", "skip", "page", "pagesize", "per_page"}
INJECTION = ["' OR '1'='1", "1; SELECT 1", '{"$ne": null}', "$(id)", "../../../../etc/passwd", "<script>apitest</script>", "{{7*7}}"]
WRONG_TYPE = {"Int": "not-an-int", "Float": "not-a-float", "String": {"nested": True}, "Boolean": "maybe",
              "ID": {"a": 1}, "ENUM": "NOT_A_VALUE", "INPUT_OBJECT": "not-an-object"}
DEPTH, ALIASES, BATCH = 15, 50, 5


def _camel(s: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in re.split(r"[^A-Za-z0-9]+", s) if w)


# --------------------------------------------------------------------------- schema helpers
class Schema:
    def __init__(self, doc: dict):
        g = doc["graphql"]
        self.ops: list[dict] = g["operations"]
        self.types: dict[str, dict] = g["types"]
        self.path = doc["endpoints"][0]["path"]

    def kind(self, name: str) -> str:
        return "SCALAR" if name in _common_scalars() else self.types.get(name, {}).get("kind", "SCALAR")

    def sample(self, named: str, is_list: bool, field_name: str = "", depth: int = 0):
        kind = self.kind(named)
        if kind == "SCALAR":
            v = {"Int": 1, "Float": 1.5, "Boolean": True, "ID": "1"}.get(named)
            if v is None:
                v = _common_placeholder(field_name) if named == "String" else f"sample-{field_name or named}"
        elif kind == "ENUM":
            vals = self.types[named].get("values") or [""]
            v = vals[0]
        elif kind == "INPUT_OBJECT" and depth < 4:
            v = {}
            for f in self.types[named].get("fields", []):
                if f.get("required"):
                    v[f["name"]] = self.sample(f["named"], f.get("list", False), f["name"], depth + 1)
        else:
            v = None
        return [v] if is_list and v is not None else v

    def selection(self, op: dict) -> str:
        if op["return_kind"] in ("OBJECT", "INTERFACE"):
            return " { " + " ".join(op["selection"] or ["__typename"]) + " }"
        if op["return_kind"] == "UNION":
            return " { __typename }"
        return ""

    def query_text(self, op: dict, selection: str | None = None) -> str:
        decl = "(" + ", ".join(f"${a['name']}: {var_type(a)}" for a in op["args"]) + ")" if op["args"] else ""
        call = "(" + ", ".join(f"{a['name']}: ${a['name']}" for a in op["args"]) + ")" if op["args"] else ""
        sel = self.selection(op) if selection is None else selection
        return f"{op['kind']} Apitest{_camel(op['name'])}{decl} {{ {op['name']}{call}{sel} }}"

    def baseline_vars(self, op: dict) -> dict:
        out = {}
        for a in op["args"]:
            if not a["required"]:
                continue
            if a["named"] == "ID":
                out[a["name"]] = Expr(f"gr.EnvArg({a['name']!r})")
            else:
                v = self.sample(a["named"], a["list"], a["name"])
                if v is not None:
                    out[a["name"]] = v
        return out

    def object_edges(self, type_name: str):
        for f in self.types.get(type_name, {}).get("fields", []):
            if self.kind(f["named"]) in ("OBJECT", "INTERFACE") and not any(a.get("required") for a in f.get("args", [])):
                yield f

    def no_arg_queries(self) -> list[dict]:
        return [o for o in self.ops if o["kind"] == "query" and not any(a["required"] for a in o["args"])
                and o["return_kind"] in ("OBJECT", "INTERFACE")]

    def find_cycle(self):
        """(op, prefix_fields, cycle_fields) reaching a recursive type from a no-required-arg query, or None."""
        for op in self.no_arg_queries():
            stack: list[tuple[str, str]] = []

            def dfs(t: str, seen: dict[str, int]):
                for f in self.object_edges(t):
                    n = f["named"]
                    if n in seen:
                        i = seen[n]
                        return [x[1] for x in stack[:i]], [x[1] for x in stack[i:]] + [f["name"]]
                    if len(stack) > 6:
                        continue
                    stack.append((n, f["name"]))
                    seen[n] = len(stack)
                    got = dfs(n, seen)
                    stack.pop()
                    del seen[n]
                    if got:
                        return got
                return None
            res = dfs(op["return_type"], {op["return_type"]: 0})
            if res:
                return op, res[0], res[1]
        return None

    def sensitive_paths(self) -> list[tuple[str, list[str], str]]:
        """(label, data path, query) for sensitive scalar fields reachable (<=3 hops) from a no-arg query."""
        out = []
        seen_labels = set()
        for op in self.no_arg_queries():
            frontier = [(op["return_type"], [op["name"]])]
            for _ in range(3):
                nxt = []
                for tname, path in frontier:
                    for f in self.types.get(tname, {}).get("fields", []):
                        if f.get("sensitive") and f["kind"] in ("SCALAR", "ENUM"):
                            label = f"{tname}.{f['name']}"
                            if label not in seen_labels and len(out) < 6:
                                seen_labels.add(label)
                                full = path + [f["name"]]
                                out.append((label, full, _nested_query(full)))
                    for f in self.object_edges(tname):
                        if len(path) < 3:
                            nxt.append((f["named"], path + [f["name"]]))
                frontier = nxt
        return out


def _common_scalars() -> set[str]:
    return {"Int", "Float", "String", "Boolean", "ID"}


def _common_placeholder(name: str) -> str:
    return "REPLACE_ME" if _common.is_secret_name(name) else f"sample-{name}" if name else "sample"


def _nested_query(path: list[str]) -> str:
    inner = path[-1]
    for key in reversed(path[:-1]):
        inner = f"{key} {{ {inner} }}"
    return "query ApitestFieldAuth { " + inner + " }"


# --------------------------------------------------------------------------- generation
def operations_source(sc: Schema) -> str:
    rows = []
    for op in sc.ops:
        if op["kind"] == "subscription":
            continue
        minimal = sc.query_text(op, " { __typename }") if op["kind"] == "query" and op["return_kind"] in ("OBJECT", "INTERFACE", "UNION") else None
        parts = [f"id={op['id']!r}", f"kind={op['kind']!r}", f"name={op['name']!r}", f"query={sc.query_text(op)!r}",
                 f"variables={py(sc.baseline_vars(op))}", f"args={py([{k: a[k] for k in ('name', 'type', 'named', 'kind', 'required', 'list')} for a in op['args']])}",
                 f"returns={op['returns']!r}", f"return_kind={op['return_kind']!r}",
                 f"return_non_null={op['return_non_null']!r}", f"return_list={op['return_list']!r}",
                 f"return_type={op['return_type']!r}", f"selection={op['selection']!r}"]
        if minimal:
            parts.append(f"query_minimal={minimal!r}")
        rows.append("    gr.Operation(" + ",\n                 ".join(parts) + ")")
    types = {n: {f["name"]: f["type"] for f in t.get("fields", [])}
             for n, t in sc.types.items() if t.get("kind") in ("OBJECT", "INTERFACE")}
    enums = {n: list(t.get("values") or []) for n, t in sc.types.items() if t.get("kind") == "ENUM"}
    kinds = {n: t.get("kind") for n, t in sc.types.items()}
    return ('"""Operation table generated from the GraphQL intake. Contains no secrets: ids come from env vars."""\n'
            "import gql_runtime as gr\n\nOPS = {o.id: o for o in [\n" + ",\n".join(rows) + ",\n]}\n"
            f"\n# what the schema says each field looks like, for the contract tests\nTYPES = {py(types)}\nENUMS = {py(enums)}\nKINDS = {py(kinds)}\n")


def smoke(sc: Schema, mod: Module) -> None:
    for op in sc.ops:
        if op["kind"] == "query" and not any(a["required"] for a in op["args"]):
            mod.add(f"@pytest.mark.smoke\ndef {mod.unique('test_smoke_' + op['name'])}(gql):\n    op = OPS[{op['id']!r}]\n"
                    f"    resp = gql.run(op)\n    gr.expect_data(resp, {op['name']!r})\n")
    mod.add("@pytest.mark.smoke\ndef test_smoke_typename(gql):\n    resp = gql.post(\"query { __typename }\")\n"
            "    gr.expect_data(resp, \"__typename\", nonnull=True)\n")


def positive(sc: Schema, mod: Module) -> None:
    for op in sc.ops:
        if op["kind"] == "subscription":
            continue
        pre = "    gr.require_destructive()\n" if op["kind"] == "mutation" and DESTRUCTIVE_RE.match(op["name"]) else ""
        name = mod.unique(f"test_{op['kind']}_{op['name']}_succeeds")
        nn = ", nonnull=True" if op["return_non_null"] else ""
        mod.add(f"def {name}(gql):\n    op = OPS[{op['id']!r}]\n{pre}    resp = gql.run(op)\n"
                f"    value = gr.expect_data(resp, {op['name']!r}{nn})\n"
                + ("    assert value is None or isinstance(value, list), \"list type must be returned as a JSON array\"\n" if op["return_list"] else ""))
        if op["kind"] == "query" and op["return_kind"] in ("OBJECT", "INTERFACE", "UNION"):
            mname = mod.unique("test_query_" + op["name"] + "_minimal_selection")
            mod.add(f"def {mname}(gql):\n"
                    f"    op = OPS[{op['id']!r}]\n    resp = gql.run(op, query=op.query_minimal)\n"
                    f"    gr.expect_data(resp, {op['name']!r})\n")


def negative(sc: Schema, mod: Module) -> None:
    mod.add("def test_syntax_error_is_reported(gql):\n    resp = gql.post(\"query { \")\n    gr.expect_graphql_error(resp, \"syntax error\")\n")
    mod.add("def test_unknown_root_field_is_reported(gql):\n    resp = gql.post(\"query { apitestUnknownField }\")\n"
            "    gr.expect_graphql_error(resp, \"unknown field\")\n")
    mod.add("def test_unknown_operation_name_is_reported(gql):\n"
            "    resp = gql.post(\"query A { __typename } query B { __typename }\", operation_name=\"DoesNotExist\")\n"
            "    gr.expect_graphql_error(resp, \"unknown operationName\")\n")
    mod.add("def test_non_json_body_is_rejected(gql):\n    resp = gql.post(\"\", raw=\"not json\")\n"
            "    gr.expect_graphql_error(resp, \"non-JSON body\")\n")
    obj = next((o for o in sc.ops if o["kind"] == "query" and o["return_kind"] in ("OBJECT", "INTERFACE")), None)
    if obj:
        q = sc.query_text(obj, " { apitestUnknownSubfield }")
        mod.add(f"def test_unknown_subfield_is_reported(gql):\n    op = OPS[{obj['id']!r}]\n"
                f"    resp = gql.run(op, query={q!r})\n    gr.expect_graphql_error(resp, \"unknown subfield\")\n")
    mod.add("def test_requires_authentication(gql):\n"
            "    gr.require_flag(\"APITEST_GQL_REQUIRES_AUTH\", \"this API is expected to reject anonymous callers\")\n"
            "    op = next(iter(OPS.values()))\n    resp = gql.run(op, auth=\"none\")\n"
            "    gr.expect_graphql_error(resp, \"anonymous request\")\n")
    for op in sc.ops:
        if op["kind"] == "subscription":
            continue
        cases: list[tuple] = []
        for a in op["args"]:
            if a["required"]:
                cases.append((f"missing_{a['name']}", {"variables": {k: v for k, v in sc.baseline_vars(op).items() if k != a["name"]}}))
        for a in op["args"][:3]:
            if a["list"]:
                continue
            key = "ENUM" if a["kind"] == "ENUM" else "INPUT_OBJECT" if a["kind"] == "INPUT_OBJECT" else a["named"]
            if key in WRONG_TYPE:
                cases.append((f"{a['name']}_wrong_type", {"override": {a["name"]: WRONG_TYPE[key]}}))
        if not cases:
            continue
        table = mod.unique("INVALID_" + ident(op["id"]))
        rows = ",\n".join(f"    ({c[0]!r}, {_kw(c[1])})" for c in cases)
        mod.add(f"{table} = [\n{rows},\n]\n", is_test=False)
        pre = "    gr.require_destructive()\n" if op["kind"] == "mutation" and DESTRUCTIVE_RE.match(op["name"]) else ""
        mod.add(f'@pytest.mark.parametrize("label,kw", {table}, ids=[c[0] for c in {table}])\n'
                f"def {mod.unique('test_' + op['kind'] + '_' + op['name'] + '_rejects_invalid_input')}(gql, label, kw):\n"
                f"    op = OPS[{op['id']!r}]\n{pre}    resp = gql.run(op, **kw)\n    gr.expect_graphql_error(resp, label)\n")


def _kw(kw: dict) -> str:
    return "{" + ", ".join(f"{k!r}: {py(v)}" for k, v in kw.items()) + "}"


def edge(sc: Schema, mod: Module) -> None:
    for op in sc.ops:
        if op["kind"] != "query":
            continue
        cases: list[tuple] = []
        for a in op["args"][:4]:
            n, named = a["name"], a["named"]
            if a["list"]:
                cases.append((f"{n}_empty_list", {"override": {n: []}}))
            elif named == "String":
                cases += [(f"{n}_unicode", {"override": {n: "Zoë 🐶 日本語"}}), (f"{n}_empty", {"override": {n: ""}}),
                          (f"{n}_special_chars", {"override": {n: "!@#$%^&*()_+-={}[]|\\:;\"'<>,.?/~`"}}),
                          (f"{n}_100k_chars", {"override": {n: Expr('"a" * 100_000')}})]
            elif named in ("Int", "Float"):
                cases += [(f"{n}_zero", {"override": {n: 0}}), (f"{n}_negative", {"override": {n: -1}}),
                          (f"{n}_int32_max", {"override": {n: 2147483647}})]
                if named == "Int":
                    cases.append((f"{n}_int32_overflow", {"override": {n: 2147483648}}))
                if n.lower() in PAGING_ARGS:
                    cases.append((f"{n}_huge", {"override": {n: 1_000_000}}))
            elif a["kind"] == "ENUM":
                vals = sc.types[named].get("values") or []
                if vals:
                    cases.append((f"{n}_lowercase_value", {"override": {n: vals[0].lower()}}))
            if not a["required"]:
                cases.append((f"{n}_explicit_null", {"override": {n: None}}))
        if not cases:
            continue
        table = mod.unique("EDGE_" + ident(op["id"]))
        rows = ",\n".join(f"    ({c[0]!r}, {_kw(c[1])})" for c in cases)
        mod.add(f"{table} = [\n{rows},\n]\n", is_test=False)
        mod.add(f'@pytest.mark.parametrize("label,kw", {table}, ids=[c[0] for c in {table}])\n'
                f"def {mod.unique('test_query_' + op['name'] + '_edge')}(gql, label, kw):\n"
                f"    op = OPS[{op['id']!r}]\n    resp = gql.run(op, **kw)\n    gr.expect_graceful(resp, label)\n")


def security(sc: Schema, mod: Module) -> list[str]:
    notes: list[str] = []
    I = "@pytest.mark.intrusive\n"
    mod.add(I + "def test_introspection_disabled(gql):\n"
            "    if gr.env_true(\"APITEST_ALLOW_INTROSPECTION\"):\n        pytest.skip(\"introspection is intentionally enabled here (APITEST_ALLOW_INTROSPECTION)\")\n"
            "    resp = gql.post(\"query { __schema { queryType { name } } }\")\n"
            "    data = (gr.body_of(resp) or {}).get(\"data\") if isinstance(gr.body_of(resp), dict) else None\n"
            "    if isinstance(data, dict) and data.get(\"__schema\"):\n"
            "        gr.fail(resp, \"introspection is enabled: disable it in production (set APITEST_ALLOW_INTROSPECTION=yes if this environment is meant to be open)\", \"MEDIUM\", \"API8:2023 security misconfiguration\")\n")
    first = next(iter(sc.no_arg_queries()), None)  # no required args: the error must come from the sub-field
    if first:
        fld = (sc.types.get(first["return_type"], {}).get("fields") or [{"name": "name"}])[0]["name"]
        typo = fld[:-1] if len(fld) > 3 else fld + "x"
        q = f"query {{ {first['name']}{{args}} {{ {typo} }} }}".replace("{args}", "")
        mod.add(I + f"def test_error_messages_do_not_suggest_schema_names(gql):\n    resp = gql.post({q!r})\n"
                "    text = \" \".join(str(e.get(\"message\")) for e in gr.errors_of(resp) if isinstance(e, dict))\n"
                "    if \"Did you mean\" in text:\n"
                "        gr.fail(resp, \"field suggestions reveal schema names even if introspection is off\", \"LOW\", \"API8:2023 security misconfiguration\")\n")
        mod.add(I + "def test_errors_do_not_leak_internals(gql):\n"
                f"    resp = gql.post({sc.query_text(first, ' { apitestUnknownSubfield }')!r}, {{}})\n    gr.expect_no_gql_error_leak(resp)\n")
    cyc = sc.find_cycle()
    if cyc:
        op, prefix, cycle = cyc
        chain = prefix + cycle * max(1, DEPTH // len(cycle))
        inner = "__typename"
        for f in reversed(chain):
            inner = f"{f} {{ {inner} }}"
        q = f"query ApitestDepth {{ {op['name']} {{ {inner} }} }}"
        mod.add(I + "def test_query_depth_is_limited(gql):\n"
                f"    resp = gql.post({q!r})\n"
                "    if resp.status_code < 500 and not (400 <= resp.status_code < 500) and not gr.errors_of(resp):\n"
                f"        gr.fail(resp, \"no query depth limit: a {len(chain) + 1}-level nested query was executed\", \"MEDIUM\", \"API4:2023 unrestricted resource consumption\")\n"
                "    gr.expect_no_crash(resp, \"deeply nested query\")\n")
    else:
        notes.append("graphql security: no recursive type reachable without arguments, so the depth-limit test was not generated")
    base = next((o for o in sc.no_arg_queries()), None)
    root = f"{base['name']} {{ __typename }}" if base else "__typename"
    aliased = " ".join(f"a{i}: {root}" for i in range(ALIASES))
    mod.add(I + "def test_alias_abuse_is_limited(gql):\n"
            f"    resp = gql.post({('query ApitestAliases { ' + aliased + ' }')!r})\n"
            "    if resp.status_code < 500 and not (400 <= resp.status_code < 500) and not gr.errors_of(resp):\n"
            f"        gr.fail(resp, \"{ALIASES} aliases of one field were executed in a single request: aliases can bypass per-request rate limits\", \"MEDIUM\", \"API4:2023 unrestricted resource consumption\")\n"
            "    gr.expect_no_crash(resp, \"alias flood\")\n")
    mod.add(I + "def test_batching_is_disabled_or_limited(gql):\n"
            "    if gr.env_true(\"APITEST_ALLOW_BATCHING\"):\n        pytest.skip(\"batching is intentionally enabled (APITEST_ALLOW_BATCHING)\")\n"
            f"    import json\n    resp = gql.post(\"\", raw=json.dumps([{{\"query\": \"query {{ __typename }}\"}}] * {BATCH}))\n"
            "    body = gr.body_of(resp)\n"
            "    if isinstance(body, list) and len(body) > 1:\n"
            f"        gr.fail(resp, \"batched requests are executed ({BATCH} operations in one HTTP call): brute-force and rate-limit bypass risk\", \"MEDIUM\", \"API4:2023 unrestricted resource consumption\")\n"
            "    gr.expect_no_crash(resp, \"batched request\")\n")
    mod.add(I + "@pytest.mark.rate_limit\ndef test_rate_limiting_present(gql):\n"
            "    gr.require_flag(\"APITEST_EXPECT_RATE_LIMIT\", \"this API is expected to rate-limit\")\n"
            "    gr.skip_if_paced(gql)\n"
            "    n = gr.burst_size(40)\n"
            "    codes = [gql.post(\"query { __typename }\").status_code for _ in range(n)]\n"
            "    assert 429 in codes, f\"[MEDIUM][API4:2023] {n} rapid requests were never throttled (no 429)\"\n")
    for label, path, query in sc.sensitive_paths():
        mod.add(I + f"def {mod.unique('test_field_authz_' + ident(label))}(gql):\n"
                f"    gr.require_flag(\"APITEST_GQL_EXPECT_FIELD_AUTH\", \"confirm {label} must be hidden from anonymous callers\")\n"
                f"    resp = gql.post({query!r}, auth=\"none\")\n"
                f"    gr.expect_field_protected(resp, {path!r}, {label!r})\n")
    for op in sc.ops:
        if op["kind"] == "query" and any(a["required"] and a["named"] == "ID" for a in op["args"]):
            mod.add(I + f"def {mod.unique('test_bola_' + op['name'])}(gql):\n    op = OPS[{op['id']!r}]\n"
                    "    resp = gql.run(op, victim=True)  # ids of ANOTHER user's objects: APITEST_GQL_VICTIM_<ARG>\n"
                    f"    gr.expect_denied_gql(resp, {op['name']!r}, \"API1:2023 BOLA\", \"user A can fetch an object owned by another user\")\n")
    for op in sc.ops:
        if op["kind"] == "subscription" or (op["kind"] == "mutation" and DESTRUCTIVE_RE.match(op["name"])):
            continue
        strs = [a for a in op["args"] if a["named"] == "String" and not a["list"]][:3]
        cases = [(f"{a['name']}[{i}]", {"override": {a["name"]: pl}}) for a in strs for i, pl in enumerate(INJECTION)]
        if not cases:
            continue
        table = mod.unique("INJECT_" + ident(op["id"]))
        rows = ",\n".join(f"    ({c[0]!r}, {_kw(c[1])})" for c in cases)
        mod.add(f"{table} = [\n{rows},\n]\n", is_test=False)
        mod.add(I + f'@pytest.mark.parametrize("label,kw", {table}, ids=[c[0] for c in {table}])\n'
                f"def {mod.unique('test_injection_' + op['name'])}(gql, label, kw):\n    op = OPS[{op['id']!r}]\n"
                "    resp = gql.run(op, **kw)\n    gr.expect_injection_safe(resp, label)\n")
    return notes


def contract(sc: Schema, mod: Module) -> None:
    """Run each read operation and check the answer has the shape the schema promises (types, enum values, non-null)."""
    for op in sc.ops:
        if op["kind"] != "query" or op["return_kind"] not in ("OBJECT", "INTERFACE") or not op["selection"]:
            continue
        nn = ", nonnull=True" if op["return_non_null"] else ""
        mod.add(f"def {mod.unique('test_contract_' + op['name'] + '_matches_schema')}(gql):\n    op = OPS[{op['id']!r}]\n"
                f"    resp = gql.run(op)\n    value = gr.expect_data(resp, {op['name']!r}{nn})\n"
                f"    gr.expect_shape(resp, value, {op['returns']!r}, op.selection, TYPES, ENUMS, KINDS)\n")


FORMAT_VALUES = {"email": "not-an-email", "uuid": "not-a-uuid", "url": "not a url", "uri": "not a url", "date-time": "yesterday", "date": "someday",
                 "ipv4": "999.1.1.1"}
MAX_VALIDATION_CASES = 40
MAX_LIMIT_PAD = 10000


def _with(base: dict, path: tuple, value, drop: bool = False) -> dict:
    """Copy of the baseline variables with one value set (or removed) at `path`; missing parents are created."""
    out = dict(base)
    cur = out
    for key in path[:-1]:
        cur[key] = dict(cur[key]) if isinstance(cur.get(key), dict) else {}
        cur = cur[key]
    if drop:
        cur.pop(path[-1], None)
    else:
        cur[path[-1]] = value
    return out


def _violations(named: str, kind: str, limits: dict) -> list[tuple[str, object]]:
    """(label, value) pairs that break the declared type or the @constraint limits of one argument or input field."""
    out: list[tuple[str, object]] = []
    key = "ENUM" if kind == "ENUM" else "INPUT_OBJECT" if kind == "INPUT_OBJECT" else named
    if key in WRONG_TYPE:
        out.append(("wrong_type", WRONG_TYPE[key]))
    if isinstance(limits.get("minLength"), int) and 1 <= limits["minLength"] <= MAX_LIMIT_PAD:
        out.append(("too_short", "a" * (limits["minLength"] - 1)))
    if isinstance(limits.get("maxLength"), int) and 0 <= limits["maxLength"] < MAX_LIMIT_PAD:
        out.append(("too_long", "a" * (limits["maxLength"] + 1)))
    if isinstance(limits.get("min"), (int, float)):
        out.append(("below_min", limits["min"] - 1))
    if isinstance(limits.get("max"), (int, float)):
        out.append(("above_max", limits["max"] + 1))
    if limits.get("format") in FORMAT_VALUES:
        out.append(("bad_format", FORMAT_VALUES[limits["format"]]))
    return out


def validation_cases(sc: Schema, op: dict) -> list[tuple]:
    """One violation per case, on the valid baseline: missing and wrong-typed nested input fields, unknown fields, constraint breaches."""
    base = sc.baseline_vars(op)
    cases: list[tuple] = []

    def add(label: str, variables: dict) -> None:
        cases.append((label, {"variables": variables}))

    for a in op["args"]:
        if a["list"]:
            continue
        for what, bad in _violations(a["named"], a["kind"], a.get("constraints") or {}):
            if what != "wrong_type":        # wrong top-level types are the negative tests' job
                add(f"{a['name']}_{what}", _with(base, (a["name"],), bad))
        if a["kind"] != "INPUT_OBJECT":
            continue
        for f in sc.types.get(a["named"], {}).get("fields", []):
            if f.get("list"):
                continue
            path = (a["name"], f["name"])
            label = f"{a['name']}.{f['name']}"
            if f.get("required") and a["name"] in base:
                add(f"{label}_missing", _with(base, path, None, drop=True))
            for what, bad in _violations(f["named"], f["kind"], f.get("constraints") or {}):
                add(f"{label}_{what}", _with(base, path, bad))
        if a["name"] in base:
            add(f"{a['name']}_unknown_field", _with(base, (a["name"], "apitestUnknownField"), 1))
    return cases[:MAX_VALIDATION_CASES]


def data_validation(sc: Schema, mod: Module) -> list[str]:
    """Each case breaks the declared type or constraint of one input and expects the server to reject it."""
    notes: list[str] = []
    made = False
    for op in sc.ops:
        if op["kind"] == "subscription":
            continue
        cases = validation_cases(sc, op)
        if not cases:
            continue
        made = True
        table = mod.unique("VALIDATION_" + ident(op["id"]))
        rows = ",\n".join(f"    ({c[0]!r}, {_kw(c[1])})" for c in cases)
        mod.add(f"{table} = [\n{rows},\n]\n", is_test=False)
        pre = "    gr.require_destructive()\n" if op["kind"] == "mutation" and DESTRUCTIVE_RE.match(op["name"]) else ""
        mod.add(f'@pytest.mark.parametrize("label,kw", {table}, ids=[c[0] for c in {table}])\n'
                f"def {mod.unique('test_' + op['kind'] + '_' + op['name'] + '_validates_input')}(gql, label, kw):\n"
                f"    op = OPS[{op['id']!r}]\n{pre}    resp = gql.run(op, **kw)\n    gr.expect_graphql_error(resp, label)\n")
    if not made:
        notes.append("graphql data-validation: no operation takes an input object or a constrained argument, so no cases were generated")
    elif not any(a.get("constraints") for o in sc.ops for a in o["args"]) and not any(
            f.get("constraints") for t in sc.types.values() for f in t.get("fields", [])):
        notes.append("graphql data-validation: the schema declares no @constraint limits, so only type, missing-field and unknown-field cases were generated")
    return notes


CREATE_RE = re.compile(r"^(create|add|new|register)", re.I)
UPDATE_RE = re.compile(r"^(update|edit|modify|patch|set)", re.I)
REMOVE_RE = re.compile(r"^(delete|remove|destroy)", re.I)


def _string_paths(sc: Schema, op: dict, type_fields: set[str], required_only: bool) -> list[list[str]]:
    """Paths (argument, then input fields) of String values that the object type also returns, so the flow can send and read them back."""
    out: list[list[str]] = []
    for a in op["args"]:
        if a["list"] or (required_only and not a["required"]):
            continue
        if a["named"] == "String" and a["name"] in type_fields:
            out.append([a["name"]])
        elif sc.kind(a["named"]) == "INPUT_OBJECT":
            for f in sc.types[a["named"]].get("fields", []):
                if f["named"] == "String" and not f.get("list") and f["name"] in type_fields and (f.get("required") or not required_only):
                    out.append([a["name"], f["name"]])
    return out[:3]


def flows(sc: Schema) -> list[dict]:
    """Object types that can be created, read back and deleted, with the operations that do it."""
    found = []
    for tname, t in sc.types.items():
        if t.get("kind") != "OBJECT" or not any(f["name"] == "id" for f in t.get("fields", [])):
            continue
        fields = {f["name"] for f in t.get("fields", [])}
        of = [o for o in sc.ops if o["return_type"] == tname and not o["return_list"]]
        create = next((o for o in of if o["kind"] == "mutation" and CREATE_RE.match(o["name"]) and "id" in o["selection"]), None)
        read = next((o for o in of if o["kind"] == "query" and len([a for a in o["args"] if a["required"]]) == 1
                     and next(a for a in o["args"] if a["required"])["named"] == "ID" and "id" in o["selection"]), None)
        delete = next((o for o in sc.ops if o["kind"] == "mutation" and REMOVE_RE.match(o["name"]) and o["name"].lower().endswith(tname.lower())
                       and [a["named"] for a in o["args"] if a["required"]] == ["ID"]), None)
        if not (create and read and delete):
            continue
        listing = next((o for o in sc.ops if o["kind"] == "query" and o["return_type"] == tname and o["return_list"]
                        and not any(a["required"] for a in o["args"]) and "id" in o["selection"]), None)
        update = next((o for o in of if o["kind"] == "mutation" and UPDATE_RE.match(o["name"]) and any(a["named"] == "ID" and a["required"] for a in o["args"])), None)
        flow = {"type": tname, "create": create["id"], "read": read["id"], "read_arg": next(a["name"] for a in read["args"] if a["required"]),
                "delete": delete["id"], "delete_arg": next(a["name"] for a in delete["args"] if a["required"]), "id_field": "id",
                "tag": _string_paths(sc, create, fields, True)}
        if listing:
            flow["list"] = listing["id"]
        if update:
            paths = _string_paths(sc, update, fields, False)
            if paths:
                flow["update"], flow["update_arg"] = update["id"], next(a["name"] for a in update["args"] if a["required"] and a["named"] == "ID")
                flow["update_path"] = paths[0]
        found.append(flow)
    return found


SIZE_ARGS = {"first", "limit", "take", "page_size", "pagesize", "size", "count", "per_page", "perpage"}
CURSOR_ARGS = {"after", "cursor", "offset", "skip", "page", "start"}


def _paging(op: dict) -> tuple[str | None, str | None]:
    """(size argument, cursor argument) of a list query, by the usual argument names."""
    size = next((a["name"] for a in op["args"] if a["name"].lower() in SIZE_ARGS and a["named"] == "Int" and not a["list"]), None)
    cursor = next((a["name"] for a in op["args"] if a["name"].lower() in CURSOR_ARGS and a["named"] in ("String", "Int", "ID") and not a["list"]), None)
    return size, cursor


def resilience(sc: Schema, mod: Module) -> list[str]:
    """Repeatable reads, response budget, page-size and cursor handling, a bounded rate-limit probe and a repeated delete."""
    notes: list[str] = []
    for op in sc.ops:
        if op["kind"] != "query":
            continue
        mod.add(f"def {mod.unique('test_' + op['name'] + '_is_repeatable')}(gql):\n    op = OPS[{op['id']!r}]\n"
                "    first, second = gql.run(op), gql.run(op)\n    gr.expect_graceful(first, \"query\")\n    gr.expect_repeatable(first, second)\n")
        mod.add(f"def {mod.unique('test_' + op['name'] + '_responds_within_budget')}(gql):\n    op = OPS[{op['id']!r}]\n"
                "    gr.expect_fast(gql.run(op))\n")
        size, cursor = _paging(op)
        if not op["return_list"] or not (size or cursor):
            continue
        cases: list[tuple] = []
        if size:
            cases.append((f"{size}=1", {"override": {size: 1}}, 1))
            cases += [(f"{size}=0", {"override": {size: 0}}, 0), (f"{size}=-1", {"override": {size: -1}}, None),
                      (f"{size}=int32_max", {"override": {size: 2147483647}}, None)]
        if cursor:
            kind = next(a["named"] for a in op["args"] if a["name"] == cursor)
            junk = ["apitest-not-a-cursor", "", "A" * 1000] if kind != "Int" else [-1, 2147483647]
            cases += [(f"{cursor}={str(v)[:12] or 'empty'}", {"override": {cursor: v}}, None) for v in junk]
        table = mod.unique("PAGING_" + ident(op["id"]))
        rows = ",\n".join(f"    ({c[0]!r}, {_kw(c[1])}, {c[2]!r})" for c in cases)
        mod.add(f"{table} = [\n{rows},\n]\n", is_test=False)
        mod.add(f'@pytest.mark.parametrize("label,kw,at_most", {table}, ids=[c[0] for c in {table}])\n'
                f"def {mod.unique('test_' + op['name'] + '_pagination')}(gql, label, kw, at_most):\n    op = OPS[{op['id']!r}]\n"
                "    resp = gql.run(op, **kw)\n    gr.expect_graceful(resp, label)\n"
                f"    if at_most is not None and not gr.errors_of(resp):\n        gr.expect_page_size(resp, {op['name']!r}, at_most)\n")
    mod.add("@pytest.mark.intrusive\n@pytest.mark.rate_limit\ndef test_rate_limit_behaviour(gql):\n"
            "    gr.expect_rate_limit_behaviour(gql, gr.burst_size(30))\n")
    table = flows(sc)
    if table:
        mod.add(f"FLOWS = {py({f['type']: f for f in table})}\n", is_test=False)
        for f in table:
            mod.add(f"def {mod.unique('test_' + ident(f['type']) + '_repeated_delete_is_safe')}(gql):\n    gfl.run_repeated_delete(gql, OPS, FLOWS[{f['type']!r}])\n")
    else:
        notes.append("graphql resilience: no object type has a create, a read-by-id and a delete operation, so the repeated-delete check was not generated")
    return notes


def integration(sc: Schema, mod: Module) -> list[str]:
    """create -> read -> list -> update -> read -> delete -> read for each object type the schema can create and delete."""
    table = flows(sc)
    if not table:
        return ["graphql integration: no object type has a create, a read-by-id and a delete operation, so no flow was generated"]
    mod.add(f"FLOWS = {py({f['type']: f for f in table})}\n", is_test=False)
    for f in table:
        mod.add(f"def {mod.unique('test_flow_' + ident(f['type']) + '_lifecycle')}(gql):\n    gfl.run_flow(gql, OPS, FLOWS[{f['type']!r}])\n")
    return []


def fuzz(sc: Schema, mod: Module) -> list[str]:
    """One intrusive, seeded and bounded fuzz test per operation (subscriptions are skipped: they need a websocket)."""
    table: dict[str, dict] = {}
    for op in sc.ops:
        if op["kind"] == "subscription":
            continue
        table[op["id"]] = {"optional": [a["name"] for a in op["args"] if not a["required"]],
                           "destructive": op["kind"] == "mutation" and bool(DESTRUCTIVE_RE.match(op["name"]))}
    if not table:
        return ["graphql fuzz: the schema has no query or mutation to fuzz"]
    for spec in table.values():
        spec["total"] = len(table)   # lets the runtime split the run budget fairly between operations
    mod.add(f"FUZZ_OPS = {py(table)}\n", is_test=False)
    mod.add("@pytest.mark.intrusive\n@pytest.mark.parametrize(\"op_id\", list(FUZZ_OPS))\n"
            "def test_fuzz_operation(gql, op_id):\n    gfz.fuzz_operation(gql, OPS[op_id], FUZZ_OPS[op_id])\n")
    return []


MODULES = {
    "smoke": ("test_graphql_smoke.py", "GraphQL smoke tests", smoke),
    "positive": ("test_graphql_positive.py", "GraphQL positive tests", positive),
    "negative": ("test_graphql_negative.py", "GraphQL negative tests", negative),
    "edge": ("test_graphql_edge.py", "GraphQL edge-case tests", edge),
    "security": ("test_graphql_security.py", "GraphQL security tests (OWASP API Top 10)", security),
    "contract": ("test_graphql_contract.py", "GraphQL contract tests (the answer matches the schema)", contract),
    "data-validation": ("test_graphql_data_validation.py", "GraphQL data-validation tests (one violation per case)", data_validation),
    "integration": ("test_graphql_integration.py", "GraphQL integration flows (create, read, list, update, delete)", integration),
    "resilience": ("test_graphql_resilience.py", "GraphQL resilience tests (repeatable reads, paging, rate limits)", resilience),
    "fuzz": ("test_graphql_fuzz.py", "GraphQL fuzz tests (opt-in, intrusive: non-production targets only)", fuzz),
}


def generate(doc: dict, out: Path, types: list[str], base_url_override: str | None = None) -> dict:
    import gen_pytest as gp
    chosen = [t for t in GQL_TYPES if t not in OPT_IN] if types == ["all"] else types
    unsupported = [t for t in chosen if t not in GQL_TYPES]
    if unsupported:
        raise _common.IntakeError(f"GraphQL suites support: {', '.join(GQL_TYPES)} (fuzz is opt-in), all. Not available for GraphQL yet: {', '.join(unsupported)}")
    sc = Schema(doc)
    base_url = gp.default_base_url(doc, base_url_override)
    out.mkdir(parents=True, exist_ok=True)
    rerun = gp.rerun_command(out, base_url, chosen)
    fixture = f'''

@pytest.fixture(scope="session")
def gql(base_url):
    import gql_runtime as gr
    return gr.Gql(base_url, SCHEMES, {sc.path!r})
'''
    files = {
        "apitest_runtime.py": gp.runtime_source(),
        "gql_runtime.py": (gp.SKILL_DIR / "templates" / "pytest" / "gql_runtime.py").read_text(encoding="utf-8"),
        "conftest.py": gp.conftest_source(doc, base_url, fixture),
        "operations.py": operations_source(sc),
    }
    notes: list[str] = list(doc.get("warnings", [])[:0])
    counts: dict[str, int] = {}
    if "integration" in chosen or "resilience" in chosen:
        files["gql_flow.py"] = (gp.SKILL_DIR / "templates" / "pytest" / "gql_flow.py").read_text(encoding="utf-8")
    if "fuzz" in chosen:
        for name in ("fuzz_runtime.py", "gql_fuzz.py"):
            files[name] = (gp.SKILL_DIR / "templates" / "pytest" / name).read_text(encoding="utf-8")
    for t in chosen:
        fname, title, fn = MODULES[t]
        mod = Module(title, t, rerun)
        extra = fn(sc, mod)
        if isinstance(extra, list):
            notes += extra
        if mod.tests:
            head = {"fuzz": "import gql_fuzz as gfz  # noqa: F401\n", "integration": "import gql_flow as gfl  # noqa: F401\n", "resilience": "import gql_flow as gfl  # noqa: F401\n"}.get(t, "")
            files[fname] = mod.render(head + "import gql_runtime as gr  # noqa: F401\nfrom operations import ENUMS, KINDS, OPS, TYPES  # noqa: F401\n").replace(
                "from endpoints import EP\n", "")
            counts[t] = mod.tests
    for name, src in files.items():
        gp.check_compiles(name, src)
        (out / name).write_text(src, encoding="utf-8")
    return {"out": str(out), "files": sorted(files), "counts": counts, "notes": notes, "rerun": rerun,
            "env": gp.env_names(doc), "base_url": base_url}
