# Vendored upstream OpenAPI documents

Unmodified copies, kept so the mock-based evals are reproducible offline. Mocks and expected results are tied to these exact files.

| File | Upstream | Retrieved | SHA-256 |
|------|----------|-----------|---------|
| `petstore-openapi.yaml` | https://github.com/swagger-api/swagger-petstore (`src/main/resources/openapi.yaml`, master) | 2026-11-12 | `65cfe2e8826c81db0eea34c51d18da3a305ae4840f1a778cd63b89d396efb687` |
| `vampi-openapi.yaml` | https://github.com/erev0s/VAmPI (`openapi_specs/openapi3.yml`, master) | 2026-11-12 | `35200f354f9c9968182fc19458295ed45bb4c368150348c46d5146a7e40a61c9` |

Licenses are the upstream projects' own (see each repository's LICENSE). These files are test fixtures only and are not part of the skill.
Re-fetch with `curl -fsSL <raw url> -o <file>` and re-run `python evals/run_evals.py --target all` if an upstream spec changes.
