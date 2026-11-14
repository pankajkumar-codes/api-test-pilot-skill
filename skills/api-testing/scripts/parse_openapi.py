#!/usr/bin/env python3
"""Parse an OpenAPI 3.x (or Swagger 2.0) file into normalized JSON.

Usage:  python parse_openapi.py spec.yaml [-o intake.json] [--summary]

Resolves local $refs (cycle-safe), merges allOf, merges path-level and operation-level
parameters, resolves effective auth per endpoint. Offline: remote refs are never fetched.
Output shape: see references/intake.md.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    HTTP_METHODS, IntakeError, cli_main, env_name, is_secret_name, load_document, redact_example,
    slug, strip_userinfo, unique_ids,
)

SCHEMA_KEYS = (
    "type", "format", "enum", "required", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "minLength", "maxLength", "pattern", "minItems", "maxItems",
    "uniqueItems", "nullable", "default", "example", "readOnly", "writeOnly", "multipleOf",
)
MAX_DEPTH = 12


class Resolver:
    def __init__(self, root: dict, warnings: list[str]):
        self.root, self.warnings = root, warnings

    def lookup(self, ref: str) -> Any:
        if not ref.startswith("#/"):
            msg = f"External $ref not resolved (offline): {ref}"
            if msg not in self.warnings:
                self.warnings.append(msg)
            return None
        node: Any = self.root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                self.warnings.append(f"Dangling $ref: {ref}")
                return None
        return node

    def deref(self, node: Any, stack: tuple = ()) -> Any:
        """Resolve a top-level $ref chain on a non-schema object (parameter, response...)."""
        while isinstance(node, dict) and "$ref" in node and node["$ref"] not in stack:
            stack += (node["$ref"],)
            target = self.lookup(node["$ref"])
            if target is None:
                return {}
            node = target
        return node if isinstance(node, dict) else {}

    def schema(self, node: Any, stack: tuple = (), depth: int = 0) -> Any:
        if not isinstance(node, dict):
            return {} if node in (None, True) else node
        if "$ref" in node:
            ref = node["$ref"]
            if ref in stack or depth > MAX_DEPTH:
                return {"$circular": ref.rsplit("/", 1)[-1]}
            target = self.lookup(ref)
            if target is None:
                return {"$unresolved": ref}
            out = self.schema(target, stack + (ref,), depth + 1)
            if isinstance(out, dict):
                out.setdefault("$name", ref.rsplit("/", 1)[-1])
            return out
        out: dict[str, Any] = {k: node[k] for k in SCHEMA_KEYS if k in node}
        if out.get("format") == "password" or out.get("writeOnly"):  # sample values can be real secrets
            for drop in ("example", "default", "enum"):
                out.pop(drop, None)
        if isinstance(out.get("type"), list):  # OpenAPI 3.1: ["string", "null"]
            types = [t for t in out["type"] if t != "null"]
            if len(types) != len(out["type"]):
                out["nullable"] = True
            out["type"] = types[0] if len(types) == 1 else types
        if "properties" in node and isinstance(node["properties"], dict):
            out["properties"] = {k: self.schema(v, stack, depth + 1) for k, v in node["properties"].items()}
            for k, v in out["properties"].items():  # don't carry over sample or default secrets
                if is_secret_name(k) and isinstance(v, dict):
                    for drop in ("example", "default", "enum"):
                        v.pop(drop, None)
            out.setdefault("type", "object")
        if "items" in node:
            out["items"] = self.schema(node["items"], stack, depth + 1)
            out.setdefault("type", "array")
        if isinstance(node.get("additionalProperties"), (dict, bool)):
            ap = node["additionalProperties"]
            out["additionalProperties"] = self.schema(ap, stack, depth + 1) if isinstance(ap, dict) else ap
        for combo in ("oneOf", "anyOf"):
            if combo in node:
                out[combo] = [self.schema(s, stack, depth + 1) for s in node[combo]]
        for sub in node.get("allOf", []) or []:
            part = self.schema(sub, stack, depth + 1)
            if not isinstance(part, dict):
                continue
            for k, v in part.items():
                if k == "properties":
                    out.setdefault("properties", {}).update(v)
                elif k == "required":
                    out["required"] = sorted(set(out.get("required", [])) | set(v))
                elif k != "$name":
                    out.setdefault(k, v)
        if "properties" in out and "required" in out:
            out["required"] = [r for r in out["required"] if r in out["properties"]] or out["required"]
        return out


# --------------------------------------------------------------------------- helpers
def _param(r: Resolver, p: Any, swagger2: bool) -> dict | None:
    p = r.deref(p)
    if not p or "name" not in p or "in" not in p:
        return None
    if swagger2:
        sch = r.schema({k: v for k, v in p.items() if k in SCHEMA_KEYS + ("items",)})
    else:
        sch = r.schema(p.get("schema", {}))
    out = {
        "name": p["name"], "in": p["in"],
        "required": bool(p.get("required", p["in"] == "path")),
        "schema": sch,
    }
    sensitive = is_secret_name(p["name"]) or (
        isinstance(sch, dict) and (sch.get("format") == "password" or sch.get("writeOnly")))
    if sensitive:
        for drop in ("example", "default", "enum"):
            if isinstance(sch, dict):
                sch.pop(drop, None)
    elif "example" in p:
        out["example"] = p["example"]
    if p.get("deprecated"):
        out["deprecated"] = True
    return out


def _scheme_info(name: str, s: dict) -> dict:
    s = s or {}
    t = s.get("type", "unknown")
    info: dict[str, Any] = {"type": t}
    if t == "http":
        info["scheme"] = str(s.get("scheme", "")).lower()
        if info["scheme"] == "bearer" and s.get("bearerFormat"):
            info["bearer_format"] = s["bearerFormat"]
    elif t == "basic":  # swagger 2
        info["type"], info["scheme"] = "http", "basic"
    elif t == "apiKey":
        info["in"], info["name"] = s.get("in"), s.get("name")
    elif t in ("oauth2", "openIdConnect"):
        flows = s.get("flows") or ({s.get("flow", "implicit"): s} if s.get("flow") else {})
        info["flows"] = {
            f: {"scopes": sorted((v.get("scopes") or {}).keys()),
                **({"tokenUrl": v["tokenUrl"]} if v.get("tokenUrl") else {})}
            for f, v in flows.items() if isinstance(v, dict)
        }
        if t == "openIdConnect":
            info["openIdConnectUrl"] = s.get("openIdConnectUrl")
    # Env var names only. Values never go into chat, files or logs.
    if info.get("scheme") == "basic":
        info["env"] = [env_name(name, "USER"), env_name(name, "PASS")]
    elif t == "apiKey":
        info["env"] = [env_name(name, "KEY")]
    else:
        info["env"] = [env_name(name, "TOKEN")]
    return info


AUTH_PARAMS = {"authorization", "x-api-key", "api-key", "apikey", "api_key", "x-auth-token", "x-access-token", "access_token", "x-token"}


def _inferred_scheme(params: list[dict], schemes: dict) -> str | None:
    """Scheme name for an operation that declares no security but has a credential parameter (header or query).
    The synthetic scheme is added to `schemes` (marked inferred) so the generated tests send credentials from env vars."""
    for p in params:
        name = str(p.get("name", ""))
        if p.get("in") not in ("header", "query") or name.lower() not in AUTH_PARAMS:
            continue
        key = "inferred_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        if key not in schemes:
            if name.lower() == "authorization":
                info: dict[str, Any] = {"type": "http", "scheme": "bearer", "env": [env_name(key, "TOKEN")]}
            else:
                info = {"type": "apiKey", "in": p["in"], "name": name, "env": [env_name(key, "KEY")]}
            info["inferred"] = True
            schemes[key] = info
        return key
    return None


def _security_reqs(sec: Any) -> list[list[str]]:
    """[{a: [], b: []}, {c: []}] -> [[a, b], [c]] (OR of ANDs). Empty dict = anonymous allowed."""
    return [sorted(req.keys()) for req in (sec or []) if isinstance(req, dict)]


def _content_schema(r: Resolver, content: dict | None) -> tuple[str | None, Any, Any]:
    if not isinstance(content, dict) or not content:
        return None, None, None
    ctype = next((c for c in content if "json" in c), next(iter(content)))
    media = content[ctype] or {}
    example = media.get("example")
    if example is None and isinstance(media.get("examples"), dict):
        first = next(iter(media["examples"].values()), {})
        example = r.deref(first).get("value") if isinstance(first, dict) else None
    return ctype, r.schema(media.get("schema", {})), example


# --------------------------------------------------------------------------- main
def parse(doc: Any, filename: str = "") -> dict:
    if not isinstance(doc, dict):
        raise IntakeError("Spec root must be an object.")
    swagger2 = "swagger" in doc
    if not swagger2 and "openapi" not in doc:
        raise IntakeError("Not an OpenAPI/Swagger document (no 'openapi' or 'swagger' key).")
    version = str(doc.get("openapi") or doc.get("swagger"))
    if not (version.startswith("3.") or version.startswith("2.")):
        raise IntakeError(f"Unsupported OpenAPI version: {version}")

    warnings: list[str] = []
    r = Resolver(doc, warnings)

    # servers
    if swagger2:
        schemes = doc.get("schemes") or ["https"]
        servers = [f"{schemes[0]}://{doc['host']}{doc.get('basePath', '')}"] if doc.get("host") else []
    else:
        servers = [s.get("url", "") for s in doc.get("servers", []) if isinstance(s, dict)]
    clean = []
    for u in servers:
        u2, stripped = strip_userinfo(u)
        if stripped:
            warnings.append("Credentials embedded in a server URL were stripped; use env vars for auth.")
        clean.append(u2)
    servers = clean
    if not servers:
        warnings.append("No servers/host in spec: ask the user for the base URL.")

    # security schemes
    raw_schemes = doc.get("securityDefinitions") if swagger2 else (doc.get("components") or {}).get("securitySchemes")
    schemes = {n: _scheme_info(n, r.deref(s)) for n, s in (raw_schemes or {}).items()}
    global_sec = _security_reqs(doc.get("security"))

    endpoints: list[dict] = []
    for path, item in (doc.get("paths") or {}).items():
        item = r.deref(item)
        if not isinstance(item, dict):
            continue
        for method in HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            params: dict[tuple, dict] = {}
            body_param = None
            for p in list(item.get("parameters", [])) + list(op.get("parameters", [])):
                pd = r.deref(p)
                if swagger2 and pd.get("in") == "body":
                    body_param = pd
                    continue
                parsed = _param(r, pd, swagger2)
                if parsed:
                    params[(parsed["name"], parsed["in"])] = parsed  # op-level overrides path-level

            body = None
            if swagger2:
                if body_param:
                    ctype = (op.get("consumes") or doc.get("consumes") or ["application/json"])[0]
                    body = {"content_type": ctype, "required": bool(body_param.get("required")),
                            "schema": r.schema(body_param.get("schema", {}))}
                form = [p for p in params.values() if p["in"] == "formData"]
                if form and not body:
                    body = {"content_type": (op.get("consumes") or ["application/x-www-form-urlencoded"])[0],
                            "required": any(p["required"] for p in form),
                            "schema": {"type": "object",
                                       "properties": {p["name"]: p["schema"] for p in form},
                                       "required": [p["name"] for p in form if p["required"]]}}
            elif "requestBody" in op:
                rb = r.deref(op["requestBody"])
                ctype, sch, ex = _content_schema(r, rb.get("content"))
                body = {"content_type": ctype, "required": bool(rb.get("required")), "schema": sch or {}}
                if ex is not None:
                    body["example"] = redact_example(ex)
                if rb.get("content") and len(rb["content"]) > 1:
                    body["other_content_types"] = [c for c in rb["content"] if c != ctype]

            responses: dict[str, dict] = {}
            for status, resp in (op.get("responses") or {}).items():
                resp = r.deref(resp)
                if swagger2:
                    ctype, sch = (op.get("produces") or doc.get("produces") or [None])[0], r.schema(resp.get("schema")) if resp.get("schema") else None
                else:
                    ctype, sch, _ = _content_schema(r, resp.get("content"))
                entry: dict[str, Any] = {"description": resp.get("description", "")}
                if sch:
                    entry["content_type"], entry["schema"] = ctype, sch
                if resp.get("headers"):
                    entry["headers"] = sorted(resp["headers"].keys())
                responses[str(status)] = entry

            # effective auth: operation-level overrides global; [] means public
            reqs = _security_reqs(op["security"]) if "security" in op else global_sec
            auth_required = bool(reqs) and not any(len(req) == 0 for req in reqs)
            if not reqs and "security" not in op:   # no scheme declared anywhere, yet a parameter carries a credential
                inferred = _inferred_scheme(list(params.values()), schemes)
                if inferred:
                    reqs, auth_required = [[inferred]], True
                    warnings.append(f"{method.upper()} {path}: no security scheme is declared, but the parameter "
                                    f"'{schemes[inferred]['name'] if schemes[inferred].get('name') else 'Authorization'}' carries a credential; "
                                    f"treated as {schemes[inferred]['type']} auth ({schemes[inferred]['env'][0]}). Declare a securityScheme in the spec to be exact.")
            for req in reqs:
                for s in req:
                    if s not in schemes:
                        warnings.append(f"{method.upper()} {path}: unknown security scheme '{s}'")

            ep = {
                "id": op.get("operationId") or slug(method, path),
                "method": method.upper(),
                "path": path,
                "summary": op.get("summary") or (op.get("description") or "").strip().split("\n")[0],
                "tags": op.get("tags", []),
                "deprecated": bool(op.get("deprecated")),
                "auth_required": auth_required,
                "security": reqs,
                "parameters": list(params.values()),
                "request_body": body,
                "responses": responses,
            }
            if not op.get("operationId"):
                warnings.append(f"{method.upper()} {path}: no operationId (generated id '{ep['id']}')")
            if not any(c.startswith("2") for c in responses):
                warnings.append(f"{method.upper()} {path}: no 2xx response documented")
            endpoints.append(ep)

    unique_ids(endpoints, warnings)
    info = doc.get("info") or {}
    return {
        "source": {"type": "openapi", "version": version, "file": Path(filename).name if filename else ""},
        "info": {"title": info.get("title", ""), "version": str(info.get("version", ""))},
        "servers": servers,
        "security_schemes": schemes,
        "endpoints": endpoints,
        "warnings": warnings,
    }


def build(path: str) -> dict:
    return parse(load_document(path), path)


def main(argv: list[str] | None = None) -> int:
    return cli_main(argv, "parse_openapi.py", "Parse OpenAPI/Swagger into normalized JSON.", build)


if __name__ == "__main__":
    sys.exit(main())
