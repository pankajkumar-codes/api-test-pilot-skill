"""gRPC: the .proto parser, the generated suite and the runtime, run against a stand-in grpcurl (tests/fake_grpcurl.py)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import gen_pytest
import parse_proto
from _common import IntakeError
from suite_runner import failed, passed, run_suite

from conftest import FIXTURES, ROOT

FAKE = str(Path(__file__).parent / "fake_grpcurl.py")
ENV = {"APITEST_GRPCURL": FAKE, "APITEST_BEARERAUTH_TOKEN": "tokA", "APITEST_GRPC_ID": "1", "APITEST_GRPC_REQUIRES_AUTH": "yes"}
URL = "http://localhost:50051"


@pytest.fixture(scope="module")
def doc():
    return parse_proto.build(str(FIXTURES / "shop.proto"))


@pytest.fixture(scope="module")
def suite(tmp_path_factory, doc):
    out = tmp_path_factory.mktemp("grpc") / "suite"
    gen_pytest.generate(doc, out, ["all"])
    return out


def test_parser_reads_services_messages_and_enums(doc):
    g = doc["grpc"]
    assert doc["source"]["type"] == "grpc" and g["package"] == "shop.v1"
    assert len(g["methods"]) == 5 and "shop.v1.Pet" in g["messages"] and "shop.v1.Species" in g["enums"]
    assert any(m["name"] == "WatchPets" and m.get("serverStreaming") for m in g["methods"]) or any("streaming" in w for w in doc["warnings"])


def test_imports_below_the_proto_folder_are_followed(doc):
    assert "common.Ack" in doc["grpc"]["messages"]


def test_an_import_outside_the_proto_folder_is_refused(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "secret.proto").write_text('syntax = "proto3"; package s; message S {}')
    (tmp_path / "a" / "x.proto").write_text('syntax = "proto3"; package x; import "../secret.proto"; service X { rpc R(S) returns (S); }')
    d = parse_proto.build(str(tmp_path / "a" / "x.proto"))
    assert any("outside" in w or "not found" in w or "skipped" in w for w in d["warnings"]) or "s.S" not in d["grpc"]["messages"]


def test_garbage_is_rejected_cleanly(tmp_path):
    p = tmp_path / "bad.proto"
    p.write_text("service {{{ rpc")
    with pytest.raises(IntakeError):
        parse_proto.build(str(p))


def test_intake_detects_proto_and_summarizes():
    proc = subprocess.run([sys.executable, str(ROOT / "skills/api-testing/scripts/intake.py"), str(FIXTURES / "shop.proto"), "--summary"],
                          capture_output=True, text=True)
    assert proc.returncode == 0 and "Source: grpc" in proc.stdout and "GetPet" in proc.stdout


def test_types_grpc_does_not_have_are_refused_by_name(doc, tmp_path):
    with pytest.raises(IntakeError, match="Not available for gRPC yet: integration"):
        gen_pytest.generate(doc, tmp_path / "x", ["integration"])


def test_suite_files_and_notes(suite):
    for name in ("grpc_runtime.py", "methods.py", "conftest.py", "test_grpc_smoke.py", "test_grpc_security.py"):
        assert (suite / name).exists(), name
    assert (suite / "protos" / "shop.proto").exists() and (suite / "protos" / "common.proto").exists()


def test_clean_service_passes_everything(suite):
    results, proc = run_suite(suite, URL, ENV)
    assert failed(results) == [] and len(passed(results)) >= 60, proc.stdout[-2000:]


def test_without_grpcurl_everything_skips(suite, tmp_path):
    results, _ = run_suite(suite, URL, {**ENV, "APITEST_GRPCURL": str(tmp_path / "nope")})
    assert failed(results) == [] and not passed(results)
    assert any("grpcurl is not installed" in m for _, m in results.values())


@pytest.mark.parametrize("bug,expected", [
    ("noauth", ["test_grpc_negative::test_requires_authentication", "test_grpc_negative::test_invalid_token_is_rejected"]),
    ("acceptsbad", ["test_grpc_negative::test_GetPet_unknown_id_is_rejected"]),
    ("reflection", ["test_grpc_security::test_reflection_disabled"]),
    ("badshape", ["test_grpc_contract::test_contract_GetPet_matches_proto"]),
])
def test_seeded_flaws_are_found(suite, bug, expected):
    results, proc = run_suite(suite, URL, {**ENV, "FAKE_GRPC_BUGS": bug})
    assert failed(results) == sorted(expected), proc.stdout[-2000:]


@pytest.mark.parametrize("bug", ["crash", "leak"])
def test_server_faults_and_leaks_are_found_by_the_security_tests(suite, bug):
    results, proc = run_suite(suite, URL, {**ENV, "FAKE_GRPC_BUGS": bug})
    bad = failed(results)
    assert bad and all(n.startswith("test_grpc_security::") for n in bad), proc.stdout[-2000:]


def test_a_failure_never_prints_the_token(suite):
    _, proc = run_suite(suite, URL, {**ENV, "FAKE_GRPC_BUGS": "noauth"})
    assert "tokA" not in proc.stdout + proc.stderr


def test_unreachable_target_skips_instead_of_failing(suite):
    results, _ = run_suite(suite, "http://localhost:1", ENV)
    assert all(n.startswith("test_grpc_smoke::") for n in failed(results))     # smoke tests are the reachability check
    assert any("could not connect" in m for _, m in results.values())


def test_production_blocks_writes(suite):
    results, _ = run_suite(suite, URL, {**ENV, "APITEST_TARGET_ENV": "production"})
    assert "test_grpc_positive::test_CreatePet_succeeds" not in passed(results) and "test_grpc_positive::test_DeletePet_succeeds" not in passed(results)


def test_other_features_do_not_need_the_grpc_tools(tmp_path):
    """No grpcurl, no ghz, no grpcio: generating and running REST and GraphQL suites works and never mentions them."""
    import os
    from mock_graphql import running as gql_server
    from suite_runner import GQL_ENV
    nogrpc = tmp_path / "bin"
    nogrpc.mkdir()
    script = ("import sys, subprocess\\n"
              "sys.modules['grpc'] = None\\n"                      # `import grpc` would raise ImportError
              "sys.path[:0] = [sys.argv[1]]\\n"
              "import intake, gen_pytest\\n"
              "assert 'grpc_runtime' not in sys.modules\\n"
              "print('ok')\\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("APITEST_", "FAKE_"))}
    env["PATH"] = str(nogrpc) + os.pathsep + "/usr/bin:/bin"
    proc = subprocess.run([sys.executable, "-c", script.replace("\\n", "\n"), str(ROOT / "skills/api-testing/scripts")],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0 and "ok" in proc.stdout, proc.stderr
    import parse_graphql
    doc = parse_graphql.build(str(FIXTURES / "shop.graphql"))
    out = tmp_path / "gql"
    gen_pytest.generate(doc, out, ["smoke", "positive"])
    assert not (out / "grpc_runtime.py").exists()
    with gql_server() as url:
        results, proc = run_suite(out, url, {**GQL_ENV, "APITEST_GRPCURL": str(tmp_path / "missing")})
    assert failed(results) == [] and passed(results), proc.stdout[-1500:]
