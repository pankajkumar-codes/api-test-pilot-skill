"""Shared helpers for the pytest generators (stdlib only, offline).

- sample values and mutations derived from the simplified schemas of references/intake.md
- Python-literal rendering (Expr keeps computed values like "a" * 50 readable)
- endpoint selection helpers; every example/default goes through the shared redaction helper
"""
from __future__ import annotations

import re
from typing import Any

from _common import is_secret_name, redact_example

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
BODY_METHODS = ("POST", "PUT", "PATCH")
PLACEHOLDER = "REPLACE_ME"
ADMIN_PATH_RE = re.compile(r"/(admin|internal|manage|management|system|superuser)(/|$)", re.I)


class Expr(str):
    """Raw Python source rendered as-is by py()."""


def py(v: Any) -> str:
    if isinstance(v, Expr):
        return str(v)
    if isinstance(v, dict):
        return "{" + ", ".join(f"{py(k)}: {py(x)}" for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        inner = ", ".join(py(x) for x in v)
        return "[" + inner + "]" if isinstance(v, list) else "(" + inner + ("," if len(v) == 1 else "") + ")"
    return repr(v)


def ident(s: str) -> str:
    out = re.sub(r"[^0-9A-Za-z_]+", "_", s).strip("_") or "x"
    return ("t_" + out) if out[0].isdigit() else out


# --------------------------------------------------------------------------- endpoint helpers
def is_json_ct(ct: str | None) -> bool:
    return bool(ct) and "json" in ct.lower()


def body_schema(ep: dict) -> dict | None:
    rb = ep.get("request_body")
    if rb and isinstance(rb.get("schema"), dict):
        return rb["schema"]
    return None


def supported_body(ep: dict) -> bool:
    """JSON and urlencoded bodies are generated; other content types are reported, not guessed."""
    rb = ep.get("request_body")
    if not rb:
        return True
    ct = (rb.get("content_type") or "").lower()
    return is_json_ct(ct) or "x-www-form-urlencoded" in ct or not ct


def has_json_body(ep: dict) -> bool:
    rb = ep.get("request_body")
    return bool(rb) and (is_json_ct(rb.get("content_type")) or not rb.get("content_type"))


ADMIN_TEXT_RE = re.compile(r"\b(only\s+(an\s+)?admin(istrator)?s?|admin(istrator)?s?[\s-]+only|requires?\s+admin(istrator)?)\b", re.I)


def is_admin_endpoint(ep: dict) -> bool:
    """Admin by path (/admin/...), by tag, or because the summary/description says so ("Only Admins")."""
    text = f"{ep.get('summary') or ''} {ep.get('description') or ''}"
    return (bool(ADMIN_PATH_RE.search(ep["path"])) or "admin" in [t.lower() for t in ep.get("tags", [])]
            or bool(ADMIN_TEXT_RE.search(text)))


def admin_line(ep: dict) -> str:
    return "    rt.admin_guard()\n" if is_admin_endpoint(ep) else ""


def rate_probe(doc: dict) -> dict | None:
    """Endpoint for small bursts: a parameterless GET, preferably an authenticated one (throttling is usually per account)."""
    cands = [e for e in doc["endpoints"] if e["method"] == "GET" and not path_params(e) and supported_body(e)
             and not any(p.get("required") for p in e.get("parameters", []) if p["in"] in ("query", "header"))]
    return next((e for e in cands if e.get("auth_required")), cands[0] if cands else None)


def success_codes(ep: dict) -> list[int]:
    return sorted(int(c) for c in ep.get("responses", {}) if str(c).isdigit() and 200 <= int(c) < 300)


def expected_ok_codes(ep: dict) -> list[int]:
    """Statuses a valid request should get: the declared 2xx, or the declared 3xx when the spec has no 2xx
    (redirect endpoints such as GET /redirect/3 -> 302 are successes, not failures). When a spec declares both a 2xx and
    a 3xx (200 and 302 on one operation), only the 2xx is expected: the 3xx is treated as an alternative outcome."""
    ok = success_codes(ep)
    return ok or sorted(int(c) for c in ep.get("responses", {}) if str(c).isdigit() and 300 <= int(c) < 400)


def error_codes(ep: dict) -> list[int]:
    return sorted(int(c) for c in ep.get("responses", {}) if str(c).isdigit() and 400 <= int(c) < 500)


def path_params(ep: dict) -> list[dict]:
    return [p for p in ep.get("parameters", []) if p.get("in") == "path"]


def params_in(ep: dict, where: str) -> list[dict]:
    return [p for p in ep.get("parameters", []) if p.get("in") == where]


def success_schema(ep: dict) -> dict | None:
    for code in success_codes(ep):
        s = ep["responses"][str(code)].get("schema")
        if isinstance(s, dict) and s:
            return s
    return None


def is_list_endpoint(ep: dict) -> bool:
    """GET on a collection path (not ending in {id}) whose success body is an array or wraps one."""
    if ep["method"] != "GET" or ep["path"].rstrip("/").endswith("}"):
        return False
    s = success_schema(ep)
    if not s:
        return True
    return s.get("type") == "array" or any(
        isinstance(v, dict) and v.get("type") == "array" for v in (s.get("properties") or {}).values())


# --------------------------------------------------------------------------- sample values
_FORMAT_SAMPLES = {
    "email": "apitest@example.com", "uuid": "123e4567-e89b-12d3-a456-426614174000",
    "date": "2024-01-15", "date-time": "2024-01-15T10:30:00Z", "uri": "https://example.com/apitest",
    "url": "https://example.com/apitest", "ipv4": "192.0.2.1", "hostname": "example.com",
}


_NAME_HINT_STR = (
    (re.compile(r"(^|[_-])e-?mail([_-]?(address|addr))?$|E-?mail(Address)?$", re.I), "apitest@example.com"),
    (re.compile(r"(^|[_-])(url|uri|link|href|website|homepage|callback|webhook)(s)?$|(Url|Uri|Link|Href|URL|URI)$"), "https://example.com/apitest"),
    (re.compile(r"(^|[_-])(phone|mobile)([_-]?(number|no|num))?$|(Phone|Mobile)(Number)?$"), "+15555550100"),
)
_NAME_HINT_INT = (
    (re.compile(r"^status([_-]?code)?$|(^|[_-])(http|response|expected|default)[_-]?status([_-]?code)?$|StatusCode$", re.I), 200),   # 1 is not a valid HTTP status
    (re.compile(r"(^|[_-])port$", re.I), 8080),
)


def name_hint(name: str, kind: str | None) -> Any:
    """A plausible value from the field name alone (`email`, `default_status`), used only when the spec gives no
    example, default, enum, range or format. The value is still a guess (see is_guessed) but a real validator accepts it."""
    if not name:
        return None
    table = _NAME_HINT_STR if kind == "string" else _NAME_HINT_INT if kind in ("integer", "number") else ()
    return next((v for rx, v in table if rx.search(name)), None)


def _first_type(schema: dict) -> str | None:
    t = schema.get("type")
    return t[0] if isinstance(t, list) and t else t


def sample(schema: Any, name: str = "", depth: int = 0) -> Any:
    """A schema-valid value, or None when the schema is circular/unknown. Never emits secret examples."""
    if not isinstance(schema, dict) or depth > 5 or "$circular" in schema or "$unresolved" in schema:
        return None
    if name and (is_secret_name(name) or schema.get("format") == "password") and _first_type(schema) in (None, "string"):
        return PLACEHOLDER  # don't reuse spec examples for fields that look like secrets
    if "enum" in schema and schema["enum"]:
        return redact_example(schema["enum"][0], name)
    for key in ("example", "default"):
        if key in schema and schema[key] not in (None, "<redacted>"):
            return redact_example(schema[key], name)
    for combo in ("oneOf", "anyOf"):
        if schema.get(combo):
            return sample(schema[combo][0], name, depth + 1)
    t = _first_type(schema) or ("object" if "properties" in schema else None)
    if t == "string":
        if is_secret_name(name) or schema.get("format") == "password":
            return PLACEHOLDER
        fmt = schema.get("format", "")
        s = _FORMAT_SAMPLES.get(fmt) or (None if schema.get("pattern") else name_hint(name, "string")) \
            or (f"sample-{name}" if name else "sample")
        lo, hi = schema.get("minLength"), schema.get("maxLength")
        if isinstance(lo, int) and len(s) < lo:
            s = s + "x" * (lo - len(s))
        if isinstance(hi, int) and len(s) > hi:
            if not fmt:
                # a cut-off email, url or phone number is no longer valid, so hinted values fall back to a plain string
                s = s[:hi] if s.startswith("sample") else f"sample-{name}"[:hi]
        return s
    if t in ("integer", "number"):
        lo, hi = schema.get("minimum"), schema.get("maximum")
        hint = name_hint(name, t) if not isinstance(lo, (int, float)) and not isinstance(hi, (int, float)) else None
        if hint is not None:
            return hint if t == "integer" else float(hint)
        v: Any = lo if isinstance(lo, (int, float)) else (min(1, hi) if isinstance(hi, (int, float)) else 1)
        if isinstance(lo, (int, float)) and schema.get("exclusiveMinimum") is True:
            v = lo + 1
        return int(v) if t == "integer" else float(v)
    if t == "boolean":
        return True
    if t == "array":
        item = sample(schema.get("items"), name, depth + 1)
        n = max(1, int(schema.get("minItems") or 1))
        return [item] * n if item is not None else []
    if t == "object":
        props = schema.get("properties") or {}
        req = list(schema.get("required") or [])
        keys = [k for k in props if k in req] or [k for k in props][:3]
        out = {}
        for k in keys:
            if isinstance(props.get(k), dict) and props[k].get("readOnly") and k not in req:
                continue
            v = sample(props.get(k), k, depth + 1)
            if v is not None or (isinstance(props.get(k), dict) and props[k].get("nullable")):
                out[k] = v
        return out
    return None


def sample_body(ep: dict) -> Any:
    s = body_schema(ep)
    rb = ep.get("request_body") or {}
    if s:
        val = sample(s)
        if ep.get("method") in ("PUT", "PATCH") and isinstance(val, dict):
            # An update that names its target in the body (PUT /pet) needs an id that exists, so use the spec's example.
            # The user seeds it; a missing record then shows up as a 404, not a bug.
            idp = (s.get("properties") or {}).get("id")
            if "id" not in val and isinstance(idp, dict) and idp.get("example") is not None:
                val = {**val, "id": sample(idp, "id")}
        if val not in (None, {}) or not rb.get("example"):
            return val
    if rb.get("example") not in (None, "<redacted>"):
        return redact_example(rb["example"])
    return {} if rb else None


def param_value(p: dict) -> Any:
    ex = p.get("example")
    if ex not in (None, "<redacted>") and not is_secret_name(p["name"]):
        return ex
    return sample(p.get("schema") or {}, p["name"])


def is_guessed(p: dict) -> bool:
    """True when param_value() would invent the value: no spec example, default, enum, range or format backs it.
    Such values (`status=1`, `url=sample-url`) are often legitimately rejected, so they must not be sent for optional
    parameters and are flagged in the failure text for required ones."""
    if p.get("example") not in (None, "<redacted>") or is_secret_name(p["name"]):
        return False
    sch = p.get("schema") or {}
    if any(sch.get(k) not in (None, "<redacted>") for k in ("example", "default")) or sch.get("enum"):
        return False
    t = _first_type(sch)
    if t == "string":
        return not sch.get("format") and not sch.get("pattern")
    if t in ("integer", "number"):
        return not isinstance(sch.get("minimum"), (int, float)) and not isinstance(sch.get("maximum"), (int, float))
    return t in ("boolean", "array", "object", None)


_ID_NAME_RE = re.compile(r"(^|[^a-z])(id|ids|uuid|guid|slug|username)$", re.I)   # id, user_id, user-id, ID
_CAMEL_ID_RE = re.compile(r"[a-z](Id|Ids|ID|Uuid|UUID|Guid|GUID)$")               # petId, userID, but not "valid" or "paid"


def is_id_param(p: dict, ep: dict) -> bool:
    """Path parameter that names a stored resource (so an unknown value should be a 404), as opposed to a value the
    server computes from (`/redirect/{n}`, `/cache/{value}`, `/anything/{anything}`). Judged from the name and format;
    an endpoint that documents a 404 is a lookup whatever the parameter is called."""
    sch = p.get("schema") or {}
    if sch.get("enum"):
        return False
    if sch.get("format") in ("uuid", "objectid") or _ID_NAME_RE.search(p["name"]) or _CAMEL_ID_RE.search(p["name"]):
        return True
    return 404 in error_codes(ep) and len(path_params(ep)) == 1


def baseline(ep: dict) -> dict:
    """Valid baseline request pieces for runtime Endpoint(): path ids, required query/header, body."""
    pp = {}
    for p in path_params(ep):
        sch = p.get("schema") or {}
        pp[p["name"]] = {"default": p.get("example") if not is_secret_name(p["name"]) else None,
                         "type": _first_type(sch) or "string", "format": sch.get("format")}
    query = {p["name"]: v for p in params_in(ep, "query") if p.get("required")
             and (v := param_value(p)) is not None}
    headers = {p["name"]: v for p in params_in(ep, "header") if p.get("required")
               and not is_secret_name(p["name"]) and p["name"].lower() not in ("authorization", "content-type", "accept")
               and (v := param_value(p)) is not None}
    return {"path_params": pp, "query": query, "headers": headers}


def optional_query(ep: dict) -> dict:
    return {p["name"]: v for p in params_in(ep, "query") if not p.get("required")
            and not is_secret_name(p["name"]) and not is_guessed(p) and (v := param_value(p)) is not None}


def guessed_query(ep: dict) -> list[str]:
    """Names of required query params in the baseline whose value is a guess (see is_guessed)."""
    return [p["name"] for p in params_in(ep, "query") if p.get("required") and is_guessed(p)
            and param_value(p) is not None]


def guessed_body(ep: dict) -> list[str]:
    """`body.<field>` for each required top-level JSON body field whose baseline value is invented (see is_guessed)."""
    sch = body_schema(ep)
    if not sch or not isinstance(sch.get("properties"), dict) or not has_json_body(ep):
        return []
    return [f"body.{k}" for k in sch.get("required") or []
            if isinstance(sch["properties"].get(k), dict) and not is_secret_name(k)
            and is_guessed({"name": k, "schema": sch["properties"][k]})]


_UNIQUE_NAME_RE = re.compile(
    r"(^|[_-])(e-?mail|username|user[_-]?name|login|handle|slug|sku|code|name|reference)$|(Email|Username|UserName|Login|Handle|Slug|Sku|SKU|Code|Name|Reference)$",
    re.I)


_CREDENTIAL_PATH_RE = re.compile(r"login|log-in|signin|sign-in|auth|token|session", re.I)


def unique_body_fields(ep: dict) -> dict:
    """Top-level string fields of a create body that a server often keeps unique (email, username, slug, name ...).
    The runtime adds a short run tag to them so repeated creates do not collide. Fields with an enum, a pattern or a
    format other than email are left alone, because a tagged value could be rejected. Login and token endpoints are skipped:
    they need a username that already exists."""
    sch = body_schema(ep)
    if ep.get("method") != "POST" or _CREDENTIAL_PATH_RE.search(ep.get("path") or "") or not sch or not isinstance(sch.get("properties"), dict) or not has_json_body(ep):
        return {}
    out = {}
    for k, v in sch["properties"].items():
        if (isinstance(v, dict) and _first_type(v) == "string" and _UNIQUE_NAME_RE.search(k) and not v.get("readOnly")
                and not is_secret_name(k) and "enum" not in v and "pattern" not in v and v.get("format") in (None, "", "email")):
            out[k] = {"max": v["maxLength"]} if isinstance(v.get("maxLength"), int) else {}
    return out


def writable_props(schema: dict | None) -> dict:
    if not schema or not isinstance(schema.get("properties"), dict):
        return {}
    return {k: v for k, v in schema["properties"].items()
            if isinstance(v, dict) and not v.get("readOnly") and "$circular" not in v}


def with_(body: dict, **changes: Any) -> dict:
    out = dict(body)
    out.update(changes)
    return out


def without(body: dict, key: str) -> dict:
    return {k: v for k, v in body.items() if k != key}


# --------------------------------------------------------------------------- module rendering
def doc_safe(text: str) -> str:
    """Text for a generated docstring or comment: one line, no quotes/backslashes that could end the string."""
    return re.sub(r'["\\\r\n]', "_", str(text))


class Module:
    def __init__(self, title: str, ttype: str, rerun: str):
        self.title, self.ttype, self.rerun = title, ttype, rerun
        self.blocks: list[str] = []
        self.names: set[str] = set()
        self.tests = 0

    def unique(self, name: str) -> str:
        name = ident(name)
        base, n = name, 2
        while name in self.names:
            name, n = f"{base}_{n}", n + 1
        self.names.add(name)
        return name

    def add(self, src: str, is_test: bool = True) -> None:
        if is_test and "require_destructive()" in src:  # generated code only, no spec text. The marker lets conftest run these last
            src = "@pytest.mark.destructive\n" + src
        self.blocks.append(src.rstrip() + "\n")
        self.tests += is_test

    def render(self, extra_imports: str = "") -> str:
        head = (f'"""{self.title} (type: {self.ttype}). Generated file, regenerate instead of editing.\n\n'
                f'Rerun: {doc_safe(self.rerun)}\n"""\n'
                "import os  # noqa: F401\n\nimport pytest  # noqa: F401\n\nimport apitest_runtime as rt\nfrom endpoints import EP\n" + extra_imports + "\n")
        return head + "\n\n".join(self.blocks)


def case_table(name: str, cases: list[tuple]) -> str:
    rows = ",\n".join("    (" + ", ".join(py(x) for x in c) + ")" for c in cases)
    return f"{name} = [\n{rows},\n]\n"
