#!/usr/bin/env python3
"""Parse a Postman collection (v2.0/2.1 JSON) into the same normalized JSON as parse_openapi.py.

Usage:  python parse_postman.py collection.json [-o intake.json] [--summary]

Secrets: auth values and secret-looking headers/body fields are never emitted. Auth becomes a
security scheme with env-var names (APITEST_<SCHEME>_TOKEN etc.).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    SECRET_HEADERS, IntakeError, cli_main, env_name, is_secret_name, load_document, redact_example,
    slug, strip_userinfo, unique_ids,
)


def _walk(items: list, trail: list[str]):
    for it in items or []:
        if "item" in it:
            yield from _walk(it["item"], trail + [it.get("name", "")])
        elif "request" in it:
            yield it, trail


def _url_parts(url: Any) -> tuple[str, str, list[dict]]:
    """Return (origin or '', path, query params)."""
    if isinstance(url, str):
        raw, query = url, []
    else:
        raw = (url or {}).get("raw", "")
        query = [{"name": q.get("key", ""), "example": q.get("value")} for q in (url or {}).get("query", []) if not q.get("disabled")]
        if not raw:
            host = ".".join(url.get("host", [])) if isinstance(url.get("host"), list) else url.get("host", "")
            raw = f"{url.get('protocol', 'https')}://{host}/" + "/".join(url.get("path", []))
    raw, _ = strip_userinfo(raw.split("#")[0])
    for q in query:
        if is_secret_name(q["name"]):
            q["example"] = "<redacted>"
    base, _, qs = raw.partition("?")
    if not query and qs:
        query = [{"name": kv.split("=")[0], "example": "<redacted>" if is_secret_name(kv.split("=")[0]) else kv.partition("=")[2]} for kv in qs.split("&") if kv]
    m = re.match(r"^((?:[a-z]+://)?\{\{[^}]+\}\}|[a-z]+://[^/]+|\{\{[^}]+\}\})(/.*)?$", base)
    if m:
        origin, path = m.group(1), m.group(2) or "/"
    else:
        origin, path = "", base if base.startswith("/") else "/" + base
    path = re.sub(r"/:([A-Za-z_][\w-]*)", r"/{\1}", path)
    return origin, path, query


def _auth(a: dict | None, schemes: dict) -> list[list[str]]:
    if not a or a.get("type") in (None, "noauth"):
        return []
    t = a["type"]
    if t == "bearer":
        name, info = "bearerAuth", {"type": "http", "scheme": "bearer", "env": [env_name("bearerAuth", "TOKEN")]}
    elif t == "basic":
        name, info = "basicAuth", {"type": "http", "scheme": "basic",
                                   "env": [env_name("basicAuth", "USER"), env_name("basicAuth", "PASS")]}
    elif t == "apikey":
        kv = {x.get("key"): x.get("value") for x in a.get("apikey", [])}
        name = "apiKeyAuth"
        info = {"type": "apiKey", "in": kv.get("in", "header"), "name": "(see collection)",
                "env": [env_name("apiKeyAuth", "KEY")]}
        # the apikey 'key' entry is the header NAME, 'value' is the secret; keep the name only
        for x in a.get("apikey", []):
            if x.get("key") == "key" and isinstance(x.get("value"), str):
                info["name"] = x["value"]
    else:
        name, info = f"{t}Auth", {"type": t, "env": [env_name(f"{t}Auth", "TOKEN")]}
    schemes.setdefault(name, info)
    return [[name]]


def parse(doc: Any, filename: str = "") -> dict:
    if not isinstance(doc, dict) or "item" not in doc or "info" not in doc:
        raise IntakeError("Not a Postman collection (expected 'info' and 'item').")
    schema_url = str(doc["info"].get("schema", ""))
    if "v1" in schema_url or "collection/v1" in schema_url:
        raise IntakeError("Postman v1 collections are unsupported. Export as Collection v2.1.")

    warnings: list[str] = []
    schemes: dict[str, dict] = {}
    coll_auth = _auth(doc.get("auth"), schemes)
    origins: list[str] = []
    endpoints: list[dict] = []

    for item, trail in _walk(doc["item"], []):
        req = item["request"]
        if isinstance(req, str):
            req = {"method": "GET", "url": req}
        method = req.get("method", "GET").upper()
        origin, path, query = _url_parts(req.get("url"))
        if "@" in json.dumps(req.get("url") or "") and strip_userinfo(json.dumps(req.get("url")))[1]:
            warnings.append("Credentials embedded in a URL were stripped; use env vars for auth.")
        if origin and origin not in origins:
            origins.append(origin)

        params = [{"name": m, "in": "path", "required": True, "schema": {"type": "string"}} for m in re.findall(r"(?<!\{)\{([^{}/]+)\}(?!\})", path)]
        params += [{"name": q["name"], "in": "query", "required": False, "schema": {}, **({"example": q["example"]} if q.get("example") else {})} for q in query]
        for h in req.get("header", []):
            if h.get("disabled"):
                continue
            key = h.get("key", "")
            if key.lower() in SECRET_HEADERS:
                continue  # value never recorded; auth is modeled via security_schemes
            params.append({"name": key, "in": "header", "required": False, "schema": {}})

        body = None
        b = req.get("body") or {}
        mode = b.get("mode")
        if mode == "raw":
            raw = b.get("raw", "")
            lang = ((b.get("options") or {}).get("raw") or {}).get("language", "")
            ctype = "application/json" if lang == "json" else "text/plain"
            body = {"content_type": ctype, "required": True, "schema": {}}
            if lang == "json" or raw.lstrip().startswith(("{", "[")):
                try:
                    body["content_type"] = "application/json"
                    body["example"] = redact_example(json.loads(raw))
                except json.JSONDecodeError:
                    warnings.append(f"{method} {path}: body has {{variables}}, example not parsed")
        elif mode in ("urlencoded", "formdata"):
            fields = b.get(mode, [])
            body = {"content_type": "application/x-www-form-urlencoded" if mode == "urlencoded" else "multipart/form-data",
                    "required": True,
                    "schema": {"type": "object", "properties": {f.get("key", ""): {"type": "string", **({"format": "binary"} if f.get("type") == "file" else {})} for f in fields}}}
        elif mode == "graphql":
            body = {"content_type": "application/json", "required": True, "schema": {}, "graphql": True}
            warnings.append(f"{method} {path}: GraphQL request; use the GraphQL flow (Phase 2) for these.")

        item_auth = _auth(req.get("auth"), schemes) if "auth" in req else coll_auth
        if "auth" in req and (req["auth"] or {}).get("type") == "noauth":
            item_auth = []
        ep = {
            "id": slug(method, path),
            "method": method,
            "path": path,
            "summary": item.get("name", ""),
            "tags": [trail[0]] if trail else [],
            "deprecated": False,
            "auth_required": bool(item_auth),
            "security": item_auth,
            "parameters": params,
            "request_body": body,
            "responses": {},
        }
        saved = item.get("response") or []
        for resp in saved:
            code = str(resp.get("code", ""))
            if code:
                ep["responses"][code] = {"description": resp.get("name", "saved example")}
        if not saved:
            warnings.append(f"{method} {path}: no saved responses; expected statuses unknown")
        endpoints.append(ep)

    # collection variable baseUrl is a hint only
    base_vars = {v.get("key"): v.get("value") for v in doc.get("variable", []) if isinstance(v, dict)}
    servers = origins
    for k in ("baseUrl", "base_url", "host"):
        if k in base_vars and isinstance(base_vars[k], str) and base_vars[k].startswith("http"):
            bv, _ = strip_userinfo(base_vars[k])
            servers = [bv] + [o for o in origins if o != bv]
            break
    if any(s.startswith("http") for s in servers):
        servers = [s for s in servers if not s.startswith("{{")]
    else:
        warnings.append("Base URL is a Postman variable; ask the user for the actual (non-production) URL.")
    unique_ids(endpoints, warnings)
    return {
        "source": {"type": "postman", "version": schema_url.rstrip("/").rsplit("/", 2)[-2] if schema_url else "",
                   "file": Path(filename).name if filename else ""},
        "info": {"title": doc["info"].get("name", ""), "version": ""},
        "servers": servers,
        "security_schemes": schemes,
        "endpoints": endpoints,
        "warnings": warnings,
    }


def build(path: str) -> dict:
    return parse(load_document(path), path)


def main(argv: list[str] | None = None) -> int:
    return cli_main(argv, "parse_postman.py", "Parse a Postman collection into normalized JSON.", build)


if __name__ == "__main__":
    sys.exit(main())
