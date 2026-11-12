# GraphQL testing

Pipeline: `parse_graphql.py` (or `intake.py`, which auto-detects) -> intake JSON -> `gen_pytest.py` -> pytest.
```
python scripts/intake.py schema.graphql -o intake.json --url http://localhost:4000/graphql   # SDL, or an introspection JSON file
python scripts/gen_pytest.py intake.json -o gql_tests --types all                          # smoke,positive,negative,edge,security
APITEST_BASE_URL=http://localhost:4000 APITEST_BEARERAUTH_TOKEN=... python -m pytest gql_tests -v -ra
```
Get the schema from the user as SDL or as the result of the standard introspection query (saved as JSON). The parser is offline
and never queries the endpoint. `--url` only records the endpoint (credentials in the URL are stripped).

## Intake shape
Same top-level keys as REST (`source`, `servers`, `security_schemes`, `endpoints`, `warnings`) so auth, planning, reporting and
the k6 generator (Phase 3) reuse everything: the one endpoint is `POST <path>` with `{query, variables, operationName}`. The extra
`graphql` block holds `operations` (kind, args with types/required/defaults, return type, scalar selection) and `types`
(fields, enums, input objects, a `sensitive` flag on password/token/email/role-like fields). Defaults of secret-named arguments are dropped.
Subscriptions are listed but not tested (need WebSocket/SSE; v2).

## Differences from REST that the tests handle
- Errors arrive as HTTP 200 + `errors`: positive tests assert on the body (`expect_data`: no `errors`, field present, non-null if the
  schema says so). Negative tests accept a 4xx **or** a well-formed `errors` array (each entry an object with a string `message`);
  silent success of an invalid operation is a failure, a 5xx is a MEDIUM finding.
- Partial success (`data` and `errors` together) counts as an error for a valid operation.
- Clients choose fields: positive tests run the default scalar selection and (for queries) a minimal `{ __typename }` selection.

## Functional tests
- **smoke**: `{ __typename }` and every query without required arguments.
- **positive**: each operation with a valid baseline (required args only; enums use the first value, inputs their required fields).
  `ID` arguments reference existing objects: set `APITEST_GQL_<ARG>` or `APITEST_GQL_<OP>_<ARG>` (e.g. `APITEST_GQL_ID`), otherwise the test
  skips and names the variable. Mutations named `delete*/remove*/destroy*/purge*/reset*...` need `APITEST_ALLOW_DESTRUCTIVE=yes`.
- **negative**: syntax error, unknown root field/sub-field, unknown `operationName`, non-JSON body, missing required variable, wrong
  variable type (Int<-string, enum<-unknown value, input<-string...). Anonymous access is checked only when you set `APITEST_GQL_REQUIRES_AUTH=yes`.
- **edge** (queries): unicode, empty, special characters and 100k-char strings; 0/-1/int32 max/overflow numbers; huge page sizes; explicit `null` vs omitted; empty lists; lowercase enum values. Must be handled, never 5xx.

## Security checks (all `intrusive`: same safety gate as `references/security.md`)
| Check | Fails when |
|-------|-----------|
| introspection | `__schema` answers (set `APITEST_ALLOW_INTROSPECTION=yes` for deliberately open environments). Disable in production |
| field suggestions | errors say "Did you mean ..." (leaks schema even with introspection off) |
| depth limit | a 15+-level query through a recursive type is executed without error |
| alias abuse | 50 aliases of one field are executed in one request (bypasses per-request rate limits) |
| batching | an array of 5 operations is executed (set `APITEST_ALLOW_BATCHING=yes` if intended) |
| field-level authorization | sensitive fields (`email`, `passwordHash`, ...) are returned without credentials. Runs when `APITEST_GQL_EXPECT_FIELD_AUTH=yes`; list public fields you accept by editing the test |
| BOLA | user A queries an object by another user's id (`APITEST_GQL_VICTIM_<ARG>`) and gets data |
| injection | 7 payloads in String variables: 5xx, DB errors or stack traces fail (payloads travel as **variables**, never concatenated into the query) |
| verbose errors | `extensions.exception`, stack traces, file paths or DB errors in `errors` |
| rate limiting | opt-in `APITEST_EXPECT_RATE_LIMIT=yes`: 40 rapid requests never throttled |

## Reuse
Credentials (`APITEST_BEARERAUTH_TOKEN`), redaction, curl reproductions (`variables` are redacted), the safety gate and the
generated `conftest.py` are the REST modules; `gql_runtime.py` only adds the GraphQL-specific assertions.
`spec_diff.py` compares two GraphQL intakes (removed fields/operations, new required arguments, type changes).
Not yet for GraphQL: contract, integration, data-validation and resilience generators (the REST ones are not applicable as-is); load testing via k6 arrives in Phase 3.

Only test APIs the user owns or is authorized to test. The safety gate applies to load/stress/fuzz as well.
