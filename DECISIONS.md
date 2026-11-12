# DECISIONS

Format: date — decision — reason.

- 2026-11-12 — Skill format: Agent Skills (folder with SKILL.md) — portable across Claude and other marketplaces.
- 2026-11-12 — Accessibility testing excluded from v1 — not meaningful for APIs; revisit as separate UI skill.
- 2026-11-12 — Safety gate required for load/stress/spike/soak/chaos/fuzz — marketplace trust and abuse prevention.

- 2026-11-12 — GraphQL included in v1, built in Phase 2 — runs over HTTP/JSON so most modules are reusable; needs its own intake and security checks.
- 2026-11-12 — gRPC deferred to v2 — needs a separate toolchain (proto, grpcurl/ghz) and load tooling.
- 2026-11-12 — Hosted service not a goal — a skill runs in the user's environment; hosting adds infra, billing and legal risk for load/attack testing.

- 2026-11-12 — Single repo — the skill runs in the user's environment, so no core/hosting split. Marketplace packaging (e.g. Claude Code plugin) = extra manifest files in the same repo.

## Open decisions (resolve in Phase 0)
- Functional test stack: pytest + httpx/requests (default) vs Postman/Newman
- Load tool: k6 (default) vs Locust
- License: MIT vs Apache-2.0
- Skill name for marketplaces (default: `api-testing`)
- Chaos scope: plan generation only (default) vs executable fault injection
