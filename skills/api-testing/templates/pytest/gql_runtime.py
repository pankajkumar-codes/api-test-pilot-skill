"""GraphQL helpers shipped next to generated GraphQL suites. Extends apitest_runtime (auth, redaction, safety gate).

GraphQL reports most failures as HTTP 200 with an `errors` array, so these helpers assert on the body, not
only on the status code. Generated file, regenerate instead of editing.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
import requests

from apitest_runtime import (  # noqa: F401  (re-exported: generated tests call them as gr.<name>)
    DB_ERROR_RE, UNSET, Api, burst, burst_size, env_true, expect_fast, expect_injection_safe, expect_no_crash, fail, json_of,
    require_destructive, require_flag, retry_after_seconds, skip_if_paced, skip_writes_on_production,
)

LEAK_RE = re.compile(r"(stacktrace|\bat \S+ \(\S+:\d+:\d+\)|/(app|usr|home|var|srv)/\S+\.(js|py|rb|java|ts)|node_modules)", re.I)


def _env_key(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").upper()


@dataclass
class EnvArg:
    """Argument value that must come from the environment (ids of existing objects)."""
    arg: str
    default: Any = None


@dataclass
class Operation:
    id: str
    kind: str
    name: str
    query: str
    variables: dict = field(default_factory=dict)
    args: list = field(default_factory=list)
    returns: str = ""
    return_kind: str = "SCALAR"
    return_non_null: bool = False
    return_list: bool = False
    query_minimal: str | None = None
    return_type: str = ""
    selection: list = field(default_factory=list)


class Gql(Api):
    def __init__(self, base_url: str, schemes: dict, path: str = "/graphql"):
        super().__init__(base_url, schemes)
        self.url = os.environ.get("APITEST_GQL_URL") or (self.base_url + path)
        self.sec = [[next(iter(schemes))]] if schemes else [["bearerAuth"]]   # the one scheme the intake declared (bearer, or an API key header)

    def _auth(self, mode: str, who: str) -> dict:
        if mode == "none":
            return {}
        if mode == "invalid":
            return self.credentials(self.sec, who, invalid=True)[0]
        try:
            return self.credentials(self.sec, who)[0]
        except pytest.skip.Exception:
            if mode == "required":
                raise
            return {}  # schema declares no auth: send a token only when one is configured

    def post(self, query: str, variables: dict | None = None, operation_name: str | None = None, *,
             auth: str = "default", who: str = "A", headers: dict | None = None, raw: Any = None,
             transient: str = "skip", timeout: float | None = None) -> requests.Response:
        if re.match(r"\s*mutation\b", query):
            skip_writes_on_production()
        payload: dict[str, Any] = {"query": query}
        if variables is not None:
            payload["variables"] = variables
        if operation_name:
            payload["operationName"] = operation_name
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        h.update(self._auth(auth, who))
        h.update(headers or {})
        data = raw if raw is not None else json.dumps(payload)
        return self._request("POST", self.url, transient=transient, data=data, headers=h,
                             timeout=self.timeout if timeout is None else timeout, allow_redirects=False)

    def arg_value(self, op: Operation, arg: str, default: Any, victim: bool) -> Any:
        keys = [f"APITEST_GQL_{'VICTIM_' if victim else ''}{_env_key(op.name)}_{_env_key(arg)}",
                f"APITEST_GQL_{'VICTIM_' if victim else ''}{_env_key(arg)}"]
        for k in keys:
            if os.environ.get(k):
                return os.environ[k]
        if default is not None and not victim:
            return default
        pytest.skip(f"no existing id for '{arg}': set {keys[0]} or {keys[1]}")

    def resolve(self, op: Operation, variables: Any = UNSET, victim: bool = False) -> dict:
        """The operation's baseline variables with ids filled in from the environment (skips when one is missing)."""
        base = op.variables if variables is UNSET else variables
        return {k: (self.arg_value(op, v.arg, v.default, victim) if isinstance(v, EnvArg) else v)
                for k, v in (base or {}).items()}

    def run(self, op: Operation, *, variables: Any = UNSET, override: dict | None = None, query: str | None = None,
            auth: str = "default", who: str = "A", victim: bool = False) -> requests.Response:
        resolved = self.resolve(op, variables, victim)
        resolved.update(override or {})
        return self.post(query or op.query, resolved, auth=auth, who=who)


# --------------------------------------------------------------------------- expectations
def body_of(resp: requests.Response) -> Any:
    return json_of(resp)


def errors_of(resp: requests.Response) -> list:
    b = body_of(resp)
    errs = b.get("errors") if isinstance(b, dict) else None
    return errs if isinstance(errs, list) else []


def expect_data(resp: requests.Response, field_name: str, nonnull: bool = False) -> Any:
    if not 200 <= resp.status_code < 300:
        fail(resp, f"expected HTTP 2xx, got {resp.status_code}")
    b = body_of(resp)
    if not isinstance(b, dict):
        fail(resp, "response is not a JSON object", "MEDIUM")
    if errors_of(resp):
        msgs = [e.get("message") if isinstance(e, dict) else e for e in errors_of(resp)][:3]
        fail(resp, f"valid operation returned errors: {msgs}")
    data = b.get("data")
    if not isinstance(data, dict) or field_name not in data:
        fail(resp, f"`data.{field_name}` is missing from the response")
    if nonnull and data[field_name] is None:
        fail(resp, f"`data.{field_name}` is null although the schema declares it non-null", "MEDIUM")
    return data[field_name]


def expect_graphql_error(resp: requests.Response, what: str = "invalid operation") -> None:
    """Invalid operations must produce a 4xx or a well-formed `errors` array; never a 5xx, never silent success."""
    s = resp.status_code
    if s >= 500:
        fail(resp, f"{what}: server error {s}", "MEDIUM")
    errs = errors_of(resp)
    bad = [e for e in errs if not (isinstance(e, dict) and isinstance(e.get("message"), str))]
    if bad:
        fail(resp, f"{what}: `errors` entries must be objects with a string `message`", "LOW")
    if 400 <= s < 500 or errs:
        return
    fail(resp, f"{what}: accepted without any error (HTTP {s}, no `errors` array)")


def expect_denied_gql(resp: requests.Response, field_name: str, owasp: str, what: str, severity: str = "HIGH") -> None:
    """Data that must not be returned: a 4xx, an `errors` array or a null field all pass."""
    if resp.status_code >= 500:
        fail(resp, f"{what}: server error", "MEDIUM", owasp)
    b = body_of(resp)
    data = b.get("data") if isinstance(b, dict) else None
    if isinstance(data, dict) and data.get(field_name) not in (None, [], {}):
        fail(resp, f"{what}: data was returned", severity, owasp)


def dig(data: Any, path: list[str]) -> list:
    """All leaf values at `path` (fans out through lists)."""
    cur = [data]
    for key in path:
        nxt = []
        for c in cur:
            for item in (c if isinstance(c, list) else [c]):
                if isinstance(item, dict) and key in item:
                    nxt.append(item[key])
        cur = nxt
    return [v for v in cur if v is not None]


def expect_field_protected(resp: requests.Response, path: list[str], label: str) -> None:
    b = body_of(resp)
    data = b.get("data") if isinstance(b, dict) else None
    leaked = dig(data, path) if data else []
    if resp.status_code < 500 and not leaked:
        return
    if resp.status_code >= 500:
        fail(resp, f"{label}: server error", "MEDIUM")
    fail(resp, f"{label} is returned to a caller without credentials (field-level authorization missing)",
         "HIGH", "API3:2023 BOPLA")


def expect_no_gql_error_leak(resp: requests.Response) -> None:
    text = json.dumps(errors_of(resp)) if errors_of(resp) else resp.text
    if LEAK_RE.search(text) or DB_ERROR_RE.search(text):
        fail(resp, "error response exposes stack traces, file paths or database errors", "MEDIUM",
             "API8:2023 security misconfiguration")


def expect_graceful(resp: requests.Response, what: str = "") -> None:
    """Unusual input: any handled outcome is fine (success or errors); a 5xx or unparseable 200 is not."""
    if resp.status_code >= 500:
        fail(resp, f"{what or 'unusual input'}: server error {resp.status_code}", "MEDIUM")
    if resp.status_code == 200 and not isinstance(body_of(resp), dict):
        fail(resp, f"{what or 'unusual input'}: HTTP 200 body is not a JSON object", "MEDIUM")


# --------------------------------------------------------------------------- resilience
def _data_shape(resp: requests.Response) -> tuple:
    b = body_of(resp)
    data = b.get("data") if isinstance(b, dict) else None
    if not isinstance(data, dict):
        return (None,)
    return tuple(sorted((k, type(v).__name__) for k, v in data.items()))


def expect_repeatable(first: requests.Response, second: requests.Response) -> None:
    """Two identical reads must agree on status, on whether they carry errors and on the fields and kinds of the data."""
    if first.status_code != second.status_code:
        fail(second, f"repeating an identical query changed the status ({first.status_code} -> {second.status_code})")
    if bool(errors_of(first)) != bool(errors_of(second)):
        fail(second, "repeating an identical query changed whether it reports errors")
    if _data_shape(first) != _data_shape(second):
        fail(second, "repeating an identical query changed the shape of the data")


def expect_page_size(resp: requests.Response, field_name: str, at_most: int) -> None:
    items = expect_data(resp, field_name)
    if isinstance(items, list) and len(items) > at_most:
        fail(resp, f"page size argument ignored: {len(items)} items came back, expected at most {at_most}")


def expect_rate_limit_behaviour(gql: "Gql", n: int) -> None:
    """If the API throttles, the 429/503 must say how long to back off (Retry-After) and the API must recover afterwards."""
    skip_if_paced(gql)
    responses = burst(lambda: gql.post("query { __typename }"), n)
    limited = [r for r in responses if r.status_code in (429, 503)]
    if not limited:
        return  # no throttling seen in this burst, so nothing to check
    wait = retry_after_seconds(limited[0])
    if wait is None:
        fail(limited[0], f"{limited[0].status_code} response has no valid Retry-After header", "LOW")
    time.sleep(min(wait, 5.0))
    again = gql.post("query { __typename }")
    if again.status_code == 429 and wait <= 5.0:
        fail(again, f"still throttled after waiting the advertised Retry-After ({wait:.0f}s)", "LOW")


# --------------------------------------------------------------------------- contract
def _parse_type(text: str) -> tuple:
    """'[[Pet!]]!' -> ('list', True, ('list', False, ('named', True, 'Pet'))). Unparseable text is treated as a plain name."""
    text = text.strip()
    non_null = text.endswith("!")
    core = text[:-1].strip() if non_null else text
    if core.startswith("[") and core.endswith("]"):
        return ("list", non_null, _parse_type(core[1:-1]))
    return ("named", non_null, core)


def _scalar_problem(named: str, value: Any, enums: dict, kinds: dict) -> str | None:
    if named == "Int":
        return None if isinstance(value, int) and not isinstance(value, bool) else "an integer"
    if named == "Float":
        return None if isinstance(value, (int, float)) and not isinstance(value, bool) else "a number"
    if named == "Boolean":
        return None if isinstance(value, bool) else "a boolean"
    if named == "String":
        return None if isinstance(value, str) else "a string"
    if named == "ID":
        return None if isinstance(value, (str, int)) and not isinstance(value, bool) else "a string or integer"
    if kinds.get(named) == "ENUM":
        return None if value in enums.get(named, []) else f"one of {enums.get(named, [])}"
    return None   # custom scalars carry anything


def _check(resp: requests.Response, value: Any, node: tuple, where: str, decl: str, on_object, enums: dict, kinds: dict,
           budget: list) -> None:
    kind, non_null, inner = node
    if value is None:
        if non_null:
            fail(resp, f"{where} is null although the schema declares {decl}", "MEDIUM")
        return
    if kind == "list":
        if not isinstance(value, list):
            fail(resp, f"{where} should be a list ({decl}), got {type(value).__name__}", "MEDIUM")
        for i, item in enumerate(value):
            if budget[0] <= 0:
                return
            budget[0] -= 1
            _check(resp, item, inner, f"{where}[{i}]", decl, on_object, enums, kinds, budget)
        return
    if kinds.get(inner) in ("OBJECT", "INTERFACE"):
        on_object(value, where)
        return
    bad = _scalar_problem(inner, value, enums, kinds)
    if bad:
        fail(resp, f"{where} should be {bad} ({decl}), got {type(value).__name__}", "MEDIUM")


def expect_shape(resp: requests.Response, value: Any, returns: str, selection: list, types: dict, enums: dict,
                 kinds: dict) -> None:
    """Check `value` (the answer for an operation whose return type is written `returns`, for example '[Pet!]!') against what
    the schema declares for the selected fields. Lists may be nested; at most 20 items are looked at per answer."""
    node = _parse_type(returns)
    named = node
    while named[0] == "list":
        named = named[2]
    type_name = named[2]
    budget = [20]

    def on_object(obj: Any, where: str) -> None:
        if not isinstance(obj, dict):
            fail(resp, f"{where} should be an object, got {type(obj).__name__}", "MEDIUM")
        fields = types.get(type_name, {})
        for name in selection:
            if name not in obj:
                fail(resp, f"{where}.{name} was selected but is missing from the answer", "MEDIUM")
            decl = fields.get(name)
            if decl is not None:
                _check(resp, obj[name], _parse_type(decl), f"{type_name}.{name}" if where == type_name else f"{where}.{name}",
                       decl, lambda *_: None, enums, kinds, [20])

    _check(resp, value, node, type_name, returns, on_object, enums, kinds, budget)
