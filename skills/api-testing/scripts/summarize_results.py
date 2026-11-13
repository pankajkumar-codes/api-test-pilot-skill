#!/usr/bin/env python3
"""Turn pytest (JUnit XML) and k6 (--summary-export JSON) results into one Markdown report. Stdlib only, offline.

Usage:
  python summarize_results.py --junit results/pytest.xml [--k6 results/load-summary.json ...]
         [--intake intake.json] [--title NAME] [--base-url URL] [--tests-dir api_tests] [--k6-dir k6_tests]
         [-o reports/report.md | -o -] [--max-findings 100] [--date YYYY-MM-DD]

Produce the inputs with:
  python -m pytest api_tests -p no:cacheprovider --junitxml=results/pytest.xml
  python scripts/run_k6.py k6_tests/load.js            # writes results/load-summary.json

The report follows templates/report.md: summary, per-endpoint coverage, k6 numbers, findings ranked by severity (each with a
reproducible command), run problems and limitations. It never runs a test and never contacts the target.

Result files are untrusted input (any pytest run or hand-edited file can end up here):
  * nothing is executed or imported from them; XML is parsed with expat directly (DOCTYPE, entity declarations and external
    entities are refused), JSON with the json module; both are size-, depth- and count-bounded;
  * every string is scrubbed with _common.redact_text (+ the values of secret APITEST_* env vars) BEFORE it is truncated,
    then Markdown-escaped, or placed in a fenced block whose fence is longer than anything inside it;
  * system-out/system-err are never read; commands in the report are built from validated parts and shell-quoted.
Exit codes: 0 report written, 2 bad input.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import shlex
import stat
import sys
import unicodedata
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.parsers import expat

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402  (shared redaction helper)

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "report.md"
MAX_XML_BYTES = 25 * 1024 * 1024
MAX_JSON_BYTES = 10 * 1024 * 1024
MAX_CASES = 100_000
MAX_XML_DEPTH = 32
MAX_FIELD_CHARS = 20_000      # per attribute / element text kept from a result file
MAX_METRICS = 5_000
MAX_CHECKS = 200
RANK = {"passed": 0, "skipped": 1, "error": 2, "failed": 3}
SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
# Used when a failing test carries no [SEVERITY] tag of its own; the report says "default rating" for these.
DEFAULT_SEVERITY = {"smoke": "HIGH", "security": "HIGH", "fuzz": "HIGH", "positive": "MEDIUM", "negative": "MEDIUM",
                    "contract": "MEDIUM", "integration": "MEDIUM", "data-validation": "MEDIUM", "edge": "LOW", "resilience": "LOW", "chaos": "MEDIUM"}
INTRUSIVE_TYPES = {"security", "fuzz", "resilience", "load", "stress", "spike", "soak", "chaos"}
GATE_NOTE = ("Needs a non-production target you own or are authorized to test. Export APITEST_TARGET_ENV and "
             "APITEST_AUTHORIZED=yes yourself (loopback targets pass without them); production is always refused.")
WRITE_NOTE = ("WARNING: the curl line above sends a write request (POST, PUT, PATCH or DELETE). Running it changes or deletes data on the "
              "target; run it only against a target whose data you can lose.")
REDACTED_NOTE = ("The curl line is redacted: replace each `<redacted>` header or value with your own credentials (from your environment, "
                 "never pasted into a report) before it will work.")
WRITE_CURL_RE = re.compile(r"^curl -sS -i -X (POST|PUT|PATCH|DELETE) ")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE")
REQUEST_LINE_RE = re.compile(r"^\s*(" + "|".join(METHODS) + r")\s+(\S+)\s+->", re.M)
REPRO_RE = re.compile(r"^\s*repro:\s*(curl\s.*)$", re.M)
OWASP_RE = re.compile(r"^API\d{1,2}(:\d{4})?$", re.I)
SAFE_DIR_RE = re.compile(r"^(?!-)(?!.*\.\.)[A-Za-z0-9_./-]{1,100}$")
CHAOS_RERUN_RE = re.compile(r"^rerun: ((?:[A-Za-z_][A-Za-z0-9_]*=[A-Za-z0-9_./:@%+,-]+ )?python [A-Za-z0-9_./-]*chaos_run\.py [A-Za-z0-9_./:=,@%+ -]{1,500})$", re.M)
SAFE_STEM_RE = re.compile(r"^test_[A-Za-z0-9_]{1,80}$")
K6_FILE_RE = re.compile(r"^([A-Za-z0-9_-]{1,40})-summary\.json$")
THRESHOLD_RE = re.compile(r"^\s*(?P<agg>[A-Za-z_]+(?:\([0-9.]+\))?)\s*(?P<op><=|>=|==|!=|<|>|=)\s*(?P<lim>-?[0-9.]+(?:e[+-]?\d+)?)\s*$", re.I)
K6_SUB_RE = re.compile(r"^(http_req_duration|http_req_failed)\{(endpoint|stage):([A-Za-z0-9_.-]{1,64})\}$")


class ReportError(Exception):
    """User-facing error (bad or hostile input); printed without a traceback."""


# --------------------------------------------------------------------------- safe text
_SECRETS: list[str] = []


def init_secrets(environ: dict | None = None) -> None:
    _SECRETS[:] = _common.secret_values_from_env(environ)


def _strip_controls(s: str, keep_newlines: bool) -> str:
    out = []
    for ch in s:
        if ch == "\n" and keep_newlines:
            out.append(ch)
        elif ch in "\r\t  \n":
            out.append("\n" if (ch in "\r  " and keep_newlines) else " ")
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):   # controls, bidi/zero-width, surrogates, unassigned
            continue
        else:
            out.append(ch)
    return "".join(out)


def plain(value: Any, limit: int = 200, keep_newlines: bool = False) -> str:
    """Untrusted value -> bounded, scrubbed, control-free text (not yet Markdown-safe). Redact BEFORE truncating."""
    s = value if isinstance(value, str) else ("" if value is None else str(value))
    s = _common.redact_text(s[:MAX_FIELD_CHARS], _SECRETS)
    s = _strip_controls(s, keep_newlines)
    if keep_newlines:
        s = re.sub(r"\n{3,}", "\n\n", s)
    return s if len(s) <= limit else s[: max(0, limit - 1)] + "…"


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+!|~<>&$@])")


def esc(text: str) -> str:
    """Escape text for Markdown prose and table cells (also defuses HTML, autolinks and table breakouts)."""
    t = _MD_SPECIAL.sub(r"\\\1", text)
    t = t.replace("://", ":\\/\\/").replace("www.", "www\\.")
    return re.sub(r"^(\s*)([-=>]|\d+[.)])", r"\1\\\2", t)


def md(value: Any, limit: int = 200) -> str:
    return esc(plain(value, limit))


def fence(text: str) -> str:
    """Fenced code block whose fence cannot be closed by the content."""
    runs = [len(m) for m in re.findall(r"`+", text)]
    f = "`" * max(3, (max(runs) + 1) if runs else 3)
    return f"{f}\n{text}\n{f}"


def num(v: Any) -> float | None:
    """A finite number or None (bools, NaN, inf and strings are not numbers)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def fmt(v: float | None, unit: str = "") -> str:
    if v is None:
        return "n/a"
    return (f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.4g}") + unit


# --------------------------------------------------------------------------- bounded reading
def read_bounded(path: str, cap: int) -> bytes:
    try:
        st = os.stat(path)
    except OSError as e:
        raise ReportError(f"cannot read {plain(path, 120)}: {e.strerror or e}") from None
    if not stat.S_ISREG(st.st_mode):
        raise ReportError(f"{plain(path, 120)} is not a regular file")
    with open(path, "rb") as f:
        data = f.read(cap + 1)
    if len(data) > cap:
        raise ReportError(f"{plain(path, 120)} is larger than {cap // (1024 * 1024)} MiB; refusing to parse it")
    return data


def load_json(path: str, cap: int = MAX_JSON_BYTES) -> tuple[Any, str]:
    """(document, sha256[:12] of the exact bytes parsed)."""
    import json

    def bad_constant(c: str):
        raise ValueError(f"constant {c} not allowed")

    data = read_bounded(path, cap)
    try:
        return json.loads(data.decode("utf-8-sig"), parse_constant=bad_constant), hashlib.sha256(data).hexdigest()[:12]
    except (ValueError, RecursionError, UnicodeDecodeError) as e:
        raise ReportError(f"{plain(path, 120)} is not valid JSON ({type(e).__name__})") from None


# --------------------------------------------------------------------------- JUnit XML (streaming, expat only)
class _Refuse(Exception):
    pass


def parse_junit(path: str) -> tuple[list[dict], str]:
    """(testcases, sha256[:12]). Cases are dicts: classname, name, time, status (passed|failed|error|skipped), message, text.
    Never builds a tree and never reads system-out/system-err."""
    data = read_bounded(path, MAX_XML_BYTES)
    cases: list[dict] = []
    state = {"depth": 0, "case": None, "case_depth": 0, "child": None, "parts": []}

    def start(tag: str, attrs: dict) -> None:
        state["depth"] += 1
        if state["depth"] > MAX_XML_DEPTH:
            raise _Refuse("XML nested too deeply")
        if tag == "testcase" and state["case"] is None:
            if len(cases) >= MAX_CASES:
                raise _Refuse(f"more than {MAX_CASES} test cases")
            time_s = None
            try:
                time_s = num(float(attrs.get("time", "")))
            except ValueError:
                pass
            state["case"] = {"classname": attrs.get("classname", "")[:300], "name": attrs.get("name", "")[:500],
                             "time": time_s, "status": "passed", "message": "", "text": ""}
            state["case_depth"] = state["depth"]
        elif state["case"] is not None and state["depth"] == state["case_depth"] + 1 and tag in ("failure", "error", "skipped"):
            state["child"] = tag
            state["parts"] = []
            c = state["case"]
            new = {"failure": "failed"}.get(tag, tag)
            if RANK[new] > RANK[c["status"]]:
                c["status"] = new
                c["message"] = attrs.get("message", "")[:MAX_FIELD_CHARS]

    def end(tag: str) -> None:
        if state["child"] == tag and state["depth"] == state["case_depth"] + 1:
            if RANK[{"failure": "failed"}.get(tag, tag)] >= RANK[state["case"]["status"]]:
                state["case"]["text"] = "".join(state["parts"])[:MAX_FIELD_CHARS]
            state["child"] = None
        if tag == "testcase" and state["case"] is not None and state["depth"] == state["case_depth"]:
            cases.append(state["case"])
            state["case"] = None
        state["depth"] -= 1

    def chars(s: str) -> None:
        if state["child"] and sum(map(len, state["parts"])) < MAX_FIELD_CHARS:
            state["parts"].append(s)

    def refuse(*_a):
        raise _Refuse("DOCTYPE, entity declarations and external entities are not accepted")

    p = expat.ParserCreate()
    p.buffer_text = True
    p.StartElementHandler, p.EndElementHandler, p.CharacterDataHandler = start, end, chars
    p.StartDoctypeDeclHandler = refuse
    p.EntityDeclHandler = refuse
    p.ExternalEntityRefHandler = refuse
    p.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        p.Parse(data, True)
    except _Refuse as e:
        raise ReportError(f"{plain(path, 120)}: {e}") from None
    except expat.ExpatError as e:
        raise ReportError(f"{plain(path, 120)} is not valid XML ({expat.ErrorString(e.code)})") from None
    return cases, hashlib.sha256(data).hexdigest()[:12]


# --------------------------------------------------------------------------- intake (endpoint names only)
def _norm(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


class EndpointIndex:
    """Maps a test name or a request line to an endpoint id. Only id/method/path are read from the intake JSON."""

    def __init__(self, doc: Any = None):
        self.eps: list[dict] = []
        self.by_norm: dict[str, dict] = {}
        self.static: dict[tuple[str, str], dict] = {}
        self.templated: list[tuple[str, re.Pattern, dict]] = []
        if doc is None:
            return
        if not isinstance(doc, dict) or not isinstance(doc.get("endpoints"), list):
            raise ReportError("intake file has no 'endpoints' list (expected the output of intake.py)")
        for raw in doc["endpoints"][:5000]:
            if not isinstance(raw, dict) or not all(isinstance(raw.get(k), str) for k in ("id", "method", "path")):
                continue
            ep = {"id": plain(raw["id"], 80), "method": plain(raw["method"], 10).upper(), "path": plain(raw["path"], 200)}
            self.eps.append(ep)
            self.by_norm.setdefault(_norm(raw["id"]), ep)
            if "{" in raw["path"]:
                rx = "^" + "".join("[^/]+" if re.fullmatch(r"\{[^{}/]+\}", part) else re.escape(part)
                                   for part in re.split(r"(\{[^{}/]+\})", raw["path"])) + "/?$"
                self.templated.append((ep["method"], re.compile(rx), ep))
            else:
                self.static.setdefault((ep["method"], raw["path"].rstrip("/") or "/"), ep)

    def by_test_name(self, name: str) -> dict | None:
        toks = [t for t in re.split(r"[^A-Za-z0-9]+", name) if t]
        best = None
        for i in range(len(toks)):
            for j in range(i + 1, min(len(toks), i + 8) + 1):
                ep = self.by_norm.get("_".join(toks[i:j]))
                if ep and (best is None or len(ep["id"]) >= len(best["id"])):
                    best = ep
        return best

    def by_request(self, method: str, url: str) -> dict | None:
        path = urllib.parse.urlsplit(url).path.rstrip("/") or "/"
        ep = self.static.get((method, path))
        if ep:
            return ep
        for m, rx, ep in self.templated:
            if m == method and rx.match(path):
                return ep
        return None

    def by_k6_tag(self, tag: str) -> dict | None:
        for n, ep in self.by_norm.items():
            if n[:40] == tag:
                return ep
        return None


def ep_key(ep: dict) -> str:
    return f"{ep['method']} {ep['path']}  ({ep['id']})"


# --------------------------------------------------------------------------- findings
class Finding:
    def __init__(self, severity, title, endpoint, kind, rating, lines, commands, owasp="", note=""):
        self.severity, self.title, self.endpoint, self.kind, self.rating = severity, title, endpoint, kind, rating
        self.lines, self.commands, self.owasp, self.note = lines, commands, owasp, note


def case_type(classname: str) -> tuple[str, str]:
    stem = classname.rsplit(".", 1)[-1]
    return (stem[5:].replace("_", "-") if stem.startswith("test_") else (stem or "other").replace("_", "-")), stem


def _tags(line: str) -> tuple[str | None, str, str]:
    """Leading [SEVERITY] / [API1:2023] tags of one line -> (severity, owasp, rest)."""
    sev, owasp = None, ""
    while True:
        m = re.match(r"^\[([^\]]{1,24})\]\s*", line)
        if not m:
            return sev, owasp, line
        tag = m.group(1).strip()
        if tag.upper() in SEVERITIES:
            sev = sev or tag.upper()
        elif OWASP_RE.match(tag):
            owasp = owasp or tag.upper()
        else:
            return sev, owasp, line
        line = line[m.end():]


def split_message(raw: str) -> tuple[str | None, str, str, list[str], list[str]]:
    """(severity, owasp, verdict, detail lines, curl repro lines) from a runtime failure message.
    Severity is the tag on the first line, else the most severe tag on any line (the fuzzer lists several findings per test)."""
    lines = [ln.rstrip() for ln in raw.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    first = re.sub(r"^(Failed|AssertionError):\s*", "", lines[0].strip()) if lines else ""
    sev, owasp, verdict = _tags(first)
    detail, repros = [], []
    for ln in lines[1:]:
        m = REPRO_RE.match(ln)
        if m:
            if m.group(1).strip() not in repros and len(repros) < 3:
                repros.append(m.group(1).strip())
            continue
        if not ln.strip():
            continue
        detail.append(ln.strip())
    if sev is None:
        tagged = [t for t in (_tags(ln)[0] for ln in detail) if t]
        if tagged:
            sev = min(tagged, key=SEVERITIES.index)
    return sev, owasp, verdict, detail, repros


CURL_HEADER_RE = re.compile(r"^[A-Za-z0-9-]{1,64}: [^\r\n]{0,500}$")


def _origin(url: str) -> tuple[str, str, int] | None:
    """(scheme, lowercase hostname, port) of an http(s) URL, or None. Parsed, never prefix-matched."""
    if re.search(r"[\s\\\x00-\x1f]", url):
        return None
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname or u.username is not None or u.password is not None:
        return None
    return u.scheme, u.hostname.lower(), port or (443 if u.scheme == "https" else 80)


def safe_curl(line: str, expected: str | None) -> str | None:
    """Accept only the exact shape the runtime writes: `curl -sS -i -X METHOD URL [-H 'K: V']... [--data-raw BODY]`.
    The line is parsed with shlex (never run), every part is validated, the URL's origin must equal the target's, and the
    command is REBUILT with shlex.quote, so nothing from the result file can add a pipe, a second command or another host."""
    if not expected or len(line) > 4000:
        return None
    line = _common.redact_text(line, _SECRETS)
    try:
        t = shlex.split(line)
    except ValueError:
        return None
    if len(t) < 6 or t[:4] != ["curl", "-sS", "-i", "-X"] or t[4] not in METHODS:
        return None
    want, got = _origin(expected), _origin(t[5])
    if want is None or got is None or want != got:
        return None
    rest, headers, body = t[6:], [], None
    while rest:
        flag = rest.pop(0)
        if flag == "-H" and rest and CURL_HEADER_RE.match(rest[0]) and body is None:
            headers.append(rest.pop(0))
        elif flag == "--data-raw" and rest and body is None:
            body = rest.pop(0)
        else:
            return None
    out = ["curl", "-sS", "-i", "-X", t[4], t[5]]
    for h in headers:
        out += ["-H", h]
    if body is not None:
        out += ["--data-raw", body]
    return _strip_controls(" ".join(shlex.quote(x) for x in out), False)


def base_prefix(base_url: str | None) -> str:
    if base_url:
        return f"APITEST_BASE_URL={shlex.quote(base_url)}"
    return 'APITEST_BASE_URL="${APITEST_BASE_URL:?set the target URL}"'


def clean_base_url(url: str) -> str:
    """Validate --base-url for use in commands (syntax only: no userinfo, no query, http(s)). No host classification here."""
    if not re.fullmatch(r"https?://[A-Za-z0-9.\-\[\]:_]{1,200}(/[A-Za-z0-9._~/%\-]{0,200})?", url or ""):
        raise ReportError("--base-url must look like http(s)://host[:port][/path] (no credentials, query or spaces)")
    parts = urllib.parse.urlsplit(url)
    if parts.username or parts.password or not parts.hostname:
        raise ReportError("--base-url must not contain credentials and needs a host")
    return url.rstrip("/")


class Report:
    def __init__(self, args, index: EndpointIndex):
        self.args, self.index = args, index
        self.cases: list[dict] = []
        self.findings: list[Finding] = []
        self.problems: dict[str, int] = {}
        self.skips: dict[str, int] = {}
        self.k6: list[dict] = []
        self.inputs: list[tuple[str, str, str]] = []   # (kind, basename, sha256[:12])
        self._raw: dict[tuple[str, str], dict] = {}

    # ---- pytest
    def add_junit(self, path: str) -> None:
        data_cases, digest = parse_junit(path)
        self.inputs.append(("junit", Path(path).name, digest))
        for c in data_cases:   # reruns and merged files: one case per classname+name, the worst outcome wins (later wins ties)
            key = (c["classname"], c["name"])
            old = self._raw.get(key)
            if old is None or RANK[c["status"]] >= RANK[old["status"]]:
                self._raw[key] = c

    def finalize(self) -> None:
        for c in self._raw.values():
            ctype, stem = case_type(c["classname"])
            name = plain(c["name"], 200)
            raw = plain(c["text"] or c["message"], 20_000, keep_newlines=True)
            request = REQUEST_LINE_RE.search(raw)
            ep = self.index.by_test_name(c["name"])
            if ep is None and request and self.index.eps:
                ep = self.index.by_request(request.group(1), request.group(2))
            if ep is not None:
                label = ep_key(ep)
            elif request:
                label = f"{request.group(1)} {plain(urllib.parse.urlsplit(request.group(2)).path or '/', 120)}"
            else:
                label = "(API-wide)"
            rec = {"type": ctype, "stem": stem, "name": name, "status": c["status"], "endpoint": label, "time": c["time"]}
            self.cases.append(rec)
            if c["status"] == "skipped":
                reason = plain(c["message"] or c["text"], 160) or "no reason given"
                self.skips[reason] = self.skips.get(reason, 0) + 1
            elif c["status"] == "error":
                first = plain((raw.strip().split("\n") or [""])[0], 160) or "error without a message"
                key = f"{name}: {first}" if len(self.problems) < 50 else "(further errors not listed)"
                self.problems[key] = self.problems.get(key, 0) + 1
            elif c["status"] == "failed":
                self.findings.append(self._pytest_finding(rec, raw, c["name"], request))
        self._raw = {}

    def _pytest_finding(self, rec: dict, raw: str, raw_name: str, request) -> Finding:
        sev, owasp, verdict, detail, repros = split_message(raw)
        rating = "tagged by the test" if sev else "default for this test type"
        sev = sev or DEFAULT_SEVERITY.get(rec["type"], "MEDIUM")
        tests_dir = self.args.tests_dir
        prefix = base_prefix(self.args.base_url)
        stem_file = f"{tests_dir}/{rec['stem']}.py"
        chaos = CHAOS_RERUN_RE.search(raw) if rec["type"] == "chaos" else None
        if rec["type"] == "chaos":   # chaos results come from chaos_run.py, not pytest: use its own validated rerun line
            cmd = chaos.group(1) if chaos else "python scripts/chaos_run.py <intake.json> --experiments <name> (rerun line missing or not in the expected shape)"
        elif rec["name"] == raw_name and SAFE_STEM_RE.match(rec["stem"]):
            cmd = f"{prefix} python -m pytest {shlex.quote(stem_file + '::' + rec['name'])} -v"
        elif SAFE_STEM_RE.match(rec["stem"]):   # a truncated or scrubbed name is not a valid node id: run the whole file
            cmd = f"{prefix} python -m pytest {shlex.quote(stem_file)} -v"
        else:
            cmd = f"{prefix} python -m pytest {shlex.quote(tests_dir)} -v"
        curls, dropped = [], 0
        for line in repros:
            safe = safe_curl(line, self.args.expected_host)
            if safe:
                curls.append(safe)
            else:
                dropped += 1
        commands = curls + [cmd]
        lines = detail[:12]
        return Finding(sev, verdict or rec["name"], rec["endpoint"], rec["type"], rating,
                       [plain(ln, 220) for ln in lines], commands, owasp,
                       " ".join(x for x in (
                           GATE_NOTE if rec["type"] in INTRUSIVE_TYPES else "",
                           WRITE_NOTE if any(WRITE_CURL_RE.match(c) for c in curls) else "",
                           REDACTED_NOTE if any("<redacted>" in c for c in curls) else "",
                           f"{dropped} curl line(s) from the result file were dropped: not the exact shape the generated runtime writes, or "
                           f"the host does not match the target ({'--base-url' if self.args.base_url else 'pass --base-url to include them'})."
                           if dropped else "") if x))

    # ---- k6
    def add_k6(self, path: str) -> None:
        doc, digest = load_json(path)
        self.inputs.append(("k6", Path(path).name, digest))
        if not isinstance(doc, dict) or not isinstance(doc.get("metrics"), dict):
            raise ReportError(f"{plain(path, 120)} is not a k6 --summary-export file (no 'metrics' object)")
        m = K6_FILE_RE.match(Path(path).name)
        profile = m.group(1) if m else re.sub(r"[^A-Za-z0-9_-]+", "_", Path(path).stem)[:40] or "k6"
        metrics = dict(list(doc["metrics"].items())[:MAX_METRICS])
        info: dict = {"profile": profile, "stages": {}, "endpoints": {}, "thresholds": [], "overall": {}, "checks": []}

        def stat(name: str, key: str) -> float | None:
            v = metrics.get(name)
            return num(v.get(key)) if isinstance(v, dict) else None

        info["overall"] = {
            "requests": stat("http_reqs", "count"), "rps": stat("http_reqs", "rate"),
            "failed_rate": stat("http_req_failed", "value"), "checks_rate": stat("checks", "value"),
            "avg": stat("http_req_duration", "avg"), "p95": stat("http_req_duration", "p(95)"),
            "p99": stat("http_req_duration", "p(99)"), "max": stat("http_req_duration", "max"),
        }
        for name, v in metrics.items():
            if not isinstance(name, str) or not isinstance(v, dict):
                continue
            sub = K6_SUB_RE.match(name)
            if sub:
                metric, dim, tag = sub.groups()
                row = (info["stages"] if dim == "stage" else info["endpoints"]).setdefault(tag, {})
                row["p95" if metric == "http_req_duration" else "failed"] = num(v.get("p(95)" if metric == "http_req_duration" else "value"))
            th = v.get("thresholds")
            if isinstance(th, dict):
                for expr, crossed in list(th.items())[:50]:
                    if isinstance(expr, str) and isinstance(crossed, bool):   # in --summary-export, true = crossed
                        info["thresholds"].append({"metric": name, "expr": expr, "crossed": crossed, "observed": self._observed(v, expr)})
        info["checks"] = self._k6_checks(doc.get("root_group"))
        self.k6.append(info)
        self._k6_findings(info)

    @staticmethod
    def _observed(metric: dict, expr: str) -> float | None:
        m = THRESHOLD_RE.match(expr)
        if not m:
            return None
        agg = m.group("agg")
        return num(metric.get("value" if agg == "rate" and "value" in metric else agg))

    @staticmethod
    def _k6_checks(group: Any, depth: int = 0) -> list[dict]:
        out: list[dict] = []
        if not isinstance(group, dict) or depth > 5:
            return out
        checks = group.get("checks")
        if isinstance(checks, dict):
            for key, c in list(checks.items())[:MAX_CHECKS]:
                if isinstance(c, dict):
                    out.append({"name": plain(c.get("name", key), 120), "passes": num(c.get("passes")) or 0, "fails": num(c.get("fails")) or 0})
        groups = group.get("groups")
        if isinstance(groups, dict):
            for g in list(groups.values())[:50]:
                out += Report._k6_checks(g, depth + 1)
        return out[:MAX_CHECKS]

    def _k6_findings(self, info: dict) -> None:
        profile = info["profile"]
        k6_dir = self.args.k6_dir
        cmd = (f"{base_prefix(self.args.base_url)} python scripts/run_k6.py "
               f"{shlex.quote(f'{k6_dir}/{profile}.js')}")
        for t in info["thresholds"]:
            if not t["crossed"]:
                continue
            metric = t["metric"]
            sev = "HIGH" if (metric.startswith("http_req_failed") or metric.startswith("checks")) else "MEDIUM"
            if profile == "stress":
                sev = "MEDIUM"   # stress runs push past the limit on purpose; the finding marks where it broke
            sub = K6_SUB_RE.match(metric)
            ep = self.index.by_k6_tag(sub.group(3)) if sub and sub.group(2) == "endpoint" else None
            where = ep_key(ep) if ep else (f"{sub.group(2)} {sub.group(3)}" if sub else "(whole run)")
            lim = THRESHOLD_RE.match(t["expr"])
            unit = " ms" if "duration" in metric else ""
            lines = [f"threshold crossed on {metric}: expected {t['expr']}" + (" (ms)" if unit else ""),
                     f"observed {fmt(t['observed'], unit)}" if lim else "observed value not available"]
            if profile == "stress":
                lines.append("stress profile: crossing a threshold marks the breaking point; compare the step numbers to see where.")
            self.findings.append(Finding(sev, f"k6 threshold crossed: {plain(metric, 90)} {plain(t['expr'], 40)}", where, profile,
                                         "default for k6 thresholds", [plain(x, 220) for x in lines], [cmd], note=GATE_NOTE))
        for c in info["checks"]:
            if c["fails"] > 0:
                total = c["passes"] + c["fails"]
                self.findings.append(Finding(
                    "MEDIUM", f"k6 check failed: {c['name']}", "(whole run)", profile, "default for k6 checks",
                    [f"{c['fails']:.0f} of {total:.0f} checks failed (expected 0)"], [cmd], note=GATE_NOTE))


# --------------------------------------------------------------------------- rendering
def table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def render_summary(r: Report) -> tuple[str, str]:
    by: dict[str, dict[str, int]] = {}
    for c in r.cases:
        row = by.setdefault(c["type"], {"passed": 0, "failed": 0, "skipped": 0, "error": 0})
        row[c["status"]] += 1
    tot = {k: sum(v[k] for v in by.values()) for k in ("passed", "failed", "skipped", "error")}
    sev_counts = {s: sum(1 for f in r.findings if f.severity == s) for s in SEVERITIES}
    sev_txt = ", ".join(f"{s} {n}" for s, n in sev_counts.items() if n)
    if r.findings:
        verdict = f"**Result: FAIL** — {len(r.findings)} finding(s): {sev_txt}."
    elif tot["error"]:
        verdict = "**Result: INCOMPLETE** — no findings, but some tests errored before running (see Run problems)."
    elif not (tot["passed"] or any((k["overall"]["requests"] or 0) > 0 for k in r.k6)):
        verdict = ("**Result: INCONCLUSIVE** — nothing was executed: no passing test and no k6 requests "
                   f"({tot['skipped']} skipped). This is not a pass.")
    else:
        verdict = "**Result: PASS** — no failing tests or crossed thresholds in the supplied results (see limitations for what was not run)."
    if r.cases:
        verdict += f" {tot['passed']} passed, {tot['failed']} failed, {tot['skipped']} skipped, {tot['error']} errored of {len(r.cases)} tests."
        if tot["skipped"] * 2 > len(r.cases):
            verdict += (f" **Warning: {tot['skipped']} of {len(r.cases)} tests were skipped (over half).** Read this result as covering only "
                        "the tests that ran; the skip reasons (missing credentials, ids, gate variables) say what to configure.")
    parts = []
    if by:
        rows = [[esc(t), str(v["passed"]), str(v["failed"]), str(v["skipped"]), str(v["error"]), str(sum(v.values()))] for t, v in sorted(by.items())]
        rows.append(["**Total**", *(f"**{tot[k]}**" for k in ("passed", "failed", "skipped", "error")), f"**{len(r.cases)}**"])
        parts.append(table(["Test type", "Passed", "Failed", "Skipped", "Errors", "Total"], rows))
        secs = sum(c["time"] or 0 for c in r.cases)
        parts.append(f"Test time (sum of per-test times): {secs:.1f} s.")
    for k in r.k6:
        o = k["overall"]
        parts.append(f"k6 {esc(k['profile'])}: {fmt(o['requests'])} requests at {fmt(o['rps'])} req/s, p95 {fmt(o['p95'], ' ms')}, "
                     f"failed-request rate {fmt(o['failed_rate'])}.")
    return verdict, "\n\n".join(parts) or "No results supplied."


def render_coverage(r: Report) -> str:
    rows: dict[str, dict] = {}
    order = [ep_key(e) for e in r.index.eps]
    for key in order:
        rows[key] = {"types": set(), "passed": 0, "failed": 0, "skipped": 0, "error": 0, "k6": ""}
    for c in r.cases:
        row = rows.setdefault(c["endpoint"], {"types": set(), "passed": 0, "failed": 0, "skipped": 0, "error": 0, "k6": ""})
        row["types"].add(c["type"])
        row[c["status"]] += 1
    for k in r.k6:
        for tag, v in k["endpoints"].items():
            ep = r.index.by_k6_tag(tag)
            key = ep_key(ep) if ep else f"(k6 tag) {plain(tag, 60)}"
            row = rows.setdefault(key, {"types": set(), "passed": 0, "failed": 0, "skipped": 0, "error": 0, "k6": ""})
            row["types"].add(k["profile"])
            row["k6"] += f"{plain(k['profile'], 20)} p95 {fmt(v.get('p95'), ' ms')}; "
    if not rows:
        return "No per-endpoint data."
    keys = [k for k in order if k in rows] + sorted(k for k in rows if k not in order)
    body = []
    for key in keys:
        v = rows[key]
        types = ", ".join(sorted(v["types"])) or "none run"
        body.append([esc(plain(key, 200)), esc(types), str(v["passed"]), str(v["failed"]), str(v["skipped"] + v["error"]), esc(v["k6"].rstrip("; "))])
    note = "" if r.index.eps else ("\n\nNo `--intake` file was given, so only endpoints that appear in a failing request are named; "
                                   "pass the intake JSON for full per-endpoint coverage.")
    return table(["Endpoint", "Types run", "Pass", "Fail", "Skipped/error", "k6"], body) + note


def render_k6(r: Report) -> str:
    if not r.k6:
        return "No k6 summary supplied."
    out = []
    for k in r.k6:
        o = k["overall"]
        out.append(f"### Profile {esc(k['profile'])}")
        out.append(table(["Metric", "Value"], [
            ["Requests", fmt(o["requests"])], ["Request rate", fmt(o["rps"], " req/s")],
            ["Failed-request rate", fmt(o["failed_rate"])], ["Checks pass rate", fmt(o["checks_rate"])],
            ["Latency avg / p95 / p99 / max", " / ".join(fmt(o[x], " ms") for x in ("avg", "p95", "p99", "max"))]]))
        if k["thresholds"]:
            out.append(table(["Metric", "Threshold", "Observed", "Result"], [
                [esc(plain(t["metric"], 90)), esc(plain(t["expr"], 40)), fmt(t["observed"]), "**CROSSED**" if t["crossed"] else "ok"]
                for t in k["thresholds"][:100]]))
        if k["stages"]:
            out.append(table(["Stage", "p95 latency", "Failed-request rate"], [
                [esc(plain(s, 40)), fmt(v.get("p95"), " ms"), fmt(v.get("failed"))] for s, v in sorted(k["stages"].items())]))
        out.append("A quick or short run shows shape, not capacity. Stage rows come from the stage tags in the generated script.")
    return "\n\n".join(out)


def render_findings(r: Report, limit: int) -> str:
    if not r.findings:
        return "None."
    ordered = sorted(r.findings, key=lambda f: (SEVERITIES.index(f.severity), f.endpoint, f.kind, f.title))
    out = []
    for f in ordered[:limit]:
        meta = [f"Type: {esc(f.kind)}", f"Rating: {esc(f.rating)}"] + ([f"OWASP: {esc(f.owasp)}"] if f.owasp else [])
        out.append(f"### [{f.severity}] {md(f.title, 110)}\n"
                   f"- Endpoint: {esc(plain(f.endpoint, 200))}\n- {' · '.join(meta)}\n\n"
                   f"**What happened vs expected**\n\n{fence(chr(10).join(f.lines) or '(no detail in the result file)')}\n\n"
                   f"**Reproduce**\n\n{fence(chr(10).join(f.commands))}"
                   + (f"\n\n{f.note}" if f.note else ""))   # constant text from this file, not input
    if len(ordered) > limit:
        out.append(f"{len(ordered) - limit} more finding(s) not shown (least severe last); raise `--max-findings` to list them.")
    return "\n\n".join(out)


def render_problems(r: Report) -> str:
    if not r.problems:
        return "None."
    return "\n".join(f"- {esc(k)}" + (f" (x{v})" if v > 1 else "") for k, v in sorted(r.problems.items())[:50])


def render_limitations(r: Report) -> str:
    out = []
    if r.skips:
        out.append("Skipped tests, by reason:")
        out += [f"- {n} x {esc(reason)}" for reason, n in sorted(r.skips.items(), key=lambda kv: -kv[1])[:15]]
    if r.index.eps:
        seen = {c["endpoint"] for c in r.cases}
        none = [ep_key(e) for e in r.index.eps if ep_key(e) not in seen]
        if none:
            out.append(f"Endpoints in the intake with no test result: {len(none)}" + ("" if len(none) > 10 else " (" + ", ".join(esc(plain(n, 80)) for n in none) + ")") + ".")
    if not r.k6:
        out.append("No load, stress, spike or soak results were supplied.")
    out.append("Fuzzing, chaos plans and GraphQL abuse checks appear only if their results were passed in; a pass here means the "
               "checks that ran found nothing, not that the API is secure.")
    return "\n".join(out)


def render_provenance(r: Report, argv_cmd: str) -> str:
    rows = [f"- {kind}: {esc(plain(name, 80))} (sha256 {digest}…)" for kind, name, digest in r.inputs]
    return "\n".join(rows) + f"\n\nRegenerate with:\n\n{fence(argv_cmd)}\n\nSecrets are never written: values are scrubbed from every message, URL and command."


def fill_template(template: str, values: dict[str, str]) -> str:
    """Single pass: values are inserted as-is and never re-scanned for placeholders."""
    names = set(re.findall(r"\{\{(\w+)\}\}", template))
    unknown = names - set(values)
    if unknown:
        raise ReportError(f"template uses unknown placeholders: {', '.join(sorted(unknown))}")
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], template)


def build_command(args) -> str:
    parts = ["python", "scripts/summarize_results.py"]
    for flag, vals in (("--junit", args.junit), ("--k6", args.k6)):
        for v in vals:
            parts += [flag, v]
    for flag, v in (("--intake", args.intake), ("--title", args.title), ("--base-url", args.base_url)):
        if v:
            parts += [flag, v]
    parts += ["--tests-dir", args.tests_dir, "--k6-dir", args.k6_dir]
    return plain(" ".join(shlex.quote(p) for p in parts), 4000)   # scrubbed: --title or a path could carry a secret


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="summarize_results.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--junit", action="append", default=[], help="pytest JUnit XML (repeatable)")
    ap.add_argument("--k6", action="append", default=[], help="k6 --summary-export JSON, e.g. results/load-summary.json (repeatable)")
    ap.add_argument("--intake", help="intake.json from intake.py: names endpoints and shows endpoints with no results")
    ap.add_argument("--title", help="API name for the heading (default: info.title of the intake, else 'API')")
    ap.add_argument("--base-url", help="target used in the rerun commands (default: $APITEST_BASE_URL at rerun time)")
    ap.add_argument("--tests-dir", default="api_tests", help="folder holding the generated pytest files (default: api_tests)")
    ap.add_argument("--k6-dir", default="k6_tests", help="folder holding the generated k6 scripts (default: k6_tests)")
    ap.add_argument("-o", "--output", default="reports/report.md", help="report path, or - for stdout (default: reports/report.md)")
    ap.add_argument("--max-findings", type=int, default=100, help="findings listed in full (default 100)")
    ap.add_argument("--date", help="date in the heading, YYYY-MM-DD (default: today, UTC)")
    args = ap.parse_args(argv)
    try:
        if not args.junit and not args.k6:
            raise ReportError("nothing to summarize: pass --junit and/or --k6")
        for label, v in (("--tests-dir", args.tests_dir), ("--k6-dir", args.k6_dir)):
            if not SAFE_DIR_RE.match(v):
                raise ReportError(f"{label} must be a plain relative folder name (letters, digits, _ . / -)")
        if args.base_url:
            args.base_url = clean_base_url(args.base_url)
        if args.date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
            raise ReportError("--date must be YYYY-MM-DD")
        if not 1 <= args.max_findings <= 1000:
            raise ReportError("--max-findings must be between 1 and 1000")
        init_secrets()
        args.expected_host = args.base_url or os.environ.get("APITEST_BASE_URL") or None
        doc = load_json(args.intake, 20 * 1024 * 1024)[0] if args.intake else None
        index = EndpointIndex(doc)
        r = Report(args, index)
        for p in args.junit:
            r.add_junit(p)
        r.finalize()
        for p in args.k6:
            r.add_k6(p)
        title = args.title or (plain((doc.get("info") or {}).get("title"), 80) if isinstance(doc, dict) and isinstance(doc.get("info"), dict) else "") or "API"
        verdict, summary = render_summary(r)
        text = fill_template(TEMPLATE.read_text(encoding="utf-8"), {
            "title": esc(plain(title, 80)), "date": args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "verdict": verdict, "summary": summary, "coverage": render_coverage(r), "k6": render_k6(r),
            "findings": render_findings(r, args.max_findings), "problems": render_problems(r),
            "limitations": render_limitations(r), "provenance": render_provenance(r, build_command(args))})
    except ReportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.output == "-":
        sys.stdout.write(text)
    else:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Wrote {out} ({len(r.findings)} findings, {len(r.cases)} tests, {len(r.k6)} k6 summaries)", file=sys.stderr)
        print(f"Rerun with:\n  {build_command(args)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
