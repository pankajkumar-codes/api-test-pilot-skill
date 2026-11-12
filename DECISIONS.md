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
