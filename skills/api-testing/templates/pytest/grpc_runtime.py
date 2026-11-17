"""gRPC runtime for generated suites (shipped next to apitest_runtime.py). Generated file: regenerate, do not edit.

Unary calls only. Every call goes through the `grpcurl` command line tool (set APITEST_GRPCURL to its path if it is not on PATH), so this
file needs nothing beyond the standard library and `requests`, which the rest of the suite already uses. A call becomes a `GrpcResult`
(status code name, message, JSON body, timing); the same exchange is also recorded as an HTTP-shaped response so the reports can show it.

Target: APITEST_BASE_URL as http://host:port for plaintext gRPC or https://host:port for TLS (APITEST_GRPC_CACERT, or
APITEST_GRPC_INSECURE=yes for a self-signed local certificate). A bearer token (APITEST_BEARERAUTH_TOKEN) is sent as an `authorization`
header; grpcurl takes headers on its command line, so run the suite on a machine you trust (other local users can see process arguments).
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
import requests

import apitest_runtime as rt
from apitest_runtime import (  # noqa: F401  (re-exported: generated tests call them as gr.<name>)
    DB_ERROR_RE, UNSET, Api, burst, env_true, expect_fast, require_destructive, require_flag, skip_if_paced, skip_writes_on_production,
)

GRPC_SEC = [["bearerAuth"]]
LEAK_RE = re.compile(r"(stacktrace|traceback \(most recent|\bat \S+ \(\S+:\d+:\d+\)|/(app|usr|home|var|srv)/\S+\.(js|py|rb|java|ts|go|rs)|node_modules|goroutine \d+)", re.I)
CODE_NUMBERS = {
    "OK": 0, "CANCELLED": 1, "UNKNOWN": 2, "INVALID_ARGUMENT": 3, "DEADLINE_EXCEEDED": 4, "NOT_FOUND": 5, "ALREADY_EXISTS": 6,
    "PERMISSION_DENIED": 7, "RESOURCE_EXHAUSTED": 8, "FAILED_PRECONDITION": 9, "ABORTED": 10, "OUT_OF_RANGE": 11, "UNIMPLEMENTED": 12,
    "INTERNAL": 13, "UNAVAILABLE": 14, "DATA_LOSS": 15, "UNAUTHENTICATED": 16,
}
# the usual gRPC-to-HTTP mapping, used only to label the recorded exchange
HTTP_OF = {"OK": 200, "CANCELLED": 499, "UNKNOWN": 500, "INVALID_ARGUMENT": 400, "DEADLINE_EXCEEDED": 504, "NOT_FOUND": 404,
           "ALREADY_EXISTS": 409, "PERMISSION_DENIED": 403, "RESOURCE_EXHAUSTED": 429, "FAILED_PRECONDITION": 400, "ABORTED": 409,
           "OUT_OF_RANGE": 400, "UNIMPLEMENTED": 501, "INTERNAL": 500, "UNAVAILABLE": 503, "DATA_LOSS": 500, "UNAUTHENTICATED": 401}
SERVER_FAULTS = {"UNKNOWN", "INTERNAL", "DATA_LOSS", "UNAVAILABLE"}      # a handled bad input never ends here
CLIENT_ERRORS = {"INVALID_ARGUMENT", "OUT_OF_RANGE", "FAILED_PRECONDITION", "NOT_FOUND", "ALREADY_EXISTS", "PERMISSION_DENIED",
                 "UNAUTHENTICATED", "ABORTED", "RESOURCE_EXHAUSTED"}
MAX_OUTPUT = 2_000_000
INT_TYPES = {"int32", "sint32", "sfixed32", "uint32", "fixed32"}
LONG_TYPES = {"int64", "sint64", "sfixed64", "uint64", "fixed64"}


def _env_key(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").upper()


def canonical_code(name: str) -> str:
    """grpcurl prints codes in CamelCase (`InvalidArgument`); the suite uses the SCREAMING form (`INVALID_ARGUMENT`)."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name.strip()).upper()
    return text if text in CODE_NUMBERS else "UNKNOWN"


@dataclass
class EnvId:
    """A field that must hold the id of an existing object: it comes from the environment."""
    field: str


@dataclass
class Method:
    id: str                 # package.Service/Method
    name: str
    service: str
    request: str            # request message full name
    response: str
    template: dict = field(default_factory=dict)    # baseline request message (JSON form); EnvId marks ids taken from the environment
    destructive: bool = False
    writes: bool = False

    @property
    def full(self) -> str:
        return f"{self.service}/{self.name}"


@dataclass
class GrpcResult:
    method: str
    code: str
    message: str
    body: Any
    elapsed: float
    sent: str
    argv: list
    unreachable: bool = False
    timed_out: bool = False
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.code == "OK"

    def repro(self) -> str:
        """The grpcurl command that repeats this call, with header values replaced."""
        parts, hide = [], False
        for a in self.argv:
            if hide:
                parts.append(shlex.quote(a.split(":", 1)[0] + ": <redacted>"))
                hide = False
                continue
            parts.append(shlex.quote(a))
            hide = a == "-H"
        try:
            shown = json.dumps(rt.redact(json.loads(self.sent)))[:2000]       # secret-looking fields are replaced
        except (ValueError, RecursionError):
            shown = "<message not shown>"
        return f"echo {shlex.quote(shown)} | " + " ".join(parts)

    def as_response(self, target: str) -> requests.Response:
        """HTTP-shaped copy of this exchange, for the report. The status is the usual gRPC-to-HTTP mapping."""
        r = requests.Response()
        r.status_code = HTTP_OF.get(self.code, 500)
        payload = self.body if self.ok else {"code": self.code, "message": self.message}
        r._content = json.dumps(payload).encode()
        r.headers["Content-Type"] = "application/json"
        r.headers["grpc-status"] = str(CODE_NUMBERS.get(self.code, 2))
        r.elapsed = datetime.timedelta(seconds=self.elapsed)
        r.request = requests.Request("POST", f"{target}/{self.method}", data=self.sent.encode(),
                                     headers={"Content-Type": "application/grpc+json"}).prepare()
        return r


def gfail(res: GrpcResult | None, msg: str, severity: str | None = None, owasp: str | None = None) -> None:
    tag = "".join(f"[{t}]" for t in (severity, owasp) if t)
    suffix = ""
    if res is not None:
        body = json.dumps(rt.redact(res.body))[:300] if res.body is not None else ""
        suffix = (f"\n  {res.method} -> {res.code} ({int(res.elapsed * 1000)} ms)\n  message: {res.message[:200]}"
                  + (f"\n  body: {body}" if body else "") + f"\n  repro: {res.repro()}")
    pytest.fail(f"{tag} {msg}{suffix}".strip(), pytrace=False)


class Grpc(Api):
    """The target of one suite: calls unary methods through grpcurl."""

    def __init__(self, base_url: str, schemes: dict, proto_file: str, proto_dir: str):
        super().__init__(base_url, schemes)
        self.tls = base_url.lower().startswith("https://")
        self.target = re.sub(r"^https?://", "", self.base_url).split("/", 1)[0]
        self.proto_file, self.proto_dir = proto_file, proto_dir
        self.binary = os.environ.get("APITEST_GRPCURL") or shutil.which("grpcurl")

    # ---- building calls
    def _auth(self, mode: str, who: str) -> dict:
        if mode == "none":
            return {}
        if mode == "invalid":
            return {k.lower(): v for k, v in self.credentials(GRPC_SEC, who, invalid=True)[0].items()}
        try:
            return {k.lower(): v for k, v in self.credentials(GRPC_SEC, who)[0].items()}
        except pytest.skip.Exception:
            if mode == "required":
                raise
            return {}  # no token configured: the call goes out without one

    def _need_binary(self) -> str:
        if not self.binary or not (os.path.isfile(self.binary) and os.access(self.binary, os.X_OK)):
            pytest.skip("grpcurl is not installed (https://github.com/fullstorydev/grpcurl); set APITEST_GRPCURL to its path to run the gRPC tests")
        return self.binary

    def _base_args(self) -> list[str]:
        args = [self._need_binary()]
        if not self.tls:
            args.append("-plaintext")
        elif env_true("APITEST_GRPC_INSECURE"):
            args.append("-insecure")
        elif os.environ.get("APITEST_GRPC_CACERT"):
            args += ["-cacert", os.environ["APITEST_GRPC_CACERT"]]
        return args

    def resolve(self, method: Method, override: dict | None = None, message: Any = UNSET, victim: bool = False) -> Any:
        """The method's baseline message with ids filled in from the environment (skips when one is missing)."""
        base = method.template if message is UNSET else message
        out = _fill(base, method, victim)
        if override and isinstance(out, dict):
            out = {**out, **override}
        return out

    def call(self, method: Method, message: Any = UNSET, *, override: dict | None = None, auth: str = "default", who: str = "A",
             headers: dict | None = None, timeout: float | None = None, victim: bool = False, raw: str | None = None,
             method_path: str | None = None) -> GrpcResult:
        if method.writes:
            skip_writes_on_production()
        payload = raw if raw is not None else json.dumps(self.resolve(method, override, message, victim))
        if len(payload) > MAX_OUTPUT:
            raise ValueError("request message is too large")
        h: dict[str, str] = {}
        if auth != "none":
            h.update(self._auth(auth, who))
        h.update({k.lower(): v for k, v in (headers or {}).items()})
        args = self._base_args()
        limit = timeout or self.timeout
        args += ["-max-time", str(max(1, int(limit))), "-format", "json", "-import-path", self.proto_dir, "-proto", self.proto_file, "-d", "@"]
        for k, v in h.items():
            args += ["-H", f"{k}: {v}"]
        args += [self.target, method_path or method.full]
        return self._run(args, payload, method_path or method.full, limit)

    def list_services(self) -> GrpcResult:
        """Server reflection: `grpcurl list` without any proto file succeeds only when reflection is enabled."""
        args = self._base_args() + ["-max-time", str(max(1, int(self.timeout))), self.target, "list"]
        return self._run(args, "", "list", self.timeout)

    def _run(self, args: list[str], payload: str, name: str, limit: float) -> GrpcResult:
        self._pace()
        started = time.monotonic()
        try:
            proc = subprocess.run(args, input=payload, capture_output=True, text=True, timeout=limit + 5, check=False)
            code_out, err_out, out = proc.returncode, proc.stderr, proc.stdout
            timed_out = False
        except subprocess.TimeoutExpired:
            code_out, err_out, out, timed_out = -1, "deadline exceeded: grpcurl did not return", "", True
        elapsed = time.monotonic() - started
        res = _parse(name, code_out, out[:MAX_OUTPUT], err_out[:20000], elapsed, payload, args, timed_out)
        if res.unreachable:
            msg = f"{rt.UNREACHABLE_PREFIX}: could not connect to {self.target} for {name}"
            if rt.unreachable_mode() == "fail":
                gfail(None, msg + "; failing because this is a reachability check (APITEST_UNREACHABLE=skip makes it a skip)")
            pytest.skip(msg + "; no verdict on the API (APITEST_UNREACHABLE=fail makes it a failure)")
        rt.record_exchange(res.as_response(self.base_url))
        return res


def _fill(value: Any, method: Method, victim: bool) -> Any:
    if isinstance(value, EnvId):
        keys = [f"APITEST_GRPC_{'VICTIM_' if victim else ''}{_env_key(method.name)}_{_env_key(value.field)}",
                f"APITEST_GRPC_{'VICTIM_' if victim else ''}{_env_key(value.field)}"]
        for k in keys:
            if os.environ.get(k):
                return os.environ[k]
        pytest.skip(f"no existing id for '{value.field}': set {keys[0]} or {keys[1]}")
    if isinstance(value, dict):
        return {k: _fill(v, method, victim) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, method, victim) for v in value]
    return value


_ERR_CODE_RE = re.compile(r"^\s*Code:\s*(\w+)", re.M)
_ERR_MSG_RE = re.compile(r"^\s*Message:\s*(.*)$", re.M)
_DIAL_RE = re.compile(r"Failed to dial target host|connection refused|no such host|context deadline exceeded.*dial|name resolution|tls: ", re.I)


def _parse(name: str, returncode: int, out: str, err: str, elapsed: float, sent: str, argv: list, timed_out: bool) -> GrpcResult:
    if timed_out:
        return GrpcResult(name, "DEADLINE_EXCEEDED", "no answer within the time limit", None, elapsed, sent, argv, timed_out=True, stderr=err)
    if returncode == 0:
        body: Any = None
        text = out.strip()
        if text:
            try:
                body = json.loads(text)
            except ValueError:
                body = text
        else:
            body = {}
        return GrpcResult(name, "OK", "", body, elapsed, sent, argv, stderr=err)
    code = _ERR_CODE_RE.search(err)
    if code:
        msg = _ERR_MSG_RE.search(err)
        return GrpcResult(name, canonical_code(code.group(1)), (msg.group(1) if msg else "").strip(), None, elapsed, sent, argv, stderr=err)
    if _DIAL_RE.search(err) and "Failed to dial" in err:
        return GrpcResult(name, "UNAVAILABLE", err.strip()[:300], None, elapsed, sent, argv, unreachable=True, stderr=err)
    # grpcurl could not even build the request (bad JSON for the message type, missing method): not a server answer
    return GrpcResult(name, "UNKNOWN", err.strip()[:300], None, elapsed, sent, argv, stderr=err)


# --------------------------------------------------------------------------- expectations
def expect_ok(res: GrpcResult, what: str = "") -> Any:
    if not res.ok:
        gfail(res, f"{what or 'valid call'} failed: expected OK, got {res.code}")
    return res.body


def expect_code(res: GrpcResult, codes: Any, what: str = "") -> None:
    allowed = {codes} if isinstance(codes, str) else set(codes)
    if res.code not in allowed:
        gfail(res, f"{what or 'call'}: expected {'/'.join(sorted(allowed))}, got {res.code}", "MEDIUM" if res.code in SERVER_FAULTS else None)


def expect_graceful(res: GrpcResult, what: str = "") -> None:
    """Unusual input: any handled outcome (OK or a client error) is fine; a server fault is not."""
    if res.timed_out:
        gfail(res, f"{what or 'unusual input'}: no answer within the time limit", "MEDIUM")
    if res.code in SERVER_FAULTS:
        gfail(res, f"{what or 'unusual input'}: server fault {res.code}", "MEDIUM")
    if res.code == "UNIMPLEMENTED":
        gfail(res, f"{what or 'unusual input'}: the server says the method is not implemented", "MEDIUM")


def expect_rejected(res: GrpcResult, what: str = "") -> None:
    """Invalid input must end in a client-error status; success is a finding and a server fault a worse one."""
    if res.ok:
        gfail(res, f"{what or 'invalid input'} was accepted (OK)")
    if res.code in SERVER_FAULTS or res.timed_out:
        gfail(res, f"{what or 'invalid input'}: server fault {res.code} instead of a client-error status", "MEDIUM")


def expect_no_leak(res: GrpcResult) -> None:
    text = res.message + " " + (json.dumps(res.body) if res.body is not None else "")
    if LEAK_RE.search(text) or DB_ERROR_RE.search(text):
        gfail(res, "the status message or body exposes stack traces, file paths or database errors", "MEDIUM", "API8:2023 security misconfiguration")


def expect_injection_safe(res: GrpcResult, what: str = "") -> None:
    if res.code in SERVER_FAULTS or res.timed_out:
        gfail(res, f"{what or 'injection string'}: server fault {res.code}", "HIGH", "API8:2023 injection")
    expect_no_leak(res)


# --------------------------------------------------------------------------- contract (response shape vs the proto)
def _problem(kind: str, ftype: str, value: Any, enums: dict, messages: dict, depth: int = 0) -> str | None:
    if value is None:
        return None
    if kind == "scalar":
        if ftype == "string":
            return None if isinstance(value, str) else "should be a string"
        if ftype == "bool":
            return None if isinstance(value, bool) else "should be a boolean"
        if ftype == "bytes":
            try:
                base64.b64decode(value, validate=False)
                return None if isinstance(value, str) else "should be a base64 string"
            except Exception:
                return "should be a base64 string"
        if ftype in ("float", "double"):
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                return "should be a number"
            if isinstance(value, str) and value not in ("NaN", "Infinity", "-Infinity"):
                return "should be a number"
            return None
        if ftype in INT_TYPES:
            ok = isinstance(value, int) and not isinstance(value, bool) or (isinstance(value, str) and re.fullmatch(r"-?\d+", value))
            return None if ok else "should be an integer"
        if ftype in LONG_TYPES:    # 64-bit integers travel as JSON strings
            ok = isinstance(value, str) and re.fullmatch(r"-?\d+", value) or isinstance(value, int) and not isinstance(value, bool)
            return None if ok else "should be an integer (JSON string)"
        return None
    if kind == "enum":
        names = enums.get(ftype) or []
        if isinstance(value, int) and not isinstance(value, bool):
            return None
        return None if value in names or not names else f"{value!r} is not a value of enum {ftype}"
    if kind == "timestamp":
        return None if isinstance(value, str) and re.match(r"\d{4}-\d\d-\d\dT", value) else "should be an RFC 3339 timestamp string"
    if kind == "duration":
        return None if isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?s", value) else "should be a duration string like '1.5s'"
    if kind == "message":
        if not isinstance(value, dict):
            return "should be an object"
        return shape_problem(value, ftype, enums, messages, depth + 1)
    return None


def shape_problem(obj: dict, message: str, enums: dict, messages: dict, depth: int = 0) -> str | None:
    """First mismatch between a JSON message and the proto definition of `message`, as a path and a reason; None when it fits."""
    if depth > 8:
        return None
    fields = {f["json_name"]: f for f in (messages.get(message) or {}).get("fields", [])}
    fields.update({f["name"]: f for f in (messages.get(message) or {}).get("fields", [])})
    for key, value in obj.items():
        f = fields.get(key)
        if f is None:
            return f"{message}.{key}: not a field of the message"
        if f["kind"] == "map":
            if not isinstance(value, dict):
                return f"{message}.{key}: should be an object (map)"
            continue
        if f["repeated"]:
            if not isinstance(value, list):
                return f"{message}.{key}: should be an array"
            for i, item in enumerate(value[:20]):
                why = _problem(f["kind"], f["type"], item, enums, messages, depth)
                if why:
                    return f"{message}.{key}[{i}]: {why}"
            continue
        why = _problem(f["kind"], f["type"], value, enums, messages, depth)
        if why:
            return f"{message}.{key}: {why}"
    return None


def expect_shape(res: GrpcResult, response: str, enums: dict, messages: dict) -> None:
    body = expect_ok(res)
    if not isinstance(body, dict):
        gfail(res, "the response is not a JSON object", "MEDIUM")
    why = shape_problem(body, response, enums, messages)
    if why:
        gfail(res, f"response does not match the proto: {why}", "MEDIUM")
