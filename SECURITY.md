# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue:

1. Open a private advisory: <https://github.com/pankajkumar-codes/api-test-pilot-skill/security/advisories/new>
   (GitHub: Security tab, "Report a vulnerability").
2. Say what you found, how to reproduce it, and which version or commit you used. A minimal spec or command is ideal.

This is a single-maintainer project. Expect an acknowledgement within about a week and a fix or a written assessment
as soon as is practical. There is no bug bounty.

**What counts as a vulnerability here**
- The skill or generated code sends traffic to a target the user did not confirm, or a way around the safety gate
  (for example a host-parsing trick that makes a remote target look local, or intrusive tests running without
  `APITEST_TARGET_ENV` and `APITEST_AUTHORIZED=yes`).
- A secret (token, cookie, password, API key) appears in output, reports, logs, repro commands or files on disk.
- Content from a spec, collection, curl command, response or result file is executed, imported or injected into generated
  code or reports in an unescaped form.
- A generated script exceeds its documented hard caps (100 requests/s, 200 virtual users, 4 hours).

Ordinary false positives or false negatives of a test are bugs, not vulnerabilities: use a normal issue.

## Responsible use

This tool sends real HTTP requests, and some of its test types are intrusive (injection and authorization probes, fuzzing,
load, stress, spike and soak). Use it only against systems you own or have written permission to test.

- Run load, stress, spike, soak, chaos and fuzz tests only against a **non-production** target.
  The safety gate refuses anything else, and you confirm the target yourself with `APITEST_TARGET_ENV` and `APITEST_AUTHORIZED=yes`.
  Never let an automated agent set those two variables for you.
- Loopback addresses (`localhost`, `127.x.x.x`, `::1`) are treated as your own machine. If you tunnel or port-forward a remote
  system to localhost, the gate cannot know; the responsibility is yours.
- Third-party services, shared environments and production systems need the owner's explicit permission, and often a
  heads-up to their security or operations team. Unauthorized testing can be illegal.
- Keep credentials in environment variables. Reports redact common secret fields, but redaction is pattern-based:
  read a report before sharing it.
- The Docker images named in `evals/README.md` (VAmPI, DVGA) are intentionally vulnerable. Run them on a loopback port only.

## Supported versions

Only the latest release receives fixes (currently 0.1.x).
