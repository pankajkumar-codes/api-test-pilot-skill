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
    "uniqueItems", "nullable", "default", "const", "example", "readOnly", "writeOnly", "multipleOf",
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
                name = ref.rsplit("/", 1)[-1]
                if not (ref.startswith(f"#/{BUNDLE_KEY}/") and ref.count("/") == 2):   # a whole-file ref has no name of its own
                    out.setdefault("$name", name)
            return out
        out: dict[str, Any] = {k: node[k] for k in SCHEMA_KEYS if k in node}
        if out.get("format") == "password" or out.get("writeOnly"):  # sample values can be real secrets
            for drop in ("example", "default", "enum", "const"):
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
                    for drop in ("example", "default", "enum", "const"):
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
        for drop in ("example", "default", "enum", "const"):
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
    media = content[ctype] if isinstance(content[ctype], dict) else {}
    example = media.get("example")
    if example is None and isinstance(media.get("examples"), dict):
        first = next(iter(media["examples"].values()), {})
        example = r.deref(first).get("value") if isinstance(first, dict) else None
    return ctype, r.schema(media.get("schema", {})), example


MAX_HOOKS = 200


def _hooks(r: Resolver, doc: dict, ops: list[tuple[str, dict]], warnings: list[str]) -> list[dict]:
    """Webhooks (OpenAPI 3.1 top-level `webhooks`) and operation `callbacks`. They describe calls the API makes to the client, so
    nothing can be sent to them; the intake keeps the name, method and payload schema so a plan can list them.
    `ops` holds (endpoint id, operation) pairs whose callbacks are read."""
    out: list[dict] = []
    dropped = []

    def add(kind: str, name: str, expression: str, item: Any, parent: str | None) -> None:
        item = r.deref(item)
        for method in HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            if len(out) >= MAX_HOOKS:
                dropped.append(name)
                continue
            try:
                rb = r.deref(op.get("requestBody"))
                ctype, sch, ex = _content_schema(r, rb.get("content"))
                text = op.get("summary") or op.get("description") or ""
                hook: dict[str, Any] = {"kind": kind, "name": name, "method": method.upper(),
                                        "summary": (text if isinstance(text, str) else "").strip().split("\n")[0]}
                if parent:
                    hook["parent"] = parent
                    hook["expression"] = expression
                if ctype:
                    hook["request_body"] = {"content_type": ctype, "schema": sch or {}}
                    if ex is not None:
                        hook["request_body"]["example"] = redact_example(ex)
                responses = op.get("responses")
                hook["responses"] = sorted(str(c) for c in responses) if isinstance(responses, dict) else []
            except (TypeError, AttributeError, ValueError, KeyError):   # a payload the spec writer got wrong: skip it, keep the rest
                warnings.append(f"{kind} '{name}' ({method.upper()}) could not be read and was skipped")
                continue
            out.append(hook)

    hooks_map = doc.get("webhooks")
    for name, item in (hooks_map.items() if isinstance(hooks_map, dict) else []):
        add("webhook", str(name), "", item, None)
    for ep_id, op in ops:
        cbs = op.get("callbacks")
        for cname, cb in (cbs.items() if isinstance(cbs, dict) else []):
            for expression, item in r.deref(cb).items():
                if isinstance(expression, str) and not expression.startswith("x-"):
                    add("callback", str(cname), expression, item, ep_id)
    if dropped:
        warnings.append(f"more than {MAX_HOOKS} webhooks and callbacks: {len(dropped)} were not listed")
    return out


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
    hook_ops: list[dict] = []
    paths = doc.get("paths")
    if paths is not None and not isinstance(paths, dict):
        warnings.append("'paths' is not a map and was ignored")
    for path, item in (paths.items() if isinstance(paths, dict) else []):
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
            raw_responses = op.get("responses")
            if raw_responses is not None and not isinstance(raw_responses, dict):
                warnings.append(f"{method.upper()} {path}: 'responses' is not a map and was ignored")
            for status, resp in (raw_responses.items() if isinstance(raw_responses, dict) else []):
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
            hook_ops.append(op)

    unique_ids(endpoints, warnings)
    hooks = _hooks(r, doc, [(e["id"], op) for e, op in zip(endpoints, hook_ops)], warnings)
    info = doc.get("info") or {}
    out = {
        "source": {"type": "openapi", "version": version, "file": Path(filename).name if filename else ""},
        "info": {"title": info.get("title", ""), "version": str(info.get("version", ""))},
        "servers": servers,
        "security_schemes": schemes,
        "endpoints": endpoints,
        "warnings": warnings,
    }
    if hooks:
        out["webhooks"] = hooks
    return out


MAX_FILES = 50
MAX_FILE_BYTES = 5_000_000
BUNDLE_KEY = "x-apitest-bundled"
FILE_SUFFIXES = (".yaml", ".yml", ".json")
VALUE_KEYS = ("example", "examples", "enum", "default", "const")   # data, not schema: a "$ref" string in there is just text
# Maps whose keys are names the author chose (a property called `default`, a response code `default`), so a key
# in there is never a keyword and its value is always walked.
NAME_MAPS = frozenset({"properties", "patternProperties", "definitions", "$defs", "schemas", "responses", "parameters", "headers",
                       "requestBodies", "callbacks", "links", "securitySchemes", "content", "paths", "webhooks", "pathItems"})


def _has_file_ref(doc: Any) -> bool:
    """True when a `$ref` anywhere points at another file. Walks each node once (YAML aliases share nodes), so it stays cheap."""
    seen: set[tuple[int, bool]] = set()
    todo: list[tuple[Any, bool]] = [(doc, False)]
    while todo:
        node, names = todo.pop()
        if isinstance(node, (dict, list)):
            if (id(node), names) in seen:
                continue
            seen.add((id(node), names))
            if isinstance(node, dict):
                ref = node.get("$ref")
                if not names and isinstance(ref, str) and not ref.startswith("#"):
                    return True
                todo.extend((v, not names and k in NAME_MAPS) for k, v in node.items() if names or k not in VALUE_KEYS)
            else:
                todo.extend((v, False) for v in node)
    return False


class _Bundler:
    """Copy the files a spec points at with relative `$ref`s into the root document, so the resolver only sees local refs.
    Only .yaml, .yml and .json files inside the root spec's folder are read: an absolute path, a `..` that leaves the folder,
    a symlink out of it and any URL are refused with a warning. Local refs inside a copied file are re-pointed at its copy."""

    def __init__(self, root_file: Path, warnings: list[str]):
        self.base = root_file.resolve().parent
        self.root_file = root_file.resolve()
        self.warnings = warnings
        self.slots: dict[Path, str] = {}
        self.copies: dict[str, Any] = {}

    def _slot(self, target: Path) -> str | None:
        if target in self.slots:
            return self.slots[target]
        if len(self.slots) >= MAX_FILES:
            self._warn(f"more than {MAX_FILES} referenced files: the rest are not loaded")
            return None
        try:
            if target.stat().st_size > MAX_FILE_BYTES:
                self._warn(f"referenced file is larger than {MAX_FILE_BYTES // 1_000_000} MB, not loaded: {target.name}")
                return None
            doc = load_document(target)
        except IntakeError:   # the parser's message can quote the file, so it is left out
            self._warn(f"referenced file is not valid JSON or YAML, not loaded: {target.name}")
            return None
        except (OSError, RecursionError, ValueError) as exc:
            self._warn(f"referenced file could not be read ({target.name}): {getattr(exc, 'strerror', None) or type(exc).__name__}")
            return None
        slot = f"f{len(self.slots) + 1}"
        self.slots[target] = slot
        self.copies[slot] = doc          # in place first, so a ref cycle between files ends here
        try:
            self.copies[slot] = self.walk(doc, target, {})
        except RecursionError:
            self._warn(f"referenced file is nested too deeply, not loaded: {target.name}")
            del self.slots[target], self.copies[slot]
            return None
        return slot

    def _warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def rewrite(self, ref: str, current: Path) -> str:
        if ref.startswith("#"):
            slot = self.slots.get(current)
            return f"#/{BUNDLE_KEY}/{slot}{ref[1:]}" if slot else ref
        file_part, _, frag = ref.partition("#")
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", file_part) or file_part.startswith(("/", "\\")):
            self._warn(f"External $ref not resolved (offline, only relative files are read): {ref[:100]}")
            return ref
        try:
            target = (current.parent / file_part).resolve()
            inside, is_file = target.is_relative_to(self.base), target.is_file()
        except (ValueError, OSError):   # a NUL byte or a name the file system rejects
            self._warn(f"$ref is not a usable file name and was not loaded: {ref[:100]!r}")
            return ref
        if not inside:
            self._warn(f"$ref points outside the spec folder and was not loaded: {ref[:100]}")
            return ref
        if not is_file:
            self._warn(f"$ref file not found: {ref[:100]}")
            return ref
        if target.suffix.lower() not in FILE_SUFFIXES:
            self._warn(f"$ref is not a .yaml, .yml or .json file and was not loaded: {ref[:100]}")
            return ref
        if target == self.root_file:
            return "#" + frag
        slot = self._slot(target)
        return ref if slot is None else f"#/{BUNDLE_KEY}/{slot}{frag}"

    def walk(self, node: Any, current: Path, memo: dict[tuple[int, bool], Any], names: bool = False) -> Any:
        """Copy with refs rewritten. `memo` keeps YAML aliases shared instead of expanding them into copies.
        `names` is true for a map of author-chosen names (see NAME_MAPS), where no key is a keyword."""
        if not isinstance(node, (dict, list)):
            return node
        if (id(node), names) in memo:
            return memo[(id(node), names)]
        if isinstance(node, dict):
            out: Any = {}
            memo[(id(node), names)] = out
            for k, v in node.items():
                if not names and k == "$ref" and isinstance(v, str):
                    out[k] = self.rewrite(v, current)
                elif not names and k in VALUE_KEYS:
                    out[k] = v
                else:
                    out[k] = self.walk(v, current, memo, not names and k in NAME_MAPS)
            return out
        out = []
        memo[(id(node), names)] = out
        out.extend(self.walk(v, current, memo) for v in node)
        return out


def bundle(doc: Any, path: str) -> tuple[Any, list[str]]:
    """Inline relative file refs of a spec read from `path`. Returns the document and any warnings.
    A spec without a file ref is returned untouched."""
    warnings: list[str] = []
    if not isinstance(doc, dict) or not _has_file_ref(doc):
        return doc, warnings
    if BUNDLE_KEY in doc:
        raise IntakeError(f"The spec already uses the top-level key '{BUNDLE_KEY}', which the file bundling needs. Rename it.")
    b = _Bundler(Path(path), warnings)
    b.slots[b.root_file] = ""
    out = b.walk(doc, b.root_file, {})
    if b.copies:
        out[BUNDLE_KEY] = b.copies
    return out, warnings


def build(path: str) -> dict:
    doc, bundle_warnings = bundle(load_document(path), path)
    out = parse(doc, path)
    out["warnings"] = bundle_warnings + out["warnings"]
    return out


def main(argv: list[str] | None = None) -> int:
    return cli_main(argv, "parse_openapi.py", "Parse OpenAPI/Swagger into normalized JSON.", build)


if __name__ == "__main__":
    sys.exit(main())
