# Security testing (OWASP API Security Top 10, 2023)

`gen_pytest.py --types security` -> `test_security.py`. **Only test APIs you own or are authorized to test.**

## Safety gate (non-negotiable)
Every active probe is marked `@pytest.mark.intrusive` and skipped by `conftest.py` unless:
- the target host is local (`localhost`, `127.*`, `::1`, `*.localhost`, `*.local`), **or**
- `APITEST_TARGET_ENV` is a non-production value (`dev`, `staging`, `qa`, `test`, `sandbox`, `local`) **and** `APITEST_AUTHORIZED=yes`.
`APITEST_TARGET_ENV=production|prod|live` blocks intrusive tests even on a local host. Before generating or running, ask the
user to confirm (a) non-production and (b) authorization, and record the answer in the plan. Never set these variables for them.
Passive checks (response headers, CORS, sensitive fields in responses) are ordinary GETs and need no gate.

## Coverage
| OWASP API 2023 | Generated checks | Notes |
|----------------|------------------|-------|
| API1 BOLA | user A's credentials requested against another user's object ids (`APITEST_VICTIM_<PARAM>`); expects 4xx | GET by default; PUT/PATCH/DELETE need `APITEST_ALLOW_DESTRUCTIVE=yes`; skipped without victim ids |
| API2 Broken authentication | garbage credentials rejected on every protected endpoint; tampered JWT (bad signature, `alg: none`, stripped signature); expired token (`APITEST_EXPIRED_TOKEN`) | missing credentials are a functional negative test |
| API3 BOPLA | mass assignment (undeclared `is_admin`, `role`, `owner_id`, `id`, `balance`... must not be stored/returned); excessive data exposure (password/hash/secret/token-like fields in responses) | response scan runs on safe GETs |
| API4 Resource consumption | page size 1,000,000 must be capped or rejected; rate limiting present (opt-in `APITEST_EXPECT_RATE_LIMIT=yes`) | bounded burst, see `references/resilience.md` |
| API5 BFLA | admin-looking endpoints (`/admin`, `/internal`, tag `admin`) with ordinary credentials must be refused | needs `APITEST_ASSUME_NON_ADMIN=yes` (you confirm the token is non-admin) |
| API6 Sensitive business flows | **plan only**: list flows (sign-up, checkout, coupon, SMS) and ask the user which need anti-automation tests | no generic automated check |
| API7 SSRF | url/uri/callback/webhook/redirect/target parameters given `169.254.169.254`, `127.0.0.1:22`, `file:///etc/passwd`; internal data in the response is HIGH, acceptance is MEDIUM | the probe only sends the URL; it never fetches anything itself |
| API8 Misconfiguration | `nosniff`, HSTS (https), no version in `Server`/`X-Powered-By`; CORS must not reflect arbitrary origins with credentials; error bodies must not leak stack traces or DB errors | |
| API9 Improper inventory | **plan only**: deprecated/undocumented versions and hosts; compare `intake.json` with traffic/gateway inventory | `spec_diff.py` shows deprecations |
| API10 Unsafe consumption | **plan only**: third-party/webhook inputs the API trusts | |
| Injection (A03 classic) | 7 payloads (SQL, NoSQL, command, traversal, XSS, template) into string query params, body fields (3 per endpoint) and path ids; 5xx, DB errors/stack traces, file content, or unescaped HTML reflection fail | payloads are non-destructive probes |

## Findings
Failures print `[SEVERITY][OWASP id] what happened`, status, redacted body and a **redacted curl reproduction**
(`Authorization` and secret-looking fields become `<redacted>`; substitute your own credential when rerunning).
Severity: HIGH = data/privilege exposure, MEDIUM = missing hardening or crash, LOW = disclosure/hygiene.

## Environment
`APITEST_VICTIM_<PARAM>`, `APITEST_EXPIRED_TOKEN`, `APITEST_ASSUME_NON_ADMIN`, `APITEST_EXPECT_RATE_LIMIT`, `APITEST_RATE_BURST`,
plus the credentials listed in `conftest.py`. Never print or log secret values; the runtime redacts them in every message.

## Limits (say so in the report)
These are black-box probes for the common, automatable flaws. They do not replace a manual review of authorization logic,
business-flow abuse, or a full pentest. A pass means "this probe found nothing", not "secure".
