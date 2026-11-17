#!/usr/bin/env python3
"""Parse a .proto file (proto2 or proto3) into the normalized intake JSON for gRPC services.

Usage:  python parse_proto.py service.proto [-o intake.json] [--summary]

Standard library only. Reads the file, the files it imports from the same folder (relative paths, at most 20 files of 2 MB, nothing outside
that folder) and nothing else. Only unary methods are turned into tests; streaming methods are listed with a warning.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from _common import IntakeError, env_name  # noqa: E402

MAX_FILES = 20
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOKENS = 400_000
MAX_DEPTH = 32
SCALARS = {"double", "float", "int32", "int64", "uint32", "uint64", "sint32", "sint64", "fixed32", "fixed64", "sfixed32", "sfixed64",
           "bool", "string", "bytes"}
# Well-known types and how their JSON form looks, so tests can build and check them without the import being present.
WELL_KNOWN = {
    "google.protobuf.Timestamp": "timestamp", "google.protobuf.Duration": "duration", "google.protobuf.Empty": "empty",
    "google.protobuf.Struct": "struct", "google.protobuf.Value": "value", "google.protobuf.ListValue": "list", "google.protobuf.Any": "any",
    "google.protobuf.FieldMask": "fieldmask", "google.protobuf.StringValue": "string", "google.protobuf.BoolValue": "bool",
    "google.protobuf.Int32Value": "int32", "google.protobuf.Int64Value": "int64", "google.protobuf.UInt32Value": "uint32",
    "google.protobuf.UInt64Value": "uint64", "google.protobuf.FloatValue": "float", "google.protobuf.DoubleValue": "double",
    "google.protobuf.BytesValue": "bytes",
}
TOKEN_RE = re.compile(r'''\s+|//[^\n]*|/\*.*?\*/|(?P<str>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|(?P<num>-?(?:0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?|inf|nan))|(?P<id>[A-Za-z_][\w.]*)|(?P<p>[{}()\[\]<>=;,:.\-+/])''', re.S)


def tokenize(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            raise IntakeError(f"proto parse error: unexpected character {text[pos]!r} at offset {pos}")
        pos = m.end()
        for kind in ("str", "num", "id", "p"):
            if m.group(kind) is not None:
                out.append((kind, m.group(kind)))
                break
        if len(out) > MAX_TOKENS:
            raise IntakeError("proto file is too large to read (token limit)")
    return out


class Parser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.t = tokens
        self.i = 0

    def peek(self, kind: str | None = None, value: str | None = None) -> bool:
        if self.i >= len(self.t):
            return False
        k, v = self.t[self.i]
        return (kind is None or k == kind) and (value is None or v == value)

    def next(self) -> tuple[str, str]:
        if self.i >= len(self.t):
            raise IntakeError("proto parse error: unexpected end of file")
        self.i += 1
        return self.t[self.i - 1]

    def accept(self, kind: str, value: str | None = None) -> bool:
        if self.peek(kind, value):
            self.i += 1
            return True
        return False

    def expect(self, kind: str, value: str | None = None) -> str:
        if not self.peek(kind, value):
            got = self.t[self.i][1] if self.i < len(self.t) else "end of file"
            raise IntakeError(f"proto parse error: expected {value or kind}, found {got!r}")
        return self.next()[1]

    def skip_statement(self) -> None:
        """Skip to the end of the current statement or block (used for options and anything not needed)."""
        depth = 0
        while self.i < len(self.t):
            _, v = self.next()
            if v in "{[(" and v != "":
                depth += 1
            elif v in "}])":
                depth -= 1
                if depth <= 0 and v == "}":
                    self.accept("p", ";")
                    return
            elif v == ";" and depth <= 0:
                return

    def skip_options(self) -> None:
        if self.accept("p", "["):
            depth = 1
            while depth and self.i < len(self.t):
                _, v = self.next()
                depth += (v == "[") - (v == "]")


def _type_name(p: Parser) -> str:
    leading = "." if p.accept("p", ".") else ""
    return leading + p.expect("id")


def parse_file(text: str) -> dict:
    """One file -> {syntax, package, imports, messages: {scoped name: msg}, enums, services}. Names are relative to the package."""
    p = Parser(tokenize(text))
    out: dict[str, Any] = {"syntax": "proto2", "package": "", "imports": [], "messages": {}, "enums": {}, "services": []}

    def enum_body(scope: str, name: str) -> None:
        values = []
        p.expect("p", "{")
        while not p.accept("p", "}"):
            if p.peek("id", "option") or p.peek("id", "reserved"):
                p.skip_statement()
                continue
            vname = p.expect("id")
            p.expect("p", "=")
            p.accept("p", "-")
            p.expect("num")
            p.skip_options()
            p.expect("p", ";")
            values.append(vname)
        out["enums"][f"{scope}{name}"] = values

    def fields_into(msg: dict, scope: str, depth: int, oneof: str | None = None) -> None:
        if depth > MAX_DEPTH:
            raise IntakeError("proto file nests messages too deeply")
        while not p.accept("p", "}"):
            if p.peek("id", "message"):
                p.next()
                message(scope, depth + 1)
            elif p.peek("id", "enum"):
                p.next()
                n = p.expect("id")
                enum_body(scope, n)
            elif p.peek("id", "oneof"):
                p.next()
                oname = p.expect("id")
                p.expect("p", "{")
                fields_into(msg, scope, depth + 1, oname)
            elif p.peek("id", "extend") or p.peek("id", "extensions") or p.peek("id", "reserved") or p.peek("id", "option"):
                p.skip_statement()
            elif p.peek("id", "map"):
                p.next()
                p.expect("p", "<")
                key = _type_name(p)
                p.expect("p", ",")
                val = _type_name(p)
                p.expect("p", ">")
                fname = p.expect("id")
                p.expect("p", "=")
                number = _field_number(p.expect("num"))
                p.skip_options()
                p.expect("p", ";")
                msg["fields"].append({"name": fname, "number": number, "type": "map", "key_type": key, "value_type": val,
                                      "repeated": False, "optional": False, "oneof": oneof})
            elif p.peek("id", "group"):
                p.skip_statement()
            else:
                label = None
                if p.peek("id") and p.t[p.i][1] in ("repeated", "optional", "required"):
                    label = p.next()[1]
                ftype = _type_name(p)
                fname = p.expect("id")
                p.expect("p", "=")
                number = _field_number(p.expect("num"))
                p.skip_options()
                p.expect("p", ";")
                msg["fields"].append({"name": fname, "number": number, "type": ftype, "repeated": label == "repeated",
                                      "optional": label in ("optional", "required"), "required": label == "required", "oneof": oneof})
        # a oneof block ends with its own closing brace; the message's closing brace is consumed by the caller's loop

    def message(scope: str, depth: int = 0) -> None:
        name = p.expect("id")
        msg: dict[str, Any] = {"fields": []}
        out["messages"][f"{scope}{name}"] = msg
        p.expect("p", "{")
        fields_into(msg, f"{scope}{name}.", depth)

    while p.i < len(p.t):
        word = p.expect("id")
        if word == "syntax":
            p.expect("p", "=")
            out["syntax"] = p.expect("str").strip("\"'")
            p.expect("p", ";")
        elif word == "package":
            out["package"] = p.expect("id")
            p.expect("p", ";")
        elif word == "import":
            p.accept("id", "public")
            p.accept("id", "weak")
            out["imports"].append(p.expect("str").strip("\"'"))
            p.expect("p", ";")
        elif word == "message":
            message("")
        elif word == "enum":
            n = p.expect("id")
            enum_body("", n)
        elif word == "service":
            sname = p.expect("id")
            methods = []
            p.expect("p", "{")
            while not p.accept("p", "}"):
                if not p.accept("id", "rpc"):
                    p.skip_statement()
                    continue
                mname = p.expect("id")
                p.expect("p", "(")
                cstream = p.accept("id", "stream")
                req = _type_name(p)
                p.expect("p", ")")
                p.expect("id", "returns")
                p.expect("p", "(")
                sstream = p.accept("id", "stream")
                resp = _type_name(p)
                p.expect("p", ")")
                if p.accept("p", "{"):
                    while not p.accept("p", "}"):
                        p.skip_statement()
                else:
                    p.expect("p", ";")
                methods.append({"name": mname, "request": req, "response": resp, "client_streaming": cstream, "server_streaming": sstream})
            out["services"].append({"name": sname, "methods": methods})
        elif word in ("option", "extend"):
            p.skip_statement()
        else:
            raise IntakeError(f"proto parse error: unexpected {word!r} at top level")
    return out


def _field_number(token: str) -> int:
    try:
        number = int(token, 0)
    except ValueError:
        raise IntakeError(f"field number must be an integer, found {token[:20]!r}") from None
    if not 1 <= number <= 536870911:
        raise IntakeError(f"field number {number} is outside 1 to 536870911")
    return number


def _read(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise IntakeError(f"{path.name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise IntakeError(f"{path.name} is not a UTF-8 text file") from None


def load_tree(root_file: Path, warnings: list[str]) -> list[tuple[str, dict]]:
    """The root file plus the files it imports from its own folder: [(file name, parsed)]. Everything else is skipped with a warning."""
    root_dir = root_file.resolve().parent
    seen: dict[Path, dict] = {}
    order: list[Path] = []
    queue = [root_file.resolve()]
    while queue:
        cur = queue.pop(0)
        if cur in seen:
            continue
        if len(seen) >= MAX_FILES:
            warnings.append(f"more than {MAX_FILES} proto files: the rest were not read")
            break
        parsed = parse_file(_read(cur))
        seen[cur] = parsed
        order.append(cur)
        for imp in parsed["imports"]:
            if imp.startswith("google/protobuf/"):
                continue
            target = (root_dir / imp)
            if imp.startswith("/") or ".." in Path(imp).parts or not target.is_file() or target.is_symlink() or root_dir not in target.resolve().parents:
                warnings.append(f"import {imp!r} was not read (only files below the proto's own folder are followed)")
                continue
            queue.append(target.resolve())
    return [(f.name if f == root_file.resolve() else str(f.relative_to(root_dir)), seen[f]) for f in order]


def _resolve(name: str, scope: str, package: str, messages: dict, enums: dict) -> tuple[str, str] | None:
    """(full name, 'message'|'enum'|<well-known kind>) for a type name used inside `scope` (a full message name), or None."""
    if name.startswith("."):
        cand = name[1:]
        if cand in messages:
            return cand, "message"
        if cand in enums:
            return cand, "enum"
        return (cand, WELL_KNOWN[cand]) if cand in WELL_KNOWN else None
    parts = scope.split(".") if scope else []
    for cut in range(len(parts), -1, -1):
        cand = ".".join(parts[:cut] + [name])
        if cand in messages:
            return cand, "message"
        if cand in enums:
            return cand, "enum"
    if name in WELL_KNOWN:
        return name, WELL_KNOWN[name]
    return None


def normalize(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        raise IntakeError(f"File not found: {path}")
    warnings: list[str] = []
    files = load_tree(p, warnings)
    sources = {name: _read(p.resolve().parent / name) for name, _ in files}   # kept in the intake so a suite can carry its own copy
    package = files[0][1]["package"]
    prefix = f"{package}." if package else ""
    messages: dict[str, dict] = {}
    enums: dict[str, list[str]] = {}
    raw_services = []
    syntax = files[0][1]["syntax"]
    for _, parsed in files:
        pre = f"{parsed['package']}." if parsed["package"] else ""
        for n, m in parsed["messages"].items():
            messages[pre + n] = m
        for n, v in parsed["enums"].items():
            enums[pre + n] = v
        if parsed is files[0][1]:
            raw_services = parsed["services"]
    if not raw_services:
        raise IntakeError("No service found in the proto file (expected `service Name { rpc Method(Req) returns (Resp); }`).")

    def kind_of(ftype: str, scope: str) -> tuple[str, str]:
        if ftype in SCALARS:
            return ftype, "scalar"
        hit = _resolve(ftype, scope, package, messages, enums)
        if hit is None:
            warnings.append(f"type {ftype!r} is not defined in the files read; treated as an empty message")
            return ftype, "unknown"
        return hit

    msg_out: dict[str, dict] = {}
    for full, m in messages.items():
        fields = []
        for f in m["fields"]:
            jn = re.sub(r"_([a-z0-9])", lambda mm: mm.group(1).upper(), f["name"])
            entry: dict[str, Any] = {"name": f["name"], "json_name": jn, "number": f["number"], "repeated": f["repeated"],
                                     "optional": f["optional"] or f.get("oneof") is not None, "required": bool(f.get("required")),
                                     "oneof": f.get("oneof")}
            if f["type"] == "map":
                entry["kind"] = "map"
                entry["key_type"] = f["key_type"] if f["key_type"] in SCALARS else "string"
                vt, vk = kind_of(f["value_type"], full)
                entry["type"], entry["value_kind"] = vt, vk
            else:
                entry["type"], entry["kind"] = kind_of(f["type"], full)
            fields.append(entry)
        msg_out[full] = {"fields": fields}
    methods_out = []
    services_out = []
    for s in raw_services:
        full_service = prefix + s["name"]
        ms = []
        for m in s["methods"]:
            req = _resolve(m["request"], package, package, messages, enums)
            resp = _resolve(m["response"], package, package, messages, enums)
            streaming = m["client_streaming"] or m["server_streaming"]
            entry = {"id": f"{full_service}/{m['name']}", "name": m["name"], "service": full_service,
                     "request": req[0] if req else m["request"], "response": resp[0] if resp else m["response"],
                     "request_kind": req[1] if req else "unknown", "response_kind": resp[1] if resp else "unknown",
                     "client_streaming": m["client_streaming"], "server_streaming": m["server_streaming"]}
            if streaming:
                warnings.append(f"{entry['id']} is a streaming method: listed but not tested (unary only)")
            ms.append(entry)
            methods_out.append(entry)
        services_out.append({"name": full_service, "methods": [m["id"] for m in ms]})
    warnings.append("gRPC has no declared authentication: a bearer token from APITEST_BEARERAUTH_TOKEN is sent as an `authorization` header when set. "
                    "Tell me if the service uses another scheme.")
    warnings.append("Set APITEST_BASE_URL to http://host:port for plaintext gRPC or https://host:port for TLS.")
    endpoint = {"id": "grpc", "method": "POST", "path": "/", "summary": "gRPC target", "tags": ["grpc"], "deprecated": False,
                "auth_required": False, "security": [], "parameters": [], "responses": {}}
    return {
        "source": {"type": "grpc", "format": syntax, "file": p.name}, "info": {"title": f"gRPC {package or p.stem}", "version": ""},
        "servers": [], "security_schemes": {"bearerAuth": {"type": "http", "scheme": "bearer", "env": [env_name("bearerAuth", "TOKEN")]}},
        "endpoints": [endpoint],
        "grpc": {"package": package, "proto_file": p.name, "services": services_out, "methods": methods_out,
                 "messages": msg_out, "enums": enums, "files": [n for n, _ in files], "sources": sources},
        "warnings": warnings,
    }


def build(path: str) -> dict:
    return normalize(path)


def summarize_grpc(doc: dict) -> str:
    g = doc["grpc"]
    rows = [f"Source: grpc ({doc['source']['format']})", f"Package: {g['package'] or '(none)'}   Files: {', '.join(g['files'])}",
            f"Methods: {len(g['methods'])}   Messages: {len(g['messages'])}   Enums: {len(g['enums'])}", ""]
    for m in g["methods"]:
        stream = " [streaming: not tested]" if m["client_streaming"] or m["server_streaming"] else ""
        rows.append(f"{m['id']}  {m['request']} -> {m['response']}{stream}")
    rows += [f"WARNING: {w}" for w in doc["warnings"]]
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="parse_proto.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args(argv)
    try:
        doc = build(args.input)
    except IntakeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.summary:
        print(summarize_grpc(doc))
    text = json.dumps(doc, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    elif not args.summary:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
