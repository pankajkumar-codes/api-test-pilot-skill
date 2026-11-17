# Local gateway and GraphQL examples

Two small servers that behave like a cloud API gateway in front of a function, and like a managed GraphQL API with an API key.
They exist so the skill can be tried (and tested) against real HTTP servers without an account, and they run on your machine only.

```
pip install -r examples/aws-local/requirements.txt
python examples/aws-local/gateway_app.py --port 8081      # REST, stage prefix /prod, header x-api-key
python examples/aws-local/graphql_app.py --port 8082      # GraphQL at /graphql, header x-api-key
```

Key for both: `local-key-A` (override with `GATEWAY_API_KEY` / `GRAPHQL_API_KEY`). Seeded flaws for experiments come from `GATEWAY_BUGS` and
`GRAPHQL_BUGS` (see the docstring at the top of each file).

## Try the skill on them
```
python skills/api-testing/scripts/intake.py examples/aws-local/openapi.yaml -o /tmp/gw.json
python skills/api-testing/scripts/gen_pytest.py /tmp/gw.json -o /tmp/gw-suite --types all
APITEST_BASE_URL=http://127.0.0.1:8081/prod APITEST_APIKEY_KEY=local-key-A APITEST_PATH_PETID=1 python -m pytest /tmp/gw-suite -v -ra
```
For the GraphQL app, export its schema (`graphql_app.schema.as_str()`), then
`intake.py schema.graphql --url http://127.0.0.1:8082/graphql --api-key-header x-api-key` and run with `APITEST_APIKEY_KEY`.

## What this is and is not
These are stand-ins written for the skill's tests, not the AWS service and not an emulator of it. The gateway app copies the visible behaviour
(stage prefix, `403 Forbidden` for a bad key, `403 Missing Authentication Token` for an unknown route, `429` from a usage plan, `502` for a failing
function). Running the skill against the real AWS emulator (https://github.com/aws/aws-emulator) has not been done yet: see docs/evals.md.
`tests/test_aws_local.py` runs both apps with seeded flaws; it skips when the three packages above are not installed.
