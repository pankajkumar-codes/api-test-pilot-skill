"""The generated suites against two real local servers in examples/aws-local: an API Gateway style REST app and a GraphQL app with an API key.

Skipped unless fastapi, uvicorn and strawberry-graphql are installed. Seeded flaws come from GATEWAY_BUGS / GRAPHQL_BUGS.
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import socket
import subprocess
import sys
import time

import pytest

import gen_pytest
import parse_graphql
import parse_openapi
from suite_runner import failed, passed, run_suite

from conftest import ROOT

APPS = ROOT / "examples" / "aws-local"
HAVE = all(importlib.util.find_spec(m) for m in ("fastapi", "uvicorn", "strawberry"))
pytestmark = pytest.mark.skipif(not HAVE, reason="pip install fastapi uvicorn strawberry-graphql to run the local gateway and GraphQL examples")
KEY = {"APITEST_APIKEY_KEY": "local-key-A"}


@contextlib.contextmanager
def serve(script: str, env: dict):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    proc = subprocess.Popen([sys.executable, str(APPS / script), "--port", str(port)], env={**os.environ, **env},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("the example server did not start")
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="module")
def rest_suite(tmp_path_factory):
    out = tmp_path_factory.mktemp("gw") / "suite"
    gen_pytest.generate(parse_openapi.build(str(APPS / "openapi.yaml")), out, ["all"])
    return out


@pytest.fixture(scope="module")
def gql_suite(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gq")
    sys.path.insert(0, str(APPS))
    import graphql_app
    sdl = tmp / "schema.graphql"
    sdl.write_text(graphql_app.schema.as_str())
    out = tmp / "suite"
    gen_pytest.generate(parse_graphql.build(str(sdl), "http://127.0.0.1/graphql", "x-api-key"), out, ["all"])
    return out


def run_rest(suite, bug=""):
    with serve("gateway_app.py", {"GATEWAY_BUGS": bug}) as url:
        return run_suite(suite, url + "/prod", {**KEY, "APITEST_PATH_PETID": "1", "APITEST_VICTIM_PETID": "2"})


def run_gql(suite, bug=""):
    with serve("graphql_app.py", {"GRAPHQL_BUGS": bug}) as url:
        return run_suite(suite, url, {**KEY, "APITEST_GQL_ID": "1"})


def test_gateway_clean_run_passes(rest_suite):
    results, proc = run_rest(rest_suite)
    assert failed(results) == [] and len(passed(results)) >= 80, proc.stdout[-2000:]


@pytest.mark.parametrize("bug,needle", [
    ("nokey", "rejects_missing_credentials"), ("crash", "injection"), ("acceptsbad", "rejects_invalid_input"), ("idor", "bola"),
])
def test_gateway_seeded_flaws_are_found(rest_suite, bug, needle):
    results, proc = run_rest(rest_suite, bug)
    assert any(needle in n for n in failed(results)), proc.stdout[-2000:]


def test_gateway_throttle_is_checked(rest_suite):
    """With the usage plan off, the rate-limit test cannot see a 429 and says so instead of failing the whole suite."""
    results, _ = run_rest(rest_suite, "throttle_off")
    assert not [n for n in failed(results) if "rate" not in n]


def test_graphql_clean_run_passes(gql_suite):
    results, proc = run_gql(gql_suite)
    assert failed(results) == [] and len(passed(results)) >= 45, proc.stdout[-2000:]


@pytest.mark.parametrize("bug,test", [
    ("introspection", "test_introspection_disabled"), ("noalias", "test_alias_abuse_is_limited"),
    ("suggest", "test_error_messages_do_not_suggest_schema_names"),
])
def test_graphql_seeded_flaws_are_found(gql_suite, bug, test):
    results, proc = run_gql(gql_suite, bug)
    assert failed(results) == [f"test_graphql_security::{test}"], proc.stdout[-2000:]
