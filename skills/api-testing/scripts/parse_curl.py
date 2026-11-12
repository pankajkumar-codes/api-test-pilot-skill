#!/usr/bin/env python3
"""Parse curl command examples into the same normalized JSON as parse_openapi.py.

Usage:  python parse_curl.py examples.txt [-o intake.json] [--summary]

The file may hold several `curl ...` commands (line continuations with \\ are supported).
Secrets: Authorization/API-key header values, -u credentials and cookies are never emitted;
they become security schemes with env-var names. Numeric/UUID path segments become {id} params.
"""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    SECRET_HEADERS, IntakeError, cli_main, env_name, redact_example, slug, unique_ids,
)

DATA_FLAGS = {"-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode"}
SKIP_WITH_ARG = {"-o", "--output", "-m", "--max-time", "--connect-timeout", "-A", "--user-agent",
                 "-e", "--referer", "--proxy", "-x", "--cacert", "--cert", "--retry", "-w", "--write-out"}
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def split_commands(text: str) -> list[str]:
    text = re.sub(r"\\\r?\n", " ", text)
    cmds: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        line = re.sub(r"^\$\s+", "", line)
        if re.match(r"^curl(\s|$)", line):
            cmds.append(line)
        elif cmds and not line.startswith("curl"):
            cmds[-1] += " " + line  # unterminated quote continuation
    return cmds


def parse_command(cmd: str) -> dict:
    try:
        toks = shlex.split(cmd)
    except ValueError as exc:
        raise IntakeError(f"Cannot tokenize curl command ({exc}): {cmd[:60]}...") from exc
    method = None
    url = None
    headers: list[tuple[str, str]] = []
    data: list[str] = []
    user = None
    form = False
    get_mode = False
    i = 1
    while i < len(toks):
        t = toks[i]
        arg = toks[i + 1] if i + 1 < len(toks) else ""
        if t in ("-X", "--request"):
            method, i = arg.upper(), i + 2
        elif t in ("-H", "--header"):
            k, _, v = arg.partition(":")
            headers.append((k.strip(), v.strip()))
            i += 2
        elif t in DATA_FLAGS:
            data.append(arg)
            i += 2
        elif t == "--json":
            data.append(arg)
            headers.append(("Content-Type", "application/json"))
            i += 2
        elif t in ("-F", "--form"):
            data.append(arg)
            form = True
            i += 2
        elif t in ("-u", "--user"):
            user, i = arg, i + 2
        elif t in ("-b", "--cookie"):
            i += 2  # never recorded
        elif t == "--url":
            url, i = arg, i + 2
        elif t in ("-G", "--get"):
            get_mode, i = True, i + 1
        elif t in ("-I", "--head"):
            method, i = "HEAD", i + 1
        elif t in SKIP_WITH_ARG:
            i += 2
        elif t.startswith("-"):
            i += 1  # boolean flags: -s -k -L -v -i ...
        else:
            url = url or t
            i += 1
    if not url:
        raise IntakeError(f"curl command without a URL: {cmd[:60]}")
    if not re.match(r"^[a-z]+://", url):
        url = "http://" + url
    if method is None:
        method = "POST" if data and not get_mode else "GET"
    return {"method": method, "url": url, "headers": headers, "data": data, "user": user, "form": form, "get": get_mode}


def _templatize(path: str) -> tuple[str, list[dict]]:
    params, out, n = [], [], 0
    for seg in path.split("/"):
        if seg.isdigit() or UUID_RE.match(seg):
            n += 1
            name = "id" if n == 1 else f"id{n}"
            params.append({"name": name, "in": "path", "required": True,
                           "schema": {"type": "integer" if seg.isdigit() else "string",
                                      **({} if seg.isdigit() else {"format": "uuid"})},
                           "example": int(seg) if seg.isdigit() else seg})
            out.append("{" + name + "}")
        else:
            out.append(seg)
    return "/".join(out), params


def parse_text(text: str, filename: str = "") -> dict:
    cmds = split_commands(text)
    if not cmds:
        raise IntakeError("No `curl` commands found.")
    warnings: list[str] = []
    schemes: dict[str, dict] = {}
    servers: list[str] = []
    endpoints: dict[tuple, dict] = {}

    for cmd in cmds:
        c = parse_command(cmd)
        u = urlsplit(c["url"])
        origin = f"{u.scheme}://{u.netloc}"
        if origin not in servers:
            servers.append(origin)
        path, params = _templatize(u.path or "/")
        query_src = u.query
        body = None
        if c["data"] and c["get"]:
            query_src = "&".join(filter(None, [query_src] + c["data"]))
        elif c["data"]:
            raw = "&".join(c["data"]) if not c["form"] else ""
            ctype = next((v for k, v in c["headers"] if k.lower() == "content-type"), None)
            if c["form"]:
                body = {"content_type": "multipart/form-data", "required": True,
                        "schema": {"type": "object", "properties": {d.split("=")[0]: {"type": "string"} for d in c["data"]}}}
            else:
                body = {"content_type": ctype or "application/x-www-form-urlencoded", "required": True, "schema": {}}
                try:
                    body["example"] = redact_example(json.loads(raw))
                    body["content_type"] = ctype or "application/json"
                except (json.JSONDecodeError, ValueError):
                    if raw.startswith("@"):
                        warnings.append(f"{c['method']} {path}: body is a file reference ({raw[:40]}); not read")
        for k, v in parse_qsl(query_src, keep_blank_values=True):
            params.append({"name": k, "in": "query", "required": False, "schema": {},
                           **({"example": "<redacted>"} if re.search(r"token|key|secret|pass", k, re.I) else {"example": v})})

        security: list[list[str]] = []
        for k, v in c["headers"]:
            kl = k.lower()
            if kl == "authorization":
                scheme = v.split(" ", 1)[0].lower()
                if scheme == "basic":
                    schemes.setdefault("basicAuth", {"type": "http", "scheme": "basic",
                                                     "env": [env_name("basicAuth", "USER"), env_name("basicAuth", "PASS")]})
                    security = [["basicAuth"]]
                else:
                    schemes.setdefault("bearerAuth", {"type": "http", "scheme": "bearer", "env": [env_name("bearerAuth", "TOKEN")]})
                    security = [["bearerAuth"]]
            elif kl in SECRET_HEADERS:
                if kl == "cookie":
                    schemes.setdefault("cookieAuth", {"type": "apiKey", "in": "cookie", "name": "(session)", "env": [env_name("cookieAuth", "KEY")]})
                    security = [["cookieAuth"]]
                else:
                    schemes.setdefault("apiKeyAuth", {"type": "apiKey", "in": "header", "name": k, "env": [env_name("apiKeyAuth", "KEY")]})
                    security = [["apiKeyAuth"]]
            elif kl not in ("content-type", "accept", "user-agent", "content-length", "host"):
                params.append({"name": k, "in": "header", "required": False, "schema": {}})
        if c["user"]:
            schemes.setdefault("basicAuth", {"type": "http", "scheme": "basic",
                                             "env": [env_name("basicAuth", "USER"), env_name("basicAuth", "PASS")]})
            security = [["basicAuth"]]
        if any(s in cmd for s in ("sk_live", "AKIA")):
            warnings.append("A credential-looking literal was found in the input and was NOT copied to the output. Rotate it if real.")

        key = (c["method"], path)
        ep = {
            "id": slug(c["method"], path), "method": c["method"], "path": path, "summary": "",
            "tags": [], "deprecated": False, "auth_required": bool(security), "security": security,
            "parameters": params, "request_body": body, "responses": {},
        }
        if key in endpoints:  # merge: keep first, add new params/auth
            prev = endpoints[key]
            seen = {(p["name"], p["in"]) for p in prev["parameters"]}
            prev["parameters"] += [p for p in params if (p["name"], p["in"]) not in seen]
            prev["request_body"] = prev["request_body"] or body
            if security and not prev["security"]:
                prev["security"], prev["auth_required"] = security, True
        else:
            endpoints[key] = ep

    warnings.append("curl gives no response schemas or status expectations; the plan must derive them from the user.")
    eps = list(endpoints.values())
    unique_ids(eps, warnings)
    return {
        "source": {"type": "curl", "version": "", "file": Path(filename).name if filename else ""},
        "info": {"title": "curl examples", "version": ""},
        "servers": servers,
        "security_schemes": schemes,
        "endpoints": eps,
        "warnings": warnings,
    }


def build(path: str) -> dict:
    if path.startswith(("http://", "https://")):
        raise IntakeError("Pass a local file containing curl commands.")
    p = Path(path)
    if not p.is_file():
        raise IntakeError(f"File not found: {path}")
    return parse_text(p.read_text(encoding="utf-8-sig"), path)


def main(argv: list[str] | None = None) -> int:
    return cli_main(argv, "parse_curl.py", "Parse curl examples into normalized JSON.", build)


if __name__ == "__main__":
    sys.exit(main())
