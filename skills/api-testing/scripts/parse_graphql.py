#!/usr/bin/env python3
"""Parse a GraphQL schema (introspection JSON or SDL) into the normalized intake JSON.

Usage:  python parse_graphql.py schema.graphql|introspection.json [-o intake.json] [--summary] [--url https://host/graphql]

Offline, stdlib only (a small SDL parser is built in). The output has the same top-level keys as REST intake
(source, info, servers, security_schemes, endpoints, warnings) plus a `graphql` block with operations and types,
so planning, auth, reporting and the k6 load generator can treat GraphQL like any HTTP API: the single
endpoint is POST <path> with {query, variables, operationName}. See references/graphql.md.
Secrets: argument/field default values of secret-looking names are dropped; credentials come from env vars.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import IntakeError, env_name, is_secret_name, load_document, redact_example, strip_userinfo  # noqa: E402

SCALARS = {"Int", "Float", "String", "Boolean", "ID"}
SENSITIVE_FIELD_RE = re.compile(
    r"(^|_)(password|passwd|pwd|hash|secret|token|ssn|email|phone|salary|dob|birth|address|api_?key|private|"
    r"credit|card|cvv|iban|role|admin|internal)($|_)")
MAX_SELECTION = 8


# --------------------------------------------------------------------------- lexer (shared with tests/mock_graphql.py)
TOKEN_RE = re.compile(r'''
    (?P<ws>[\s,﻿]+|\#[^\n]*)
  | (?P<block>"""(?:\\"""|.)*?""")
  | (?P<string>"(?:\\.|[^"\\\n])*")
  | (?P<spread>\.\.\.)
  | (?P<float>-?\d+(?:\.\d+)?[eE][+-]?\d+|-?\d+\.\d+)
  | (?P<int>-?\d+)
  | (?P<name>[_A-Za-z][_0-9A-Za-z]*)
  | (?P<punct>[!$&()\[\]:=@{|}])
''', re.X | re.S)


def tokenize(text: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            raise IntakeError(f"GraphQL syntax error at offset {pos}: {text[pos:pos + 20]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        val = m.group()
        if kind == "block":
            val = _block_string(val)
            kind = "string"
        elif kind == "string":
            try:
                val = json.loads(val)
            except ValueError:
                val = val[1:-1]
        out.append((kind, val))
    out.append(("eof", ""))
    return out


def _block_string(raw: str) -> str:
    body = raw[3:-3].replace('\\"""', '"""')
    lines = body.split("\n")
    indents = [len(l) - len(l.lstrip()) for l in lines[1:] if l.strip()]
    cut = min(indents) if indents else 0
    lines = [lines[0]] + [l[cut:] for l in lines[1:]]
    return "\n".join(lines).strip()


class Parser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.t, self.i = tokens, 0

    def peek(self, kind: str | None = None, val: str | None = None) -> bool:
        k, v = self.t[self.i]
        return (kind is None or k == kind) and (val is None or v == val)

    def next(self) -> tuple[str, str]:
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expect(self, kind: str, val: str | None = None) -> str:
        if not self.peek(kind, val):
            k, v = self.t[self.i]
            raise IntakeError(f"GraphQL parse error: expected {val or kind}, found {v or k!r}")
        return self.next()[1]

    def accept(self, kind: str, val: str | None = None) -> bool:
        if self.peek(kind, val):
            self.i += 1
            return True
        return False

    # ---- values / types
    def value(self) -> Any:
        k, v = self.next()
        if k == "int":
            return int(v)
        if k == "float":
            return float(v)
        if k == "string":
            return v
        if k == "name":
            return {"true": True, "false": False, "null": None}.get(v, v)
        if k == "punct" and v == "[":
            out = []
            while not self.accept("punct", "]"):
                out.append(self.value())
            return out
        if k == "punct" and v == "{":
            obj = {}
            while not self.accept("punct", "}"):
                name = self.expect("name")
                self.expect("punct", ":")
                obj[name] = self.value()
            return obj
        if k == "punct" and v == "$":
            return {"$var": self.expect("name")}
        raise IntakeError(f"GraphQL parse error: unexpected {v!r} in value")

    def type_ref(self) -> dict:
        if self.accept("punct", "["):
            inner = self.type_ref()
            self.expect("punct", "]")
            ref = {"list": inner, "non_null": False}
        else:
            ref = {"name": self.expect("name"), "non_null": False}
        if self.accept("punct", "!"):
            ref["non_null"] = True
        return ref

    def directives(self) -> None:
        while self.accept("punct", "@"):
            self.expect("name")
            if self.accept("punct", "("):
                while not self.accept("punct", ")"):
                    self.expect("name")
                    self.expect("punct", ":")
                    self.value()

    def description(self) -> str:
        return self.next()[1] if self.peek("string") else ""

    def args(self, closer: str) -> list[dict]:
        out = []
        while not self.accept("punct", closer):
            desc = self.description()
            name = self.expect("name")
            self.expect("punct", ":")
            ref = self.type_ref()
            default = self.value() if self.accept("punct", "=") else None
            self.directives()
            out.append({"name": name, "ref": ref, "default": default, "description": desc})
        return out


def ref_str(ref: dict) -> str:
    s = f"[{ref_str(ref['list'])}]" if "list" in ref else ref["name"]
    return s + ("!" if ref.get("non_null") else "")


def ref_named(ref: dict) -> str:
    return ref_named(ref["list"]) if "list" in ref else ref["name"]


def parse_sdl(text: str) -> dict:
    """SDL -> {'schema': {query,mutation,subscription}, 'types': {name: {kind, fields|values|possible}}}."""
    p = Parser(tokenize(text))
    types: dict[str, dict] = {}
    roots: dict[str, str] = {}

    def merge(name: str, kind: str, body: dict) -> None:
        cur = types.setdefault(name, {"kind": kind, "fields": [], "values": [], "possible": []})
        for k in ("fields", "values", "possible"):
            cur[k] += body.get(k, [])
        if body.get("description"):
            cur["description"] = body["description"]

    while not p.peek("eof"):
        desc = p.description()
        word = p.expect("name")
        extend = word == "extend"
        if extend:
            word = p.expect("name")
        if word == "schema":
            p.directives()
            p.expect("punct", "{")
            while not p.accept("punct", "}"):
                op = p.expect("name")
                p.expect("punct", ":")
                roots[op] = p.expect("name")
        elif word == "scalar":
            name = p.expect("name")
            p.directives()
            merge(name, "SCALAR", {})
        elif word in ("type", "interface", "input"):
            name = p.expect("name")
            if p.accept("name", "implements"):
                p.accept("punct", "&")
                p.expect("name")
                while p.accept("punct", "&"):
                    p.expect("name")
            p.directives()
            fields = []
            if p.accept("punct", "{"):
                while not p.accept("punct", "}"):
                    fdesc = p.description()
                    fname = p.expect("name")
                    fargs = p.args(")") if p.accept("punct", "(") else []
                    if word == "input":
                        # input field: name: Type = default
                        p.expect("punct", ":")
                        ref = p.type_ref()
                        default = p.value() if p.accept("punct", "=") else None
                        p.directives()
                        fields.append({"name": fname, "ref": ref, "default": default, "args": [], "description": fdesc})
                        continue
                    p.expect("punct", ":")
                    ref = p.type_ref()
                    p.directives()
                    fields.append({"name": fname, "ref": ref, "args": fargs, "description": fdesc})
            merge(name, {"type": "OBJECT", "interface": "INTERFACE", "input": "INPUT_OBJECT"}[word],
                  {"fields": fields, "description": desc})
        elif word == "enum":
            name = p.expect("name")
            p.directives()
            values = []
            if p.accept("punct", "{"):
                while not p.accept("punct", "}"):
                    p.description()
                    values.append(p.expect("name"))
                    p.directives()
            merge(name, "ENUM", {"values": values})
        elif word == "union":
            name = p.expect("name")
            p.directives()
            members = []
            if p.accept("punct", "="):
                p.accept("punct", "|")
                members.append(p.expect("name"))
                while p.accept("punct", "|"):
                    members.append(p.expect("name"))
            merge(name, "UNION", {"possible": members})
        elif word == "directive":
            p.expect("punct", "@")
            p.expect("name")
            if p.accept("punct", "("):
                p.args(")")
            p.accept("name", "repeatable")
            p.expect("name", "on")
            p.accept("punct", "|")
            p.expect("name")
            while p.accept("punct", "|"):
                p.expect("name")
        else:
            raise IntakeError(f"GraphQL parse error: unexpected definition keyword {word!r}")
    for op, default in (("query", "Query"), ("mutation", "Mutation"), ("subscription", "Subscription")):
        if op not in roots and default in types:
            roots[op] = default
    return {"roots": roots, "types": types}


def _intro_ref(t: dict) -> dict:
    if t["kind"] == "NON_NULL":
        r = _intro_ref(t["ofType"])
        r["non_null"] = True
        return r
    if t["kind"] == "LIST":
        return {"list": _intro_ref(t["ofType"]), "non_null": False}
    return {"name": t["name"], "non_null": False}


def parse_introspection(doc: dict) -> dict:
    schema = doc.get("data", doc).get("__schema")
    if not isinstance(schema, dict):
        raise IntakeError("Introspection JSON has no __schema (expected the result of the standard introspection query).")
    types: dict[str, dict] = {}
    for t in schema.get("types", []):
        if t["name"].startswith("__"):
            continue
        def arg(a):
            return {"name": a["name"], "ref": _intro_ref(a["type"]), "default": _intro_default(a.get("defaultValue")),
                    "description": a.get("description") or ""}
        fields = [{"name": f["name"], "ref": _intro_ref(f["type"]), "args": [arg(a) for a in f.get("args", [])],
                   "description": f.get("description") or ""} for f in (t.get("fields") or [])]
        fields += [{"name": f["name"], "ref": _intro_ref(f["type"]), "args": [],
                    "default": _intro_default(f.get("defaultValue")), "description": f.get("description") or ""}
                   for f in (t.get("inputFields") or [])]
        types[t["name"]] = {"kind": t["kind"], "fields": fields, "values": [v["name"] for v in (t.get("enumValues") or [])],
                            "possible": [x["name"] for x in (t.get("possibleTypes") or [])],
                            "description": t.get("description") or ""}
    roots = {}
    for op, key in (("query", "queryType"), ("mutation", "mutationType"), ("subscription", "subscriptionType")):
        if schema.get(key):
            roots[op] = schema[key]["name"]
    return {"roots": roots, "types": types}


def _intro_default(v: Any) -> Any:
    if v is None:
        return None
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return v


# --------------------------------------------------------------------------- normalization
def _kind(types: dict, name: str) -> str:
    if name in SCALARS:
        return "SCALAR"
    return types.get(name, {}).get("kind", "SCALAR")


def _arg_out(types: dict, a: dict) -> dict:
    default = a.get("default")
    if is_secret_name(a["name"]):
        default = None
    named = ref_named(a["ref"])
    return {"name": a["name"], "type": ref_str(a["ref"]), "named": named, "kind": _kind(types, named),
            "required": bool(a["ref"].get("non_null")) and default is None, "list": "list" in a["ref"],
            "default": redact_example(default, a["name"]), "description": a.get("description", "")}


def scalar_selection(types: dict, type_name: str) -> list[str]:
    t = types.get(type_name) or {}
    out = [f["name"] for f in t.get("fields", [])
           if _kind(types, ref_named(f["ref"])) in ("SCALAR", "ENUM") and not any(a["ref"].get("non_null") for a in f["args"])]
    return out[:MAX_SELECTION]


def normalize(parsed: dict, source: dict, url: str | None) -> dict:
    types, roots = parsed["types"], parsed["roots"]
    warnings: list[str] = []
    if "query" not in roots or roots["query"] not in types:
        raise IntakeError("No query root type found (expected 'type Query' or a schema { query: ... } block).")
    ops = []
    for kind in ("query", "mutation", "subscription"):
        root = types.get(roots.get(kind, ""), {})
        for f in root.get("fields", []):
            named = ref_named(f["ref"])
            ops.append({
                "id": f"{kind}.{f['name']}", "kind": kind, "name": f["name"], "description": f.get("description", ""),
                "args": [_arg_out(types, a) for a in f["args"]],
                "returns": ref_str(f["ref"]), "return_type": named, "return_kind": _kind(types, named),
                "return_list": "list" in f["ref"], "return_non_null": bool(f["ref"].get("non_null")),
                "selection": scalar_selection(types, named) if _kind(types, named) in ("OBJECT", "INTERFACE") else [],
            })
    if any(o["kind"] == "subscription" for o in ops):
        warnings.append("Subscriptions are listed but not tested (they need WebSocket/SSE transport; v2).")
    type_out: dict[str, dict] = {}
    for name, t in types.items():
        if name in roots.values() and name in (roots.get("subscription"),):
            continue
        entry: dict[str, Any] = {"kind": t["kind"]}
        if t["kind"] == "ENUM":
            entry["values"] = t["values"]
        elif t["kind"] == "UNION":
            entry["possible"] = t["possible"]
        else:
            entry["fields"] = []
            for f in t["fields"]:
                named = ref_named(f["ref"])
                fo = {"name": f["name"], "type": ref_str(f["ref"]), "named": named, "kind": _kind(types, named),
                      "sensitive": bool(SENSITIVE_FIELD_RE.search(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", f["name"]).lower()))}
                if t["kind"] == "INPUT_OBJECT":
                    fo["required"] = bool(f["ref"].get("non_null")) and f.get("default") is None
                    fo["list"] = "list" in f["ref"]
                    fo["default"] = None if is_secret_name(f["name"]) else redact_example(f.get("default"), f["name"])
                elif f["args"]:
                    fo["args"] = [_arg_out(types, a) for a in f["args"]]
                entry["fields"].append(fo)
        type_out[name] = entry
    servers, path = [], "/graphql"
    if url:
        clean, stripped = strip_userinfo(url)
        if stripped:
            warnings.append("Credentials in the URL (user:pass@) were removed; use env vars.")
        u = urlsplit(clean)
        if u.scheme in ("http", "https") and u.netloc:
            servers, path = [f"{u.scheme}://{u.netloc}"], u.path or "/graphql"
        else:
            warnings.append(f"--url is not an http(s) URL: {clean!r}")
    if not servers:
        warnings.append("No endpoint URL: pass --url or set APITEST_BASE_URL (and APITEST_GQL_URL for a non-/graphql path) when running.")
    warnings.append("GraphQL schemas do not declare authentication: a bearer token from APITEST_BEARERAUTH_TOKEN is sent when set. "
                    "Tell me if some operations are public or use another scheme.")
    body_schema = {"type": "object", "required": ["query"], "properties": {
        "query": {"type": "string"}, "variables": {"type": "object"}, "operationName": {"type": "string"}}}
    endpoint = {
        "id": "graphql", "method": "POST", "path": path, "summary": "GraphQL endpoint", "tags": ["graphql"],
        "deprecated": False, "auth_required": False, "security": [], "parameters": [],
        "request_body": {"content_type": "application/json", "required": True, "schema": body_schema},
        "responses": {"200": {"description": "GraphQL response (errors arrive here too)", "content_type": "application/json",
                              "schema": {"type": "object", "properties": {"data": {"nullable": True}, "errors": {"type": "array"}}}}},
    }
    return {
        "source": source, "info": {"title": "GraphQL API", "version": ""}, "servers": servers,
        "security_schemes": {"bearerAuth": {"type": "http", "scheme": "bearer", "env": [env_name("bearerAuth", "TOKEN")]}},
        "endpoints": [endpoint],
        "graphql": {"query_type": roots.get("query"), "mutation_type": roots.get("mutation"),
                    "subscription_type": roots.get("subscription"), "operations": ops, "types": type_out},
        "warnings": warnings,
    }


def looks_like_introspection(doc: Any) -> bool:
    return isinstance(doc, dict) and ("__schema" in doc or (isinstance(doc.get("data"), dict) and "__schema" in doc["data"]))


def build(path: str, url: str | None = None) -> dict:
    p = Path(path)
    if not p.is_file():
        raise IntakeError(f"File not found: {path}")
    text = p.read_text(encoding="utf-8-sig")
    if text.lstrip().startswith("{") and p.suffix.lower() != ".graphql":
        doc = load_document(path)
        parsed, fmt = parse_introspection(doc), "introspection"
    else:
        parsed, fmt = parse_sdl(text), "sdl"
    return normalize(parsed, {"type": "graphql", "format": fmt, "file": p.name}, url)


def summarize_graphql(doc: dict) -> str:
    g = doc["graphql"]
    rows = [f"Source: graphql ({doc['source']['format']})"]
    if doc["servers"]:
        rows.append("Endpoint: " + doc["servers"][0] + doc["endpoints"][0]["path"])
    rows.append(f"Operations: {len(g['operations'])}   Types: {len(g['types'])}")
    rows.append("")
    for o in g["operations"]:
        args = ", ".join(f"{a['name']}: {a['type']}" for a in o["args"])
        rows.append(f"{o['kind']:12} {o['name']}({args}) -> {o['returns']}")
    rows += [f"WARNING: {w}" for w in doc["warnings"]]
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="parse_graphql.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--url", help="GraphQL endpoint URL (recorded as server + path; no credentials)")
    args = ap.parse_args(argv)
    try:
        doc = build(args.input, args.url)
    except IntakeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.summary:
        print(summarize_graphql(doc))
    text = json.dumps(doc, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.output} ({len(doc['graphql']['operations'])} operations)", file=sys.stderr)
    elif not args.summary:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
