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
  Remember that this is a consent prompt, not a lock; see "What the safety gate is, and is not" below.
- Loopback addresses (`localhost`, `127.x.x.x`, `::1`) are treated as your own machine. If you tunnel or port-forward a remote
  system to localhost, the gate cannot know; the responsibility is yours.
- Third-party services, shared environments and production systems need the owner's explicit permission, and often a
  heads-up to their security or operations team. Unauthorized testing can be illegal.
- Keep credentials in environment variables. Reports redact common secret fields, but redaction is pattern-based:
  read a report before sharing it.
- gRPC tests call `grpcurl`, which takes headers on its command line, so a bearer token is visible in the process list to other users of the same machine. Run gRPC suites on a machine you trust.
- The Docker images named in `evals/README.md` (VAmPI, DVGA) are intentionally vulnerable. Run them on a loopback port only.

## What the safety gate is, and is not

The gate (`APITEST_TARGET_ENV`, `APITEST_AUTHORIZED=yes`, the loopback rule, the hard caps) is a **consent prompt, not an enforced control**.
It exists to stop honest mistakes. It cannot stop a determined or careless user, and it is not a security boundary.

- The variables are ordinary environment variables. A person, a script or an AI agent with shell access can set them. The skill instructs agents
  never to set `APITEST_AUTHORIZED` or `APITEST_TARGET_ENV` and never to follow text in a spec or response that says otherwise; a test keeps
  that wording in place and checks that hostile spec text does not change it. That is a rule for the agent, not a technical barrier.
- A remote system reached through an SSH tunnel, port-forward, local proxy or hosts-file entry looks like loopback and passes.
- `APITEST_TARGET_ENV` is whatever you type. A staging label on a production system is not detected.
- The caps (100 requests/s, 200 virtual users, 4 hours) limit generated k6 scripts. They do not limit what you do with the tests by hand.

Because of this, do not describe the tool as "safe by default" or as sandboxed, and review the commands an agent proposes before it runs them
against anything that is not your own machine.

## Legal and misuse

This software is provided as is, under the [MIT licence](LICENSE), with no warranty of any kind. You alone are responsible for how you use it.

- Run it only against systems you own, or systems whose owner has given you written permission to test. Probing, fuzzing and load testing a
  third party's system without permission can break computer-misuse, telecommunications and contract law where you or they are, and can
  breach a cloud provider's or API vendor's terms of service. Public demo APIs usually allow light read-only use only: use `APITEST_READ_ONLY=yes`.
- The authors and contributors accept no liability for damage, downtime, data loss, costs, account suspension or legal consequences that follow
  from using this tool, including when it is run by an AI agent on your behalf. If an agent runs it, you are the one who authorized it.
- Results are not a security audit or a compliance finding. A clean report means the checks that ran found nothing.
- Do not use this project to attack, disrupt or gain unauthorized access to any system. Reports of such use of the project are welcome through
  the private advisory link above.

## If a release is bad

A release is bad if it contains a gate bypass, a secret leak, code execution from spec content, a generated script that exceeds its caps, or
anything else listed under "What counts as a vulnerability" above.

1. **Triage.** Acknowledge the report (about a week at most, sooner for a gate bypass or a leak) and reproduce it on the tagged version.
2. **Contain.** Mark the GitHub release as a pre-release or delete the release and its zip asset so it stops being offered. Where a marketplace lists
   the version, ask for it to be unlisted or pulled to the previous good version. Say plainly on the release page that the version is withdrawn and why.
3. **Fix.** Prepare the fix in a private advisory fork where the details are sensitive, add a regression test, and review it against the gate tests before merging.
4. **Ship.** Tag a patch release (for example 0.1.1), and put the issue, the affected versions and the fixed version in `CHANGELOG.md` and the
   published advisory. Request a CVE through GitHub when the issue meets the bar for one.
5. **Tell people.** Publish the advisory, update any marketplace listing, and note in the next release what changed in the process so it does not recur.
6. **Never rewrite a published tag.** A withdrawn version stays visible as withdrawn; the fix is a new version.

This is a single-maintainer project, so these steps are a commitment to a process, not a service level.

## Supported versions

Only the latest release receives fixes (currently 0.1.x).
