"""The generated gRPC suite against a real gRPC server (grpcio) through the real grpcurl.

Skipped unless `grpcio`, `grpcio-tools` and `grpcurl` are installed (set APITEST_GRPCURL_REAL to a grpcurl path if it is not on PATH).
"""
from __future__ import annotations

import os
import shutil

import pytest

import gen_pytest
import mock_grpc
import parse_proto
from suite_runner import failed, passed, run_suite

from conftest import FIXTURES

GRPCURL = os.environ.get("APITEST_GRPCURL_REAL") or shutil.which("grpcurl")
pytestmark = pytest.mark.skipif(not (GRPCURL and mock_grpc.available()), reason="needs grpcio, grpcio-tools and grpcurl")
ENV = {"APITEST_GRPCURL": GRPCURL or "", "APITEST_BEARERAUTH_TOKEN": "tokA", "APITEST_GRPC_ID": "1", "APITEST_GRPC_REQUIRES_AUTH": "yes"}


@pytest.fixture(scope="module")
def suite(tmp_path_factory):
    out = tmp_path_factory.mktemp("grpcreal") / "suite"
    gen_pytest.generate(parse_proto.build(str(FIXTURES / "shop.proto")), out, ["all"])
    return out


def test_clean_server_passes(suite):
    with mock_grpc.running() as url:
        results, proc = run_suite(suite, url, ENV)
    assert failed(results) == [] and len(passed(results)) >= 60, proc.stdout[-2000:]


@pytest.mark.parametrize("bug,expected", [
    ("noauth", {"test_grpc_negative::test_requires_authentication", "test_grpc_negative::test_invalid_token_is_rejected"}),
    ("acceptsbad", {"test_grpc_negative::test_GetPet_unknown_id_is_rejected"}),
    ("reflection", {"test_grpc_security::test_reflection_disabled"}),
])
def test_seeded_flaws_are_found(suite, bug, expected):
    with mock_grpc.running({bug}) as url:
        results, proc = run_suite(suite, url, ENV)
    assert set(failed(results)) == expected, proc.stdout[-2000:]


@pytest.mark.parametrize("bug", ["crash", "leak"])
def test_server_faults_and_leaks_are_found(suite, bug):
    with mock_grpc.running({bug}) as url:
        results, proc = run_suite(suite, url, ENV)
    bad = failed(results)
    assert bad and all(n.startswith("test_grpc_security::") for n in bad), proc.stdout[-2000:]
