"""Safety gate for non-functional runs (load, stress, spike, soak, fuzz). Stdlib only, offline.

Mirrors the gate in templates/pytest/apitest_runtime.py (intrusive tests) and the JavaScript gate inside every
generated k6 script; tests/test_nonfunctional.py proves the three agree on a host matrix. This copy is deliberately
a little stricter where the spellings are ambiguous, never looser.

Rules (CLAUDE.md, non-negotiable):
  * APITEST_TARGET_ENV=production|prod|live blocks everything, even a local address.
  * Loopback targets (`localhost`, `*.localhost`, 127.x.x.x, ::1) are assumed to be the user's own machine.
  * Every other target needs APITEST_TARGET_ENV set to a non-production value AND APITEST_AUTHORIZED=yes.
  * No string-prefix host checks: IP literals are parsed, names are matched label by label.
Never set these variables on the user's behalf; ask the user to confirm and export them.
"""
from __future__ import annotations

import os
import re
import urllib.parse
from typing import Mapping

NON_PROD_ENVS = {"local", "dev", "development", "test", "testing", "staging", "stage", "qa", "sandbox"}
PROD_ENVS = {"prod", "production", "live"}
TRUTHY = {"1", "true", "yes", "y", "on"}

# Hard ceilings (a user may lower them with CLI flags or env vars, never raise them).
HARD_MAX_RPS = 100
HARD_MAX_VUS = 200
HARD_MAX_DURATION_S = 4 * 3600
MAX_RAMP_RPS_PER_S = 5   # steady ramps (load, stress, soak); spike has its own rule in gen_k6.py

# scheme://host[:port][/path?query#fragment]. No userinfo, no backslash, no whitespace/control characters.
_URL_RE = re.compile(
    r"^(?P<scheme>https?)://(?P<host>\[[0-9A-Fa-f:]+\]|[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)"
    r"(?::(?P<port>[0-9]{1,5}))?(?P<rest>[/?#][^\s\\\x00-\x1f]*)?$")
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_OCTET = r"(?:0|[1-9][0-9]{0,2})"
_IPV4_RE = re.compile(rf"^({_OCTET})\.({_OCTET})\.({_OCTET})\.({_OCTET})$")


def target_host(base_url: str) -> str | None:
    """Host of a plain http(s) URL, or None if the URL is ambiguous or not plain http(s).

    The strict grammar above is the first check; urllib.parse must then agree on the host (and, when urllib3 is
    importable, so must it). Disagreement means a parser-differential trick, so the URL is refused."""
    m = _URL_RE.match(base_url or "")
    if not m:
        return None
    host = m.group("host").lower()
    if m.group("port") and int(m.group("port")) > 65535:
        return None
    try:
        if (urllib.parse.urlsplit(base_url).hostname or "").lower() != host.strip("[]"):
            return None
    except ValueError:
        return None
    try:
        from urllib3.util import parse_url  # type: ignore[import-not-found]
    except ImportError:
        return host.strip("[]")
    try:
        other = (parse_url(base_url).host or "").lower().strip("[]")
    except Exception:  # noqa: BLE001 - any parser error means "refuse"
        return None
    return host.strip("[]") if other == host.strip("[]") else None


def is_local_host(host: str | None) -> bool:
    """Loopback only. `localhost` / `*.localhost` by label, IPv4 127.x.x.x in canonical form, or exactly `::1`.

    127.0.0.1.nip.io is a remote DNS name; decimal/hex/octal IP spellings, leading zeros and other IPv6
    spellings of loopback count as remote (conservative)."""
    h = (host or "").lower().rstrip(".")
    if not h:
        return False
    if h == "::1":
        return True
    m = _IPV4_RE.match(h)
    if m:
        octets = [int(x) for x in m.groups()]
        return all(o <= 255 for o in octets) and octets[0] == 127
    if ":" in h:
        return False
    labels = h.split(".")
    if not all(_LABEL_RE.match(label) for label in labels):
        return False
    return labels == ["localhost"] or (len(labels) >= 2 and labels[-1] == "localhost")


def env_true(name: str, env: Mapping[str, str] | None = None) -> bool:
    return (env if env is not None else os.environ).get(name, "").strip().lower() in TRUTHY


def gate_reason(base_url: str, env: Mapping[str, str] | None = None) -> str | None:
    """None when a non-functional run may start, else why it must not."""
    e = env if env is not None else os.environ
    host = target_host(base_url)
    if host is None:
        return "safety gate: the target must be a plain http(s) URL (no credentials, backslashes, spaces or ambiguous host)"
    tenv = e.get("APITEST_TARGET_ENV", "").strip().lower()
    if tenv in PROD_ENVS:
        return "safety gate: APITEST_TARGET_ENV says production; load, stress, spike, soak and fuzz runs never target production"
    if is_local_host(host):
        return None
    if tenv not in NON_PROD_ENVS:
        return ("safety gate: confirm the target is non-production: set APITEST_TARGET_ENV to one of "
                + "/".join(sorted(NON_PROD_ENVS)))
    if not env_true("APITEST_AUTHORIZED", e):
        return "safety gate: confirm you own or are authorized to test this target: set APITEST_AUTHORIZED=yes"
    return None
