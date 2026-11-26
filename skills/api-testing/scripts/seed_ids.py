#!/usr/bin/env python3
"""Optional setup stage: create one record per resource type and write their ids to an ids file. Stdlib only.

Usage:
  python seed_ids.py intake.json --out ids.json [--base-url URL] [--dry-run]
  python seed_ids.py intake.json --cleanup ids.json [--base-url URL]

Most per-object and BOLA tests skip with "no existing id" until APITEST_PATH_<NAME> (or the ids file) holds a real id. This
script makes those ids: for every resource that has a create endpoint (POST /pets) and an item endpoint (GET /pets/{petId})
it sends one create request and records the id under "path" in the ids file, the same file the suite reads through
APITEST_IDS_FILE. It never runs on its own; run_all.py and the generated suites do not call it.

Rules:
  * It writes data, so the same gate as the intrusive runs applies: production is refused, a target that is not loopback needs
    APITEST_TARGET_ENV set to a non-production value and APITEST_AUTHORIZED=yes (set by you, never by this script), and
    APITEST_READ_ONLY=yes refuses it. --dry-run sends nothing and prints the plan.
  * An id you already supplied (APITEST_PATH_<NAME> or an entry in the ids file) is kept and no record is made for it.
  * Credentials come from the environment variables the intake names. Nothing secret is printed: output holds operation ids,
    status codes and ids only, never request or response bodies.
  * What it creates is listed under "created" in the ids file. `--cleanup` deletes those records (newest first) through the
    resource's DELETE endpoint and removes them from the file; a record with no DELETE endpoint is listed for you to remove by hand.
  * One create per resource, at most 25 resources. Nested resources are created when the ids of their parents are in the
    environment, the ids file or were just created.

Exit codes: 0 done, 1 at least one record could not be created or removed, 2 refused or bad input.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gen_common  # noqa: E402
import safety_gate  # noqa: E402
from _common import IntakeError, load_intake_json  # noqa: E402
from gen_integration import build_resources  # noqa: E402

MAX_RESOURCES = 25
TIMEOUT = 15.0


class Refused(Exception):
    """The run must not start (gate, read-only, bad input)."""


def env_key(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()


def gate(base_url: str) -> None:
    if safety_gate.env_true("APITEST_READ_ONLY"):
        raise Refused("APITEST_READ_ONLY=yes: this stage writes records, so it does not run in read-only mode")
    reason = safety_gate.gate_reason(base_url)
    if reason:
        raise Refused(reason)


# --------------------------------------------------------------------------- ids file
def read_ids(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused(f"{path} is not a readable JSON file; fix or remove it first") from None
    if not isinstance(data, dict):
        raise Refused(f"{path} must hold a JSON object")
    return data


def write_ids(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def known_id(name: str, ids: dict) -> str | None:
    val = os.environ.get("APITEST_PATH_" + env_key(name))
    if val:
        return val
    section = ids.get("path")
    val = section.get(name) if isinstance(section, dict) else None
    return str(val) if val not in (None, "") else None


# --------------------------------------------------------------------------- http
class Client:
    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        host = safety_gate.target_host(self.base)
        handlers = [urllib.request.ProxyHandler({})] if safety_gate.is_local_host(host) else []
        self.opener = urllib.request.build_opener(*handlers)

    def send(self, method: str, path: str, headers: dict, query: dict, body=None) -> tuple[int, bytes, dict]:
        url = self.base + path + (("?" + urllib.parse.urlencode(query)) if query else "")
        data = None
        hdrs = dict(headers)
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with self.opener.open(req, timeout=TIMEOUT) as resp:
                return resp.status, resp.read(1_000_000), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(1_000_000), dict(exc.headers)
        except (urllib.error.URLError, OSError, TimeoutError):
            return 0, b"", {}


def credentials(doc: dict, ep: dict) -> tuple[dict, dict] | None:
    """Headers and query for the first security alternative whose environment variables are all set, else None.
    A public operation needs none."""
    alts = ep.get("security") or []
    if not alts:
        return {}, {}
    schemes = doc.get("security_schemes") or {}
    for alt in alts:
        headers: dict = {}
        query: dict = {}
        ok = True
        for name in alt:
            sch = schemes.get(name) or {}
            vals = [os.environ.get(v) for v in sch.get("env") or []]
            if not vals or any(not v for v in vals):
                ok = False
                break
            kind = (sch.get("scheme") or "").lower()
            if sch.get("type") == "http" and kind == "basic":
                pair = f"{vals[0]}:{vals[1] if len(vals) > 1 else ''}"
                headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
            elif sch.get("type") == "apiKey":
                {"header": headers, "query": query}.get(sch.get("in", "header"), headers)[sch.get("name", "X-API-Key")] = vals[0]
            else:
                headers["Authorization"] = f"{(sch.get('scheme') or 'bearer').capitalize()} {vals[0]}"
        if ok:
            return headers, query
    return None


def missing_credentials(doc: dict, ep: dict) -> list[str]:
    names: list[str] = []
    for alt in (ep.get("security") or [])[:1]:
        for name in alt:
            names += (doc.get("security_schemes") or {}).get(name, {}).get("env") or []
    return names or ["(no credential variable known)"]


def extract_id(status: int, body: bytes, headers: dict, name: str):
    try:
        data = json.loads(body) if body else None
    except (ValueError, RecursionError):
        data = None
    for scope in (data, data.get("data") if isinstance(data, dict) else None, data.get("result") if isinstance(data, dict) else None):
        if isinstance(scope, dict):
            for key in (name, "id", "_id", "uuid", "Id", "ID"):
                if scope.get(key) not in (None, "") and not isinstance(scope[key], (dict, list, bool)):
                    return scope[key]
    loc = next((v for k, v in headers.items() if k.lower() == "location"), None)
    if loc:
        return urllib.parse.urlsplit(loc).path.rstrip("/").rsplit("/", 1)[-1] or None
    return None


# --------------------------------------------------------------------------- body
def examples_for(ep: dict) -> dict:
    """APITEST_EXAMPLES_FILE entries for this operation ({"createPet.name": "x", "*.state": "27"}), the same file the suite reads."""
    path = os.environ.get("APITEST_EXAMPLES_FILE", "").strip()
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused("APITEST_EXAMPLES_FILE must name a readable JSON file") from None
    out = {}
    for key, val in (data.items() if isinstance(data, dict) else []):
        op, _, field = str(key).partition(".")
        if field and op in ("*", ep["id"]):
            out[field] = val
    return out


def tag(value: str, max_len: int | None) -> str:
    suffix = secrets.token_hex(3)
    if "@" in value:
        local, _, domain = value.partition("@")
        return f"{local}-{suffix}@{domain}"
    out = f"{value}-{suffix}"
    return out if max_len is None or len(out) <= max_len else value[: max(1, max_len - 7)] + "-" + suffix


def required_params(ep: dict) -> tuple[dict, dict]:
    """Required header and query parameters the spec declares (Idempotency-Key and the like). Without them a create
    endpoint answers 400 and no id is recorded. An Idempotency-Key gets a fresh value so a rerun is not a replay."""
    base = gen_common.baseline(ep)
    headers = {k: (str(uuid.uuid4()) if k.lower() == "idempotency-key" else str(v)) for k, v in base["headers"].items()}
    return headers, {k: str(v) for k, v in base["query"].items()}


def create_body(ep: dict):
    body = gen_common.sample_body(ep)
    if not isinstance(body, dict):
        return body
    body = dict(body)
    body.update(examples_for(ep))
    for name, meta in gen_common.unique_body_fields(ep).items():
        if isinstance(body.get(name), str) and body[name]:
            body[name] = tag(body[name], meta.get("max"))
    return body


# --------------------------------------------------------------------------- plan and run
def fill(path: str, values: dict) -> str | None:
    """Path with its {params} filled in, or None when one has no value."""
    names = re.findall(r"\{([^}/]+)\}", path)
    if any(n not in values for n in names):
        return None
    return re.sub(r"\{([^}/]+)\}", lambda m: urllib.parse.quote(str(values[m.group(1)]), safe=""), path)


def plan(doc: dict, ids: dict) -> tuple[list[dict], list[str]]:
    """Resources to create (a create endpoint, an item endpoint with one id param) and the reasons the others are left out."""
    todo, notes = [], []
    for coll, r in sorted(build_resources(doc).items()):
        param = r.get("param")
        if not r.get("create") or not param or not (r.get("read") or r.get("delete")):
            continue
        if known_id(param, ids):
            notes.append(f"{param}: already set, no record made")
            continue
        if not gen_common.has_json_body(r["create"]) and r["create"].get("request_body"):
            notes.append(f"{param}: {r['create']['id']} does not take a JSON body")
            continue
        todo.append(r)
    if len(todo) > MAX_RESOURCES:
        notes.append(f"only the first {MAX_RESOURCES} of {len(todo)} resources are seeded")
        todo = todo[:MAX_RESOURCES]
    return todo, notes


def seed(doc: dict, ids: dict, client: Client | None, dry: bool) -> tuple[dict, int]:
    todo, notes = plan(doc, ids)
    created = list(ids.get("created") or [])
    path_ids = dict(ids.get("path") or {})
    values = {**{n: known_id(n, ids) for n in path_ids}, **path_ids}
    failures = 0
    pending = list(todo)
    for _ in range(2):   # a second pass lets a child follow a parent that was created in the first
        later = []
        for r in pending:
            ep, param = r["create"], r["param"]
            for p in gen_common.path_params(ep):
                if p["name"] not in values:
                    env_val = known_id(p["name"], ids)
                    if env_val:
                        values[p["name"]] = env_val
            target = fill(ep["path"], values)
            if target is None:
                later.append(r)
                continue
            if dry:
                print(f"would create  {param:<24} {ep['method']} {ep['path']} ({ep['id']})")
                continue
            creds = credentials(doc, ep)
            if creds is None:
                print(f"skipped       {param}: set {', '.join(missing_credentials(doc, ep))}")
                failures += 1
                continue
            extra_h, extra_q = required_params(ep)
            status, raw, hdrs = client.send(ep["method"], target, {**extra_h, **creds[0]}, {**extra_q, **creds[1]}, create_body(ep))
            new_id = extract_id(status, raw, hdrs, param) if 200 <= status < 300 else None
            if new_id is None:
                why = "the server could not be reached" if status == 0 else f"{ep['id']} answered {status}" + (
                    "" if not 200 <= status < 300 else " with no id in the response")
                print(f"not created   {param}: {why}; set APITEST_PATH_{env_key(param)} to an id you know")
                failures += 1
                continue
            values[param] = str(new_id)
            path_ids[param] = str(new_id)
            delete = r.get("delete")
            created.append({"param": param, "id": str(new_id), "operation": ep["id"],
                            "delete": delete["path"] if delete else None, "delete_op": delete["id"] if delete else None,
                            "parents": {p["name"]: values[p["name"]] for p in gen_common.path_params(ep) if p["name"] in values}})
            print(f"created       {param:<24} {new_id}  ({ep['id']} -> {status})")
        pending = later
    for r in pending:
        missing = [p["name"] for p in gen_common.path_params(r["create"]) if p["name"] not in values]
        print(f"skipped       {r['param']}: its create path needs {', '.join(missing)} (set APITEST_PATH_{env_key(missing[0])} or put it in the ids file)")
    for n in notes:
        print(f"note          {n}")
    out = dict(ids)
    if path_ids:
        out["path"] = path_ids
    if created:
        out["created"] = created
    return out, failures


def cleanup(doc: dict | None, ids: dict, client: Client) -> tuple[dict, int]:
    created = list(ids.get("created") or [])
    failures = 0
    remaining = []
    path_ids = dict(ids.get("path") or {})
    for rec in reversed(created):
        if not rec.get("delete"):
            print(f"left          {rec['param']} {rec['id']}: no DELETE endpoint, remove it by hand")
            remaining.append(rec)
            continue
        values = {**(rec.get("parents") or {}), rec["param"]: rec["id"]}
        target = fill(rec["delete"], values)
        creds = credentials(doc, {"security": doc_security(doc, rec)}) if doc else ({}, {})
        if target is None or creds is None:
            print(f"left          {rec['param']} {rec['id']}: cannot build the delete call (credentials or parent ids missing)")
            remaining.append(rec)
            failures += 1
            continue
        status, _, _ = client.send("DELETE", target, creds[0], creds[1])
        if 200 <= status < 300 or status == 404:
            print(f"deleted       {rec['param']:<24} {rec['id']}  ({status})")
            if path_ids.get(rec["param"]) == rec["id"]:
                del path_ids[rec["param"]]
        else:
            print(f"left          {rec['param']} {rec['id']}: delete answered {status}" if status else
                  f"left          {rec['param']} {rec['id']}: the server could not be reached")
            remaining.append(rec)
            failures += 1
    out = dict(ids)
    for key, val in (("path", path_ids), ("created", list(reversed(remaining)))):
        if val:
            out[key] = val
        else:
            out.pop(key, None)
    return out, failures


def doc_security(doc: dict | None, rec: dict) -> list:
    if not doc:
        return []
    ep = next((e for e in doc["endpoints"] if e["id"] == rec.get("delete_op")), None)
    return (ep or {}).get("security") or []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Create one record per resource type and write their ids to an ids file.")
    ap.add_argument("intake", nargs="?", help="intake.json from intake.py (with --cleanup it supplies the credentials the delete calls need)")
    ap.add_argument("--out", default="ids.json", help="ids file to write or extend (default ids.json)")
    ap.add_argument("--base-url", default=os.environ.get("APITEST_BASE_URL"), help="target (default APITEST_BASE_URL, else the first server in the intake)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be created and send nothing")
    ap.add_argument("--cleanup", metavar="IDS_FILE", help="delete the records listed under 'created' in this ids file")
    args = ap.parse_args(argv)
    try:
        if args.cleanup:
            doc = load_intake_json(args.intake) if args.intake else None
            ids_path = Path(args.cleanup)
            base = args.base_url or ((doc or {}).get("servers") or [None])[0]
            if not base:
                raise Refused("no target: pass --base-url or set APITEST_BASE_URL")
            gate(base)
            ids = read_ids(ids_path)
            out, failures = cleanup(doc, ids, Client(base))
            write_ids(ids_path, out)
            return 1 if failures else 0
        if not args.intake:
            ap.error("give an intake.json, or --cleanup IDS_FILE")
        doc = load_intake_json(args.intake)
        base = args.base_url or (doc.get("servers") or [None])[0]
        if not base:
            raise Refused("no target: pass --base-url or set APITEST_BASE_URL")
        gate(base)
        out_path = Path(args.out)
        ids = read_ids(out_path)
        out, failures = seed(doc, ids, None if args.dry_run else Client(base), args.dry_run)
        if not args.dry_run:
            write_ids(out_path, out)
            print(f"\nids written to {out_path}. Run the suite with:  APITEST_IDS_FILE={out_path} pytest <suite dir>")
            print(f"Remove what was created afterwards with:  python {Path(__file__).name} --cleanup {out_path} {args.intake}")
        return 1 if failures else 0
    except (Refused, IntakeError) as exc:
        print(f"seed_ids: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
