# Test plan — {API name}

- Date / author:
- Spec source: {file}, type {openapi|postman|curl}, parsed with `scripts/intake.py`
- Base URL / environment: {url} ({local|staging|other})
- Protocol: REST | GraphQL (Phase 2)

## Authentication
| Scheme | Env var(s) (names only, never values) | Notes |
|--------|----------------------------------------|-------|

## Endpoints in scope
| ID | Method | Path | Auth | Test types (TAXONOMY #) | Notes |
|----|--------|------|------|--------------------------|-------|

## Test types selected
| Type | Included | Why / why not |
|------|----------|---------------|
| Smoke, Positive, Negative, Edge | | |
| Integration (flows) | | |
| Contract | | |
| Security | | |
| Data validation, Resilience | | |
| Load, Stress, Spike, Soak (gated) | | |
| Chaos plan, Fuzzing (gated) | | |

## Out of scope / assumptions
- Parser warnings carried over:

## Safety confirmation (required for load/stress/spike/soak/chaos/fuzz)
- [ ] Target is non-production
- [ ] User owns or is authorized to test it
- [ ] Confirmed by the user in this conversation (quote the confirmation):

## Run and rerun
- Output directory:
- Rerun command:

## Approval
- [ ] User approved this plan
