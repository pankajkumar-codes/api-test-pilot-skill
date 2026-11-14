# Public sandbox runs (read-only)

Reports from a read-only run of the skill against four public sandbox APIs on 2026-11-14. The specs used are in `specs/`; three of the four were written by hand
from the providers' documentation, so a "spec mismatch" finding can be the spec's fault as much as the API's.

| API | Report | Tests | Pass | Fail | Skip |
|---|---|---|---|---|---|
| JSONPlaceholder | [jsonplaceholder.html](jsonplaceholder.html) | 378 | 24 | 2 | 352 |
| ReqRes | [reqres.html](reqres.html) | 192 | 21 | 11 | 160 |
| HTTPBin | [httpbin.html](httpbin.html) | 413 | 139 | 8 | 266 |
| Webhook.site | [webhook-site.html](webhook-site.html) | 55 | 2 | 0 | 53 |
| **Total** | | **1038** | **186** | **21** | **831** |

Rerun (read-only, polite to the host; replace the spec, the URL and the output folder for each API):

```bash
SK=skills/api-testing/scripts
python $SK/intake.py examples/public-sandbox/specs/jsonplaceholder.openapi.json -o intake.json
python $SK/gen_pytest.py intake.json -o api_tests --types smoke,positive,negative,edge,data-validation,contract,security,integration
APITEST_READ_ONLY=yes APITEST_DELAY_MS=300 python -m pytest api_tests -p no:cacheprovider -q -ra --junitxml=results/pytest.xml
python $SK/summarize_results.py --junit results/pytest.xml --intake intake.json --base-url https://jsonplaceholder.typicode.com -o reports/report.html
```

No credentials, no load, fuzz, stress, spike, soak or chaos, and no resilience tests (they send bursts). Only GET, HEAD and OPTIONS were sent.
Do not run the load, fuzz or chaos types against these hosts.
