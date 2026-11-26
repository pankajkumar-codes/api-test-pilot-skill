# Resilience: idempotency, pagination, timeouts, rate limits

`gen_pytest.py --types resilience` -> `test_resilience.py`.

| Check | What it asserts |
|-------|-----------------|
| GET repeatable | two identical GETs agree on status and top-level keys |
| response budget | a GET answers within `APITEST_SLOW_MS` (default 2000 ms); `[LOW]` finding otherwise |
| PUT idempotent | the same PUT twice gives the same status and shape |
| DELETE repeat | second DELETE of the same id does not 5xx (needs `APITEST_ALLOW_DESTRUCTIVE=yes`) |
| Idempotency-Key | if the spec documents the header: same key twice -> same status and the same created id |
| pagination | `limit=1` returns at most one item; `limit` 0/-1 handled (no 5xx); offset/page far past the end is empty or a 4xx; page 1 and page 2 differ |
| rate-limit behaviour | a small sequential burst (`APITEST_RATE_BURST`, default 30): any 429/503 carries a valid `Retry-After`, and the API recovers after waiting it |

Rules
- The burst is bounded, sequential and `intrusive` (safety gate). It is **not** a load test. Sustained or concurrent
  traffic belongs to the k6 profiles (`references/load.md`) and is gated separately.
- A paced run (`APITEST_RPS`, `APITEST_DELAY_MS`) skips the rate-limit tests (marker `rate_limit`). `run_all.py` runs them in a burst pass without the pacing when the target is this machine; for other targets run `pytest -m rate_limit` without the pacing once a short burst is acceptable there.
- If no throttling is seen the rate-limit test passes (nothing to verify). Whether throttling should exist is a
  security check: set `APITEST_EXPECT_RATE_LIMIT=yes` and `test_api4_rate_limiting_present` fails when 40 rapid requests are never throttled.
- Retries and client timeouts are client behaviour; what the server owes clients is covered above (`Retry-After`, idempotency).
- Pagination detection uses parameter names (`limit`, `page_size`, `per_page`, `size`, `count`, `take`, `first`; `offset`, `skip`, `start`;
  `page`, `page_number`). Cursor pagination needs hand-written tests.
