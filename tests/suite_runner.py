"""Helpers: generate a suite, run it with pytest in a subprocess against a mock server, read junit results."""
from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REST_ENV = {
    "APITEST_BEARERAUTH_TOKEN": "tokA", "APITEST_PATH_PETID": "1", "APITEST_PATH_TOYID": "7",
    "APITEST_VICTIM_PETID": "2", "APITEST_VICTIM_TOYID": "8", "APITEST_ASSUME_NON_ADMIN": "yes",
}
GQL_ENV = {
    "APITEST_BEARERAUTH_TOKEN": "tokA", "APITEST_GQL_ID": "1", "APITEST_GQL_VICTIM_ID": "2",
    "APITEST_GQL_EXPECT_FIELD_AUTH": "yes",
}


def run_suite(suite: Path, base_url: str, env: dict, args: list[str] | None = None, junit: Path | None = None):
    """Returns (results, proc): results maps 'file::test' -> ('passed'|'failed'|'skipped', message)."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("APITEST_")}
    clean.update(env)
    clean["APITEST_BASE_URL"] = base_url
    junit = junit or suite.parent / f"{suite.name}-junit.xml"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(suite), "-p", "no:cacheprovider", "-q", f"--junitxml={junit}", "--tb=short"]
        + (args or []), env=clean, capture_output=True, text=True, timeout=300)
    results: dict[str, tuple[str, str]] = {}
    if junit.exists():
        for tc in ET.parse(junit).getroot().iter("testcase"):
            name = f"{tc.get('classname', '').split('.')[-1]}::{tc.get('name')}"
            status, msg = "passed", ""
            for child in tc:
                if child.tag in ("failure", "error"):
                    status, msg = "failed", (child.get("message") or "") + (child.text or "")
                elif child.tag == "skipped":
                    status, msg = "skipped", child.get("message") or ""
            results[name] = (status, msg)
    return results, proc


def failed(results: dict) -> list[str]:
    return sorted(k for k, (s, _) in results.items() if s == "failed")


def passed(results: dict) -> list[str]:
    return sorted(k for k, (s, _) in results.items() if s == "passed")
