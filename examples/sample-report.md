<!-- Real output of summarize_results.py, produced from a clean directory against `python examples/demo_server.py --bugs bola,leak` (2026-11-12). Not edited. The two /admin/stats findings appear because the token used (tokA) is not an admin; they are not seeded flaws. -->

# Test report — Mock Pets API — 2026-11-12

**Result: FAIL** — 7 finding(s): HIGH 5, MEDIUM 2. 239 passed, 7 failed, 15 skipped, 0 errored of 261 tests.

## Summary
| Test type | Passed | Failed | Skipped | Errors | Total |
|---|---|---|---|---|---|
| contract | 12 | 1 | 0 | 0 | 13 |
| data-validation | 42 | 0 | 0 | 0 | 42 |
| edge | 35 | 0 | 0 | 0 | 35 |
| integration | 2 | 0 | 0 | 0 | 2 |
| negative | 24 | 0 | 2 | 0 | 26 |
| positive | 10 | 1 | 2 | 0 | 13 |
| resilience | 21 | 0 | 2 | 0 | 23 |
| security | 87 | 4 | 9 | 0 | 100 |
| smoke | 6 | 1 | 0 | 0 | 7 |
| **Total** | **239** | **7** | **15** | **0** | **261** |

Test time (sum of per-test times): 0.6 s.

## Coverage per endpoint
| Endpoint | Types run | Pass | Fail | Skipped/error | k6 |
|---|---|---|---|---|---|
| GET /health  \(health\) | contract, positive, resilience, security, smoke | 6 | 0 | 0 |  |
| GET /pets  \(listPets\) | contract, data-validation, edge, negative, positive, resilience, security, smoke | 24 | 1 | 0 |  |
| POST /pets  \(createPet\) | contract, data-validation, edge, negative, positive, security | 47 | 0 | 0 |  |
| GET /pets/\{petId\}  \(getPet\) | contract, data-validation, negative, positive, resilience, security, smoke | 15 | 1 | 0 |  |
| PUT /pets/\{petId\}  \(updatePet\) | data-validation, edge, negative, positive, resilience, security | 52 | 0 | 1 |  |
| DELETE /pets/\{petId\}  \(deletePet\) | data-validation, negative, positive, resilience, security | 7 | 0 | 4 |  |
| GET /pets/\{petId\}/toys  \(listToys\) | contract, data-validation, negative, positive, resilience, security, smoke | 13 | 1 | 0 |  |
| POST /pets/\{petId\}/toys  \(createToy\) | data-validation, edge, negative, positive, security | 29 | 0 | 0 |  |
| GET /pets/\{petId\}/toys/\{toyId\}  \(getToy\) | contract, data-validation, negative, positive, resilience, security, smoke | 15 | 1 | 0 |  |
| DELETE /pets/\{petId\}/toys/\{toyId\}  \(deleteToy\) | data-validation, negative, positive, resilience, security | 8 | 0 | 4 |  |
| GET /admin/stats  \(adminStats\) | contract, negative, positive, resilience, security, smoke | 5 | 3 | 1 |  |
| GET /preview  \(preview\) | contract, data-validation, negative, positive, resilience, security, smoke | 12 | 0 | 0 |  |
| \(API-wide\) | integration, resilience, security | 6 | 0 | 5 |  |

## Load and performance (k6)
No k6 summary supplied.

## Findings (most severe first)
### [HIGH] smoke: expected \[200\], got 403
- Endpoint: GET /admin/stats  \(adminStats\)
- Type: smoke · Rating: default for this test type

**What happened vs expected**

```
GET http://127.0.0.1:8080/admin/stats -> 403 (1 ms)
body: {"code": 403, "message": "admin only"}
```

**Reproduce**

```
curl -sS -i -X GET http://127.0.0.1:8080/admin/stats -H 'Authorization: <redacted>'
APITEST_BASE_URL=http://127.0.0.1:8080 python -m pytest api_tests/test_smoke.py::test_smoke_adminStats -v
```

The curl line is redacted: replace each `<redacted>` header or value with your own credentials (from your environment, never pasted into a report) before it will work.

### [HIGH] \[API3:2023 excessive data exposure\] response exposes sensitive fields: \['\$\[0\].password\_hash', '\$\[1\].password\_…
- Endpoint: GET /pets  \(listPets\)
- Type: security · Rating: tagged by the test

**What happened vs expected**

```
GET http://127.0.0.1:8080/pets -> 200 (1 ms)
body: [{"id": 1, "name": "sample-name", "tag": null, "owner_id": 1, "password_hash": "<redacted>"}, {"id": 3, "name": "Tom", "tag": null, "age": 3, "contact": "o@example.com", "owner_id": 1, "password_hash": "<redacted>…
```

**Reproduce**

```
curl -sS -i -X GET http://127.0.0.1:8080/pets -H 'Authorization: <redacted>'
APITEST_BASE_URL=http://127.0.0.1:8080 python -m pytest api_tests/test_security.py::test_api3_no_sensitive_fields_listPets -v
```

Needs a non-production target you own or are authorized to test. Export APITEST_TARGET_ENV and APITEST_AUTHORIZED=yes yourself (loopback targets pass without them); production is always refused. The curl line is redacted: replace each `<redacted>` header or value with your own credentials (from your environment, never pasted into a report) before it will work.

### [HIGH] \[API1:2023 BOLA\] user A can reach an object owned by another user: request was accepted \(200\)
- Endpoint: GET /pets/\{petId\}  \(getPet\)
- Type: security · Rating: tagged by the test

**What happened vs expected**

```
GET http://127.0.0.1:8080/pets/2 -> 200 (1 ms)
body: {"id": 2, "name": "Fido", "tag": null, "age": 3, "contact": "o@example.com", "owner_id": 2, "password_hash": "<redacted>"}
```

**Reproduce**

```
curl -sS -i -X GET http://127.0.0.1:8080/pets/2 -H 'Authorization: <redacted>'
APITEST_BASE_URL=http://127.0.0.1:8080 python -m pytest api_tests/test_security.py::test_api1_bola_getPet -v
```

Needs a non-production target you own or are authorized to test. Export APITEST_TARGET_ENV and APITEST_AUTHORIZED=yes yourself (loopback targets pass without them); production is always refused. The curl line is redacted: replace each `<redacted>` header or value with your own credentials (from your environment, never pasted into a report) before it will work.

### [HIGH] \[API1:2023 BOLA\] user A can reach an object owned by another user: request was accepted \(200\)
- Endpoint: GET /pets/\{petId\}/toys  \(listToys\)
- Type: security · Rating: tagged by the test

**What happened vs expected**

```
GET http://127.0.0.1:8080/pets/2/toys -> 200 (1 ms)
body: [{"id": 8, "label": "bone", "pet_id": 2}]
```

**Reproduce**

```
curl -sS -i -X GET http://127.0.0.1:8080/pets/2/toys -H 'Authorization: <redacted>'
APITEST_BASE_URL=http://127.0.0.1:8080 python -m pytest api_tests/test_security.py::test_api1_bola_listToys -v
```

Needs a non-production target you own or are authorized to test. Export APITEST_TARGET_ENV and APITEST_AUTHORIZED=yes yourself (loopback targets pass without them); production is always refused. The curl line is redacted: replace each `<redacted>` header or value with your own credentials (from your environment, never pasted into a report) before it will work.

### [HIGH] \[API1:2023 BOLA\] user A can reach an object owned by another user: request was accepted \(200\)
- Endpoint: GET /pets/\{petId\}/toys/\{toyId\}  \(getToy\)
- Type: security · Rating: tagged by the test

**What happened vs expected**

```
GET http://127.0.0.1:8080/pets/2/toys/8 -> 200 (1 ms)
body: {"id": 8, "label": "bone", "pet_id": 2}
```

**Reproduce**

```
curl -sS -i -X GET http://127.0.0.1:8080/pets/2/toys/8 -H 'Authorization: <redacted>'
APITEST_BASE_URL=http://127.0.0.1:8080 python -m pytest api_tests/test_security.py::test_api1_bola_getToy -v
```

Needs a non-production target you own or are authorized to test. Export APITEST_TARGET_ENV and APITEST_AUTHORIZED=yes yourself (loopback targets pass without them); production is always refused. The curl line is redacted: replace each `<redacted>` header or value with your own credentials (from your environment, never pasted into a report) before it will work.

### [MEDIUM] request with valid input should succeed: expected \[200\], got 403
- Endpoint: GET /admin/stats  \(adminStats\)
- Type: contract · Rating: default for this test type

**What happened vs expected**

```
GET http://127.0.0.1:8080/admin/stats -> 403 (1 ms)
body: {"code": 403, "message": "admin only"}
```

**Reproduce**

```
curl -sS -i -X GET http://127.0.0.1:8080/admin/stats -H 'Authorization: <redacted>'
APITEST_BASE_URL=http://127.0.0.1:8080 python -m pytest api_tests/test_contract.py::test_adminStats_response_contract -v
```

The curl line is redacted: replace each `<redacted>` header or value with your own credentials (from your environment, never pasted into a report) before it will work.

### [MEDIUM] request with valid input should succeed: expected \[200\], got 403
- Endpoint: GET /admin/stats  \(adminStats\)
- Type: positive · Rating: default for this test type

**What happened vs expected**

```
GET http://127.0.0.1:8080/admin/stats -> 403 (1 ms)
body: {"code": 403, "message": "admin only"}
```

**Reproduce**

```
curl -sS -i -X GET http://127.0.0.1:8080/admin/stats -H 'Authorization: <redacted>'
APITEST_BASE_URL=http://127.0.0.1:8080 python -m pytest api_tests/test_positive.py::test_adminStats_valid_request -v
```

The curl line is redacted: replace each `<redacted>` header or value with your own credentials (from your environment, never pasted into a report) before it will work.

## Run problems
None.

## Not tested / limitations
Skipped tests, by reason:
- 9 x destructive call: set APITEST\_ALLOW\_DESTRUCTIVE=yes to run it against data you can lose
- 3 x bearer token is not a JWT
- 1 x set APITEST\_EXPIRED\_TOKEN to an expired token for this check
- 1 x this API is expected to rate-limit: set APITEST\_EXPECT\_RATE\_LIMIT=yes
- 1 x confirm the configured credentials belong to an ordinary, non-admin user: set APITEST\_ASSUME\_NON\_ADMIN=yes
No load, stress, spike or soak results were supplied.
Fuzzing, chaos plans and GraphQL abuse checks appear only if their results were passed in; a pass here means the checks that ran found nothing, not that the API is secure.

## How this report was produced
- junit: pytest.xml (sha256 88dd87a44951…)

Regenerate with:

```
python scripts/summarize_results.py --junit results/pytest.xml --intake intake.json --base-url http://127.0.0.1:8080 --tests-dir api_tests --k6-dir k6_tests
```

Secrets are never written: values are scrubbed from every message, URL and command.
