# DECISIONS

Format: date — decision — reason.

- 2026-11-12 — Skill format: Agent Skills (folder with SKILL.md) — portable across Claude and other marketplaces.
- 2026-11-12 — Accessibility testing excluded from v1 — not meaningful for APIs; revisit as separate UI skill.
- 2026-11-12 — Safety gate required for load/stress/spike/soak/chaos/fuzz — marketplace trust and abuse prevention.

- 2026-11-12 — GraphQL included in v1, built in Phase 2 — runs over HTTP/JSON so most modules are reusable; needs its own intake and security checks.
- 2026-11-12 — gRPC deferred to v2 — needs a separate toolchain (proto, grpcurl/ghz) and load tooling.
- 2026-11-12 — Hosted service not a goal — a skill runs in the user's environment; hosting adds infra, billing and legal risk for load/attack testing.

- 2026-11-12 — Single repo — the skill runs in the user's environment, so no core/hosting split. Marketplace packaging (e.g. Claude Code plugin) = extra manifest files in the same repo.
- 2026-11-12 — Functional test stack: pytest + httpx/requests — readable, editable code; portable; generators emit pytest.
- 2026-11-12 — Load tool: k6 — single binary, GraphQL over HTTP, thresholds give pass/fail.
- 2026-11-12 — License: MIT — permissive and simple for an open-source skill.
- 2026-11-12 — Display name and GitHub repo name: `api-test-pilot`; skill folder/identifier stays `api-testing` — branded repo, stable skill path and `name:` in SKILL.md.
- 2026-11-12 — Chaos scope: plan generation only in v1; executable fault injection deferred to v2 — safety and abuse risk.

- 2026-11-12 — One normalized intake JSON for all inputs (OpenAPI, Swagger 2.0, Postman, curl), documented in references/intake.md — generators in Phase 2 depend on one shape, not on the input format.
- 2026-11-12 — YAML: use PyYAML if installed, else a built-in subset reader (no anchors/aliases, errors clearly) — keeps scripts stdlib-only and offline per CLAUDE.md without breaking common specs.
- 2026-11-12 — Parsers never emit secrets: auth becomes env-var names (APITEST_<SCHEME>_TOKEN/USER/PASS/KEY), secret-looking headers and body fields are dropped or `<redacted>` — enforces the no-secrets rule at the earliest point.
- 2026-11-12 — Parsers are offline: remote URLs and external $refs are not fetched (warning instead); multi-file specs must be bundled first — safety and reproducibility.
- 2026-11-12 — Skill scripts are tested with pytest in repo-level tests/ (fixtures in tests/fixtures), not shipped inside the skill folder — keeps the installable skill small.

- 2026-11-12 — Generated suites use `requests` + a copied `apitest_runtime.py`, not a shared pip package — one stack for REST and GraphQL, nothing for users to install besides pytest/requests, and the suite stays editable and portable. (`httpx` stays an allowed alternative; revisit if users ask for async.)
- 2026-11-12 — Suite layout: `conftest.py` + `apitest_runtime.py` + `endpoints.py` (valid baseline requests) + one `test_<type>.py` per type, with case tables and `rt.*` helpers — tests stay short and readable; regenerating overwrites, edits are for the user's copy.
- 2026-11-12 — Test-type boundaries: negative = auth/ids/malformed requests, data-validation = one schema violation per case, edge = accepted boundaries and "never 5xx" inputs, contract = responses vs schema — no case is generated twice.
- 2026-11-12 — Safety gate for active probes in security/resilience/GraphQL abuse checks (`intrusive` marker): allowed only on local hosts, or non-production `APITEST_TARGET_ENV` plus `APITEST_AUTHORIZED=yes`; production blocks even local hosts; non-local targets need `APITEST_TARGET_ENV` at all — extends CLAUDE.md's gate to functional-adjacent probes without weakening it.
- 2026-11-12 — Destructive calls (DELETE on existing ids, `delete*` GraphQL mutations) need `APITEST_ALLOW_DESTRUCTIVE=yes`; integration flows delete only data they created — protects user data while keeping lifecycle coverage.
- 2026-11-12 — Existing ids come from env (`APITEST_PATH_<PARAM>`, `APITEST_GQL_<ARG>`) or spec examples; otherwise the test skips and names the variable — no guessing, no hidden state.
- 2026-11-12 — Opt-in flags for checks that are noisy by default: `APITEST_EXPECT_RATE_LIMIT`, `APITEST_ASSUME_NON_ADMIN` (BFLA), `APITEST_GQL_EXPECT_FIELD_AUTH`, `APITEST_GQL_REQUIRES_AUTH`; opt-outs for deliberate settings: `APITEST_ALLOW_INTROSPECTION`, `APITEST_ALLOW_BATCHING` — the tests cannot know the intended policy.
- 2026-11-12 — Secrets in generated output: spec examples/defaults for secret-named fields are never used (`REPLACE_ME` placeholder); the runtime redacts failures and curl repros using the same `SECRET_KEY_RE` as the parsers (injected at generation time) — one source of truth for "looks like a secret".
- 2026-11-12 — GraphQL intake reuses the REST shape (one `POST /graphql` endpoint + a `graphql` block) and the `APITEST_BEARERAUTH_TOKEN` credential; schema parsing is offline (SDL parser built in, introspection JSON accepted), never queries the endpoint — Phase 3 k6, reporting and auth work unchanged.
- 2026-11-12 — GraphQL variables carry all test input (never string-concatenated into queries); subscriptions are listed but not tested in v1 (needs WebSocket/SSE).
- 2026-11-12 — GraphQL v1 test types: smoke, positive, negative, edge, security. Contract/integration/data-validation/resilience are REST-only for now.
- 2026-11-12 — Generators and the runtime are validated by running generated suites against stdlib mock servers with seeded bugs (tests/mock_rest.py, mock_graphql.py), because eval-apis.md targets are not reachable from the build sandbox; real eval runs remain Phase 5.
- 2026-11-12 — Breaking-change detection (`spec_diff.py`) works on intake JSON, so one tool covers OpenAPI, Postman and GraphQL; it reports spec changes, not client usage.
- 2026-11-12 — Generated code never embeds spec strings raw: every id, path, name and URL is emitted through repr (or sanitized for docstrings/comments), the base URL baked into conftest must match a strict URL charset, and a hostile-spec test (REST and GraphQL) proves nothing executes — a spec is untrusted input and the generated suite runs on the user's machine.
- 2026-11-12 — Local-host auto-pass is limited to `localhost`, `127.*`, `::1` and `*.localhost`; `.local`, `.internal` and `.test` names can be real corporate hosts, so they need `APITEST_TARGET_ENV` (and `APITEST_AUTHORIZED=yes` for intrusive tests). A local address is assumed to be the user's own machine; `APITEST_TARGET_ENV=production` still overrides it.
- 2026-11-12 — `APITEST_TARGET_ENV=production` sends read-only calls only: intrusive probes and every POST/PUT/PATCH/DELETE (and GraphQL mutation) are skipped, because functional write tests would change real data.
