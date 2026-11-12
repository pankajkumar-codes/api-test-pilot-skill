"""Shared helpers for the intake parsers (stdlib only, offline).

- load_document(): read JSON or YAML (PyYAML if installed, else a built-in YAML subset reader)
- redaction helpers: parsers never emit secret values, only env-var placeholders
- normalized output shape is documented in references/intake.md
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

# Single source of truth for "does this name look like it carries a secret?" (keys, params, headers).
SECRET_KEY_RE = re.compile(
    r"(pass(word|wd|phrase)?|pwd|secret|token|api[-_]?key|apikey|authorization|auth|bearer|credential|"
    r"session|sess|sid|cookie|private[-_]?key|signature|sig|jwt|otp|pin|cvv|ssn|salt|nonce|code[-_]?verifier|"
    r"access[-_]?key|client[-_]?secret)",
    re.I,
)
USERINFO_RE = re.compile(r"(?<=//)[^/@\s]*@")
SECRET_HEADERS = {
    "authorization", "proxy-authorization", "cookie", "set-cookie",
    "x-api-key", "x-auth-token", "x-access-token", "x-csrf-token",
}


class IntakeError(Exception):
    """User-facing parse error (printed without a traceback by the CLIs)."""


def load_intake_json(path: str) -> dict:
    """Read an intake.json written by intake.py; raise IntakeError (never a traceback) for anything else."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise IntakeError(f"Cannot read {path}: {exc.strerror or exc}") from exc
    except (ValueError, RecursionError) as exc:  # includes UnicodeDecodeError and over-nested JSON
        raise IntakeError(f"{path} is not valid intake JSON ({type(exc).__name__}); create it with intake.py") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("endpoints"), list):
        raise IntakeError(f"{path} has no 'endpoints' list; it must be the output of intake.py")
    return doc


# --------------------------------------------------------------------------- loading
def load_document(path: str | Path) -> Any:
    p = Path(path)
    if str(path).startswith(("http://", "https://")):
        raise IntakeError("Remote URLs are not fetched (offline by design). Download the file and pass its path.")
    if not p.is_file():
        raise IntakeError(f"File not found: {path}")
    text = p.read_text(encoding="utf-8-sig")
    return loads(text, hint=p.suffix.lower())


def loads(text: str, hint: str = "") -> Any:
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")) and hint != ".yaml" and hint != ".yml":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            if hint == ".json":
                raise IntakeError(f"Invalid JSON: {exc}") from exc
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except ImportError:
        return mini_yaml(text)
    except Exception as exc:  # PyYAML error
        raise IntakeError(f"Invalid YAML: {exc}") from exc


# --------------------------------------------------------------------------- mini YAML
def _strip_comment(s: str) -> str:
    q = None
    for i, ch in enumerate(s):
        if q:
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
    return s.rstrip()


def _split_top(s: str, sep: str = ",") -> list[str]:
    out, depth, q, cur = [], 0, None, []
    for ch in s:
        if q:
            cur.append(ch)
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
            cur.append(ch)
        elif ch in "[{":
            depth += 1
            cur.append(ch)
        elif ch in "]}":
            depth -= 1
            cur.append(ch)
        elif ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur))
    return out


def _find_key_sep(s: str) -> int:
    """Index of the ':' ending a mapping key (outside quotes), or -1."""
    q = None
    for i, ch in enumerate(s):
        if q:
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == ":" and (i + 1 == len(s) or s[i + 1] in " \t"):
            return i
    return -1


def _scalar(s: str) -> Any:
    s = s.strip()
    if s == "":
        return None
    if s[0] in "[{":
        return _flow(s)
    if s[0] == '"' and s[-1] == '"' and len(s) >= 2:
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return s[1:-1]
    if s[0] == "'" and s[-1] == "'" and len(s) >= 2:
        return s[1:-1].replace("''", "'")
    if s[0] in "&*!":
        raise IntakeError("YAML anchors/aliases/tags need PyYAML: pip install pyyaml (or convert the spec to JSON).")
    low = s.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if re.fullmatch(r"[-+]?\d+", s):
        return int(s)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", s):
        return float(s)
    return s


def _flow(s: str) -> Any:
    s = s.strip()
    if s.startswith("["):
        return [_scalar(x) for x in _split_top(s[1:-1])]
    if s.startswith("{"):
        d: dict[str, Any] = {}
        for part in _split_top(s[1:-1]):
            i = _find_key_sep(part.strip() + " ")
            if i < 0:
                d[str(_scalar(part))] = None
            else:
                part = part.strip()
                d[str(_scalar(part[:i]))] = _scalar(part[i + 1:])
        return d
    return _scalar(s)


def mini_yaml(text: str) -> Any:
    """Parse the YAML subset used by typical OpenAPI files.

    Supports block maps/lists, quoted and plain scalars, flow [] / {}, block scalars (| >),
    comments. Not supported: anchors/aliases, tags, multi-document, multi-line plain scalars.
    """
    raw = text.replace("\t", "  ").splitlines()
    lines: list[list] = []  # [indent, text, raw]
    for r in raw:
        t = _strip_comment(r)
        if not t.strip() or t.strip() in ("---", "..."):
            continue
        lines.append([len(t) - len(t.lstrip(" ")), t.strip(), r])
    if not lines:
        return None
    value, _ = _block(lines, 0, lines[0][0])
    return value


def _block(lines: list[list], i: int, indent: int) -> tuple[Any, int]:
    if lines[i][1] == "-" or lines[i][1].startswith("- "):
        return _list(lines, i, indent)
    return _map(lines, i, indent)


def _child(lines, i, indent, allow_same_list=False):
    """Parse the nested value that follows a 'key:' or '-' line at `indent`."""
    if i < len(lines):
        ni, nt = lines[i][0], lines[i][1]
        if ni > indent:
            return _block(lines, i, ni)
        if allow_same_list and ni == indent and (nt == "-" or nt.startswith("- ")):
            return _list(lines, i, indent)
    return None, i


def _map(lines, i, indent):
    out: dict[str, Any] = {}
    while i < len(lines) and lines[i][0] == indent:
        text = lines[i][1]
        if text == "-" or text.startswith("- "):
            break
        k = _find_key_sep(text)
        if k < 0:
            raise IntakeError(f"YAML parse error near: {text[:60]!r}")
        key = str(_scalar(text[:k]))
        rest = text[k + 1:].strip()
        i += 1
        if rest == "":
            val, i = _child(lines, i, indent, allow_same_list=True)
        elif re.fullmatch(r"[|>][+-]?\d*", rest):
            parts = []
            while i < len(lines) and lines[i][0] > indent:
                parts.append(lines[i][2].strip())
                i += 1
            val = ("\n" if rest[0] == "|" else " ").join(parts)
        else:
            val = _scalar(rest)
        out[key] = val
    return out, i


def _list(lines, i, indent):
    out: list[Any] = []
    while i < len(lines) and lines[i][0] == indent and (lines[i][1] == "-" or lines[i][1].startswith("- ")):
        text = lines[i][1]
        rest = text[1:].lstrip(" ")
        if rest == "":
            i += 1
            val, i = _child(lines, i, indent)
            out.append(val)
        elif _find_key_sep(rest) >= 0 and rest[0] not in "[{\"'":
            offset = len(text) - len(rest)
            lines[i] = [indent + offset, rest, lines[i][2]]
            val, i = _map(lines, i, indent + offset)
            out.append(val)
        elif _find_key_sep(rest) >= 0 and rest[0] in "\"'" and _find_key_sep(rest) < len(rest) - 1 and rest[_find_key_sep(rest) - 1] in "\"'":
            offset = len(text) - len(rest)
            lines[i] = [indent + offset, rest, lines[i][2]]
            val, i = _map(lines, i, indent + offset)
            out.append(val)
        else:
            out.append(_scalar(rest))
            i += 1
    return out, i


# --------------------------------------------------------------------------- redaction
def env_name(scheme: str, suffix: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "_", scheme).strip("_").upper() or "API"
    return f"APITEST_{base}_{suffix}"


def is_secret_name(name: Any) -> bool:
    return isinstance(name, str) and bool(SECRET_KEY_RE.search(name))


def redact_example(value: Any, key: str = "") -> Any:
    """Replace values of secret-looking keys in example payloads (any non-container value)."""
    if isinstance(value, dict):
        return {k: redact_example(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_example(v, key) for v in value]
    if key and is_secret_name(key) and value not in ("", None):
        return "<redacted>"
    return value


REDACTED = "<redacted>"
_FALSY_OR_TRUTHY = {"0", "1", "true", "false", "yes", "no", "y", "n", "on", "off"}
_HEADER_LINE_RE = re.compile(
    r"(?im)(?<![A-Za-z0-9_-])(?P<k>authorization|proxy-authorization|cookie|set-cookie|x-api-key|x-auth-token|x-access-token|"
    r"x-csrf-token)(?P<sep>\s*:\s*)(?P<v>[^\r\n'\"]+)")
_CURL_USER_RE = re.compile(r"(?<![\w-])(?P<f>-u|--user|--proxy-user)(?P<sep>\s+|=)(?:'[^'\n]*'|\"[^\"\n]*\"|\S+)")
_BEARER_RE = re.compile(r"(?i)\b(?P<k>bearer)(?P<sep>\s+)[A-Za-z0-9._~+/=-]{6,}")
_TOKEN_SHAPES_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*"          # JWT
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}|\bAKIA[0-9A-Z]{16}\b|\bsk-[A-Za-z0-9_-]{20,}|\bxox[baprs]-[A-Za-z0-9-]{10,}")
_KEYSEP_RE = re.compile(
    r"""(?P<key>(?<![A-Za-z0-9_.-])[A-Za-z][A-Za-z0-9_.-]{0,63})(?P<sep>(?:\\?["'])?[ \t]*(?::|=>|=)[ \t]*)""")
_VALUE_RE = re.compile(r"""\\?"(?:[^"\\\n]|\\.)*"|\\"[^"\\\n]*\\"|'[^'\n]*'|[^\s,;&}\])'"]+""")
MAX_REDACT_CHARS = 200_000


def secret_values_from_env(environ: "dict[str, str] | None" = None) -> list[str]:
    """Values of secret-looking APITEST_* variables (tokens, passwords, keys), to scrub wherever they appear in free text."""
    import os
    env = os.environ if environ is None else environ
    out = []
    for name, value in env.items():
        if name.startswith("APITEST_") and is_secret_name(name) and len(value) >= 4 and value.strip().lower() not in _FALSY_OR_TRUTHY:
            out.append(value)
    return out


def redact_text(text: str, secrets: "list[str] | tuple[str, ...]" = ()) -> str:
    """Scrub secrets from untrusted free text (failure messages, curl lines, URLs, JSON snippets).

    Defence in depth for the report: generated suites already redact their own output, but result files may come from any
    pytest run. Removes: the exact values in `secrets`, URL userinfo, Authorization/Cookie-style header lines, Bearer tokens,
    JWT and well-known token shapes, `curl -u`, and values of secret-looking keys in JSON, key=value, headers and query strings.
    Over-redaction is accepted (same broad SECRET_KEY_RE as everywhere else); leaking is not.
    """
    import urllib.parse
    s = text[:MAX_REDACT_CHARS]
    for v in sorted({x for x in secrets if x and len(x) >= 4}, key=len, reverse=True):
        for variant in {v, urllib.parse.quote(v, safe=""), urllib.parse.quote_plus(v)}:
            s = s.replace(variant, REDACTED)
    s = USERINFO_RE.sub(REDACTED + "@", s)
    s = _HEADER_LINE_RE.sub(lambda m: m.group("k") + m.group("sep") + REDACTED, s)
    s = _CURL_USER_RE.sub(lambda m: m.group("f") + m.group("sep") + REDACTED, s)
    s = _BEARER_RE.sub(lambda m: m.group("k") + m.group("sep") + REDACTED, s)
    s = _TOKEN_SHAPES_RE.sub(REDACTED, s)

    # Manual scan (linear): a non-secret key must not swallow the text after it (`http://h/x?token=...` starts with key `http`),
    # and the value is only parsed for secret keys, so a long run of `a=a=a=...` cannot make this quadratic.
    out, pos = [], 0
    while True:
        m = _KEYSEP_RE.search(s, pos)
        if not m:
            out.append(s[pos:])
            return "".join(out)
        out.append(s[pos:m.end("sep")])
        pos = m.end("sep")
        if is_secret_name(m.group("key")):
            v = _VALUE_RE.match(s, pos)
            if v and REDACTED not in v.group(0):
                q = next((x for x in ('\\"', '"', "'") if v.group(0).startswith(x)), "")
                out.append(q + REDACTED + q)   # keep JSON/shell quoting valid
                pos = v.end()


def strip_userinfo(url: str) -> tuple[str, bool]:
    """Remove user:pass@ from a URL string. Returns (clean_url, was_stripped)."""
    clean = USERINFO_RE.sub("", url, count=1)
    return clean, clean != url


def slug(method: str, path: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")
    return f"{method.upper()}_{s or 'root'}"


def unique_ids(endpoints: list[dict], warnings: list[str]) -> None:
    seen: dict[str, int] = {}
    for ep in endpoints:
        eid = ep["id"]
        if eid in seen:
            seen[eid] += 1
            new = f"{eid}_{seen[eid]}"
            warnings.append(f"Duplicate endpoint id '{eid}' renamed to '{new}'")
            ep["id"] = new
        else:
            seen[eid] = 1


# --------------------------------------------------------------------------- output
def emit(doc: dict, out: str | None, summary: bool) -> None:
    if summary:
        print(summarize(doc))
        if not out:
            return
    text = json.dumps(doc, indent=2, ensure_ascii=False)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {out} ({len(doc['endpoints'])} endpoints)", file=sys.stderr)
    else:
        print(text)


def summarize(doc: dict) -> str:
    src = doc.get("source", {})
    rows = [f"Source: {src.get('type')} {src.get('version', '')}".strip()]
    if doc.get("servers"):
        rows.append("Servers: " + ", ".join(doc["servers"]))
    if doc.get("security_schemes"):
        rows.append("Auth schemes: " + ", ".join(
            f"{n} ({s.get('type')}{'/' + s['scheme'] if s.get('scheme') else ''})" for n, s in doc["security_schemes"].items()))
    rows.append(f"Endpoints: {len(doc['endpoints'])}")
    rows.append("")
    rows.append(f"{'METHOD':7} {'PATH':45} AUTH")
    for ep in doc["endpoints"]:
        auth = "required" if ep.get("auth_required") else "public"
        rows.append(f"{ep['method']:7} {ep['path']:45} {auth}")
    for w in doc.get("warnings", []):
        rows.append(f"WARNING: {w}")
    return "\n".join(rows)


def cli_main(argv: list[str] | None, prog: str, description: str, build) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog=prog, description=description)
    ap.add_argument("input", help="path to the input file (local only)")
    ap.add_argument("-o", "--output", help="write JSON here instead of stdout")
    ap.add_argument("--summary", action="store_true", help="print a human-readable table")
    args = ap.parse_args(argv)
    try:
        doc = build(args.input)
    except IntakeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    emit(doc, args.output, args.summary)
    return 0
