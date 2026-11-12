#!/usr/bin/env python3
"""Detect breaking changes between two normalized intake files (old API vs new API).

Usage:  python spec_diff.py old.json new.json [--format text|json|markdown] [-o report] [--fail-on breaking|any|never]

Works on intake JSON (see references/intake.md), so OpenAPI, Postman and GraphQL-over-HTTP inputs
are all comparable. Offline, stdlib only. Exit code 1 when --fail-on matches (default: breaking).

Breaking (a client built for `old` can fail against `new`):
  removed endpoint/method; removed or renamed response field that was required or documented;
  response field type change; removed success status; new required parameter/body field;
  parameter/body field type change or tightened constraint; enum value removed from a request,
  or added to a response; auth newly required.
Non-breaking (reported as info): new endpoints, new optional params, new response fields, relaxed constraints.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BREAKING, INFO = "breaking", "info"


def _key(ep: dict) -> str:
    return f"{ep['method']} {ep['path']}"


def _props(schema: Any) -> dict:
    return schema.get("properties") or {} if isinstance(schema, dict) else {}


def _type(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return None
    t = schema.get("type")
    return tuple(sorted(t)) if isinstance(t, list) else t


def _walk_response(old: Any, new: Any, where: str, out: list) -> None:
    """Response direction: removed fields and type changes break clients; new fields do not."""
    if not isinstance(old, dict) or not isinstance(new, dict) or "$circular" in old or "$circular" in new:
        return
    if _type(old) and _type(new) and _type(old) != _type(new):
        out.append((BREAKING, where, f"response type changed {_type(old)} -> {_type(new)}"))
        return
    if old.get("type") == "array":
        _walk_response(old.get("items"), new.get("items"), where + "[]", out)
    op, np_ = _props(old), _props(new)
    for name, sub in op.items():
        if name not in np_:
            out.append((BREAKING, f"{where}.{name}", "response field removed"))
        else:
            _walk_response(sub, np_[name], f"{where}.{name}", out)
    for name in np_:
        if name not in op:
            out.append((INFO, f"{where}.{name}", "response field added"))
    if old.get("enum") and new.get("enum"):
        added = [v for v in new["enum"] if v not in old["enum"]]
        if added:
            out.append((BREAKING, where, f"response enum gained values {added} (strict clients may fail)"))
    if old.get("nullable") is not True and new.get("nullable") is True:
        out.append((BREAKING, where, "response field became nullable"))


def _walk_request(old: Any, new: Any, where: str, out: list) -> None:
    """Request direction: new required fields, tighter constraints, removed enum values break clients."""
    if not isinstance(old, dict) or not isinstance(new, dict) or "$circular" in old or "$circular" in new:
        return
    if _type(old) and _type(new) and _type(old) != _type(new):
        out.append((BREAKING, where, f"request type changed {_type(old)} -> {_type(new)}"))
        return
    op, np_ = _props(old), _props(new)
    old_req, new_req = set(old.get("required") or []), set(new.get("required") or [])
    for name in sorted(new_req - old_req):
        out.append((BREAKING, f"{where}.{name}", "field is now required"))
    for name in op:
        if name not in np_:
            out.append((BREAKING, f"{where}.{name}", "request field removed (clients still sending it may be rejected)"))
        else:
            _walk_request(op[name], np_[name], f"{where}.{name}", out)
    for name in np_:
        if name not in op and name not in new_req:
            out.append((INFO, f"{where}.{name}", "optional request field added"))
    if old.get("enum") and new.get("enum"):
        gone = [v for v in old["enum"] if v not in new["enum"]]
        if gone:
            out.append((BREAKING, where, f"request enum lost values {gone}"))
    for k, tighter in (("minimum", lambda a, b: b > a), ("maxLength", lambda a, b: b < a),
                       ("maximum", lambda a, b: b < a), ("minLength", lambda a, b: b > a)):
        a, b = old.get(k), new.get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and tighter(a, b):
            out.append((BREAKING, where, f"{k} tightened {a} -> {b}"))
        elif a is None and isinstance(b, (int, float)):
            out.append((BREAKING, where, f"new constraint {k}={b}"))
    if old.get("items") and new.get("items"):
        _walk_request(old["items"], new["items"], where + "[]", out)


def diff(old: dict, new: dict) -> list[dict]:
    out: list[tuple] = []
    oe, ne = {_key(e): e for e in old.get("endpoints", [])}, {_key(e): e for e in new.get("endpoints", [])}
    for k in oe:
        if k not in ne:
            out.append((BREAKING, k, "endpoint removed"))
    for k in ne:
        if k not in oe:
            out.append((INFO, k, "endpoint added"))
    for k in sorted(set(oe) & set(ne)):
        a, b = oe[k], ne[k]
        if not a.get("deprecated") and b.get("deprecated"):
            out.append((INFO, k, "endpoint deprecated"))
        if not a.get("auth_required") and b.get("auth_required"):
            out.append((BREAKING, k, "authentication is now required"))
        pa = {(p["name"], p["in"]): p for p in a.get("parameters", [])}
        pb = {(p["name"], p["in"]): p for p in b.get("parameters", [])}
        for pk, p in pb.items():
            if pk not in pa:
                out.append((BREAKING if p.get("required") else INFO, f"{k} {pk[1]} param '{pk[0]}'",
                            "new required parameter" if p.get("required") else "new optional parameter"))
            else:
                if not pa[pk].get("required") and p.get("required"):
                    out.append((BREAKING, f"{k} {pk[1]} param '{pk[0]}'", "parameter is now required"))
                _walk_request(pa[pk].get("schema"), p.get("schema"), f"{k} {pk[1]} param '{pk[0]}'", out)
        for pk in pa:
            if pk not in pb:
                out.append((INFO, f"{k} {pk[1]} param '{pk[0]}'", "parameter removed (clients sending it may still work)"))
        ra, rb = a.get("request_body"), b.get("request_body")
        if ra and rb:
            if not ra.get("required") and rb.get("required"):
                out.append((BREAKING, f"{k} request body", "request body is now required"))
            _walk_request(ra.get("schema"), rb.get("schema"), f"{k} request", out)
        elif rb and rb.get("required") and not ra:
            out.append((BREAKING, f"{k} request body", "request body is now required"))
        for code, spec in a.get("responses", {}).items():
            if code not in b.get("responses", {}):
                if code.startswith("2"):
                    out.append((BREAKING, f"{k} response {code}", "success status removed"))
                continue
            _walk_response(spec.get("schema"), b["responses"][code].get("schema"), f"{k} response {code}", out)
        for code in b.get("responses", {}):
            if code not in a.get("responses", {}) and code.startswith("2"):
                out.append((INFO, f"{k} response {code}", "success status added"))
    out += _diff_graphql(old.get("graphql"), new.get("graphql"))
    return [{"severity": s, "where": w, "change": c} for s, w, c in out]


def _diff_graphql(old: dict | None, new: dict | None) -> list[tuple]:
    """GraphQL: removed operations/fields, new required arguments and changed types break existing queries."""
    if not old or not new:
        return []
    out: list[tuple] = []
    oo, no = {o["id"]: o for o in old["operations"]}, {o["id"]: o for o in new["operations"]}
    for k, o in oo.items():
        n = no.get(k)
        if n is None:
            out.append((BREAKING, k, "operation removed"))
            continue
        oa, na = {a["name"]: a for a in o["args"]}, {a["name"]: a for a in n["args"]}
        for name, a in na.items():
            if name not in oa:
                out.append((BREAKING if a["required"] else INFO, f"{k}({name})",
                            "new required argument" if a["required"] else "new optional argument"))
            elif oa[name]["type"] != a["type"]:
                out.append((BREAKING, f"{k}({name})", f"argument type changed {oa[name]['type']} -> {a['type']}"))
        for name in oa:
            if name not in na:
                out.append((BREAKING, f"{k}({name})", "argument removed (queries passing it fail validation)"))
        if o["returns"] != n["returns"]:
            out.append((BREAKING, k, f"return type changed {o['returns']} -> {n['returns']}"))
    for k in no:
        if k not in oo:
            out.append((INFO, k, "operation added"))
    for tname, t in old["types"].items():
        nt = new["types"].get(tname)
        if nt is None:
            out.append((BREAKING, f"type {tname}", "type removed"))
            continue
        if t["kind"] == "ENUM":
            gone = [v for v in t.get("values", []) if v not in nt.get("values", [])]
            if gone:
                out.append((BREAKING, f"enum {tname}", f"values removed {gone}"))
            continue
        nf = {f["name"]: f for f in nt.get("fields", [])}
        for f in t.get("fields", []):
            if f["name"] not in nf:
                out.append((BREAKING, f"{tname}.{f['name']}", "field removed"))
            elif f["type"] != nf[f["name"]]["type"]:
                out.append((BREAKING, f"{tname}.{f['name']}", f"field type changed {f['type']} -> {nf[f['name']]['type']}"))
        if t["kind"] == "INPUT_OBJECT":
            for name, f in nf.items():
                if f.get("required") and name not in {x["name"] for x in t.get("fields", [])}:
                    out.append((BREAKING, f"{tname}.{name}", "new required input field"))
    return out


def render(findings: list[dict], fmt: str, old_path: str, new_path: str) -> str:
    n_break = sum(f["severity"] == BREAKING for f in findings)
    if fmt == "json":
        return json.dumps({"old": old_path, "new": new_path, "breaking": n_break,
                           "total": len(findings), "changes": findings}, indent=2)
    rows = [f"Compared {old_path} -> {new_path}: {n_break} breaking, {len(findings) - n_break} non-breaking"]
    if fmt == "markdown":
        rows = [f"# API diff: {n_break} breaking change(s)", "", f"`{old_path}` -> `{new_path}`", "",
                "| Severity | Where | Change |", "|---|---|---|"]
        rows += [f"| {f['severity']} | {f['where']} | {f['change']} |" for f in findings]
        return "\n".join(rows)
    for f in sorted(findings, key=lambda f: f["severity"] != BREAKING):
        rows.append(f"[{f['severity'].upper():8}] {f['where']}: {f['change']}")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="spec_diff.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    ap.add_argument("-o", "--output")
    ap.add_argument("--fail-on", choices=("breaking", "any", "never"), default="breaking")
    args = ap.parse_args(argv)
    try:
        old, new = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (args.old, args.new))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    findings = diff(old, new)
    text = render(findings, args.format, args.old, args.new)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(text)
    breaking = any(f["severity"] == BREAKING for f in findings)
    return 1 if (args.fail_on == "any" and findings) or (args.fail_on == "breaking" and breaking) else 0


if __name__ == "__main__":
    sys.exit(main())
