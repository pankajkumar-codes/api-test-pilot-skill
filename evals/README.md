# Evals (Phase 5)

`evals/run_evals.py` runs the whole skill pipeline (intake, generate, run, report) against an eval target and scores
**expected vs found**. Results and their status live in [`docs/evals.md`](../docs/evals.md); the target list is
[`eval-apis.md`](../eval-apis.md).

```
python evals/run_evals.py --list                       # targets, seeded flaws, real-target commands
python evals/run_evals.py --target all --markdown      # mock mode, offline, prints the result tables (about two minutes)
python evals/run_evals.py --target petstore --variant clean --variant noauth
```

Requirements: Python 3.11+, `pip install -r requirements-dev.txt`; the real local FastAPI target also needs `pip install -r evals/requirements.txt`.
Scratch output (generated suites, JUnit XML, reports) goes to `evals/out/` (git-ignored); every run prints where its report is.

## Two modes, never mixed up

| Mode | What runs | Label in docs/evals.md |
|------|-----------|------------------------|
| mock | a local stdlib stand-in for the target, one seeded flaw at a time plus a clean variant | **mock-based, real target not run** |
| real local | the seeded FastAPI app in `evals/targets/` (real server, real HTTP) | real local server (self-hosted, seeded by us) |
| real | a target **you** started, via `--real --base-url` | real target run by the person |

The mocks and the expected-test lists were written by the same people who wrote the generators. A mock result shows that the pipeline
works end to end and that the checks catch *these* seeded flaws. It says nothing about how the real Petstore, VAmPI or DVGA behave.

## Run the real evals on your own machine

Rules: only targets you started yourself, on your own machine or network. The generated suite runs its intrusive checks (injection, BOLA,
mass assignment, SSRF, rate limits) automatically only for loopback targets; for anything else you must export `APITEST_TARGET_ENV`
(a non-production value) and `APITEST_AUTHORIZED=yes` yourself. The driver never sets either. Credentials come from `APITEST_*`
environment variables; nothing is written to disk. Load, stress, soak, spike, chaos and fuzz are not part of these evals; run them with their own
reference files, and only on self-hosted copies.

Each command writes `evals/out/<target>/real/report.md` (the findings, with repro commands) and prints the failing generated tests.
Copy what you find into the "found" column of `docs/evals.md`.

**Containers: bind to loopback and pin the image.** VAmPI and DVGA are intentionally vulnerable (SQL and OS command injection among others),
so every `docker run` below publishes the port on `127.0.0.1` only (`-p 127.0.0.1:PORT:PORT`), never on all interfaces, and uses a pinned image
(`name:tag@sha256:digest`; VAmPI publishes only `latest`, so it is pinned by digest alone). The digests are the ones Docker Hub's API reported
on 2026-11-12; they were **not** pulled or verified in the build sandbox (no Docker daemon). After `docker pull`, check that the digest it prints
matches, and when you bump an image, re-pin it. Stop and remove the containers when done (`docker rm -f petstore vampi-vuln vampi-fixed dvga`).

### 1. Swagger Petstore (spec: `evals/specs/petstore-openapi.yaml`)
```
docker run -d --name petstore -p 127.0.0.1:8080:8080 swaggerapi/petstore3:1.0.29@sha256:97e5aa178dd4d160af9e31a900a685a943cd715cab7ba6ca6377fe976e95d1cf
export APITEST_API_KEY_KEY=<any value your instance accepts>      # header api_key
export APITEST_PETSTORE_AUTH_TOKEN=<any value your instance accepts>
export APITEST_PATH_PETID=1 APITEST_PATH_ORDERID=1 APITEST_PATH_USERNAME=user1   # ids that exist on your instance
export APITEST_ALLOW_DESTRUCTIVE=yes                              # only because this container's data can be lost
python evals/run_evals.py --target petstore --real --base-url http://localhost:8080/api/v3
```
Expected: not known in advance. The reference implementation has no ownership model (do not set `APITEST_VICTIM_*`). Any HIGH finding is
worth a manual look: it is either a real flaw of that build or a false positive to fix in the skill.

### 2. VAmPI (spec: `evals/specs/vampi-openapi.yaml`), vulnerable and fixed
```
docker run -d --name vampi-vuln -p 127.0.0.1:5000:5000 erev0s/vampi@sha256:0a5a224b6e14ae7da6a6ea265178ff71286ff903aec74adee98f660bb0e4ca12
docker run -d --name vampi-fixed -p 127.0.0.1:5001:5000 -e vulnerable=0 erev0s/vampi@sha256:0a5a224b6e14ae7da6a6ea265178ff71286ff903aec74adee98f660bb0e4ca12   # README: `vulnerable=0` turns the flaws off
curl -s localhost:5000/createdb ; curl -s localhost:5001/createdb
# register your own users on each instance and log in (never reuse a real password):
curl -s -X POST localhost:5000/users/v1/register -H 'Content-Type: application/json' -d '{"username":"evaluser","password":"evalpass1","email":"eval@example.com"}'
export APITEST_BEARERAUTH_TOKEN=$(curl -s -X POST localhost:5000/users/v1/login -H 'Content-Type: application/json' \
  -d '{"username":"evaluser","password":"evalpass1"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["auth_token"])')
export APITEST_ASSUME_NON_ADMIN=yes APITEST_ALLOW_DESTRUCTIVE=yes APITEST_PATH_USERNAME=evaluser APITEST_VICTIM_USERNAME=<another registered user>
export APITEST_PATH_BOOK_TITLE=<a book you added> APITEST_VICTIM_BOOK_TITLE=<a book owned by the other user> APITEST_KNOWN_USERNAME=<an existing user>
python evals/run_evals.py --target vampi --real --base-url http://localhost:5000
# repeat against :5001 with a token from that instance; the fixed instance is the false-positive baseline
```
Expected (README, "List of Vulnerabilities"): SQL injection, unauthorized password change, BOLA, mass assignment, excessive data exposure
through the debug endpoint, user/password enumeration, RegexDoS, no rate limiting, weak JWT key. The skill is not expected to cover RegexDoS
or the weak JWT key, and mass assignment is a known gap (see `docs/evals.md`).

### 3. Seeded FastAPI app (real server, started by the driver, no Docker)
```
pip install -r evals/requirements.txt
python evals/run_evals.py --target seeded          # starts the app on a free port once per variant; clean + 5 seeded flaws
```
To start it yourself: `SEEDED_BUGS=all python -m uvicorn seeded_fastapi:app --app-dir evals/targets --port 8101`, then
`curl localhost:8101/openapi.json -o seeded.json` and
`APITEST_BEARERAUTH_TOKEN=eval-token-a APITEST_PATH_ITEM_ID=1 APITEST_VICTIM_ITEM_ID=2 APITEST_ALLOW_DESTRUCTIVE=yes APITEST_ASSUME_NON_ADMIN=yes python evals/run_evals.py --target seeded --real --base-url http://127.0.0.1:8101 --spec seeded.json`.

### 4. DVGA (GraphQL)
```
docker run -d --name dvga -p 127.0.0.1:5013:5013 -e WEB_HOST=0.0.0.0 dolevf/dvga:2.2.0@sha256:040aa33c199d99f3380c9ff9a1ee5d725e9abca7b189c63a35a2a73bda79c957     # README: Beginner/Expert modes change difficulty; record which you used
python evals/dump_introspection.py http://localhost:5013/graphql -o dvga-introspection.json   # a read-only introspection query
python evals/run_evals.py --target graphql --real --base-url http://localhost:5013 --spec dvga-introspection.json
```
DVGA documents (README scenarios): batch, deep recursion, field duplication and alias attacks, introspection, field suggestions, stack-trace errors,
SSRF, OS command and SQL injection, JWT forgery. The skill's GraphQL checks cover introspection, depth, aliases, batching, suggestions,
verbose errors, field-level authorization, BOLA and injection in variables; the others are out of its scope or not built.
Set `APITEST_BEARERAUTH_TOKEN` (a DVGA login token) and `APITEST_GQL_ID` / `APITEST_GQL_VICTIM_ID` (ids of your own and someone else's object)
if you want the authorization checks to run; without them they skip with a reason.

## Files
- `run_evals.py` the driver; `dump_introspection.py` saves a GraphQL schema from a running endpoint
- `mocks/` Petstore and VAmPI stand-ins (stdlib), `_schema.py` a tiny schema checker the clean mocks use
- `targets/seeded_fastapi.py` the real local FastAPI target; `specs/` vendored upstream OpenAPI documents (with source and hashes)
- the GraphQL stand-in is `tests/mock_graphql.py` with `tests/fixtures/shop.graphql`
