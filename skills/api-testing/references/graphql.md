# GraphQL testing

Pipeline: `parse_graphql.py` (or `intake.py`, which auto-detects) -> intake JSON -> `gen_pytest.py` -> pytest.
```
python scripts/intake.py schema.graphql -o intake.json --url http://localhost:4000/graphql   # SDL, or an introspection JSON file
python scripts/gen_pytest.py intake.json -o gql_tests --types all                          # smoke,positive,negative,edge,security,contract,integration,data-validation,resilience (fuzz is opt-in)
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

## Resilience tests
`resilience` (part of `all`) writes `test_graphql_resilience.py`:
- each query run twice must agree on status, on whether it reports errors and on the fields and kinds of the data (values may change);
- each query must answer within `APITEST_SLOW_MS` (default 2000 ms; LOW finding otherwise);
- a list query with a size argument (`first`, `limit`, `take`, `page_size`, `size`, `count`, `per_page`) gets `1` (at most one item), `0`, `-1` and the int32 maximum, and one with a cursor argument (`after`, `cursor`, `offset`, `skip`, `page`, `start`) gets an invented, an empty and a very long cursor; none may crash the server;
- an `intrusive` burst of `APITEST_RATE_BURST` (default 30) tiny queries: a 429 or 503 must carry a valid `Retry-After` and the API must recover after it (no throttling means nothing to check);
- for each creatable type (same pairing as the integration flows) the object is created, deleted twice, and the second delete must not crash the server. It only touches its own data.
Pagination is found by argument names, so a connection-style `edges`/`pageInfo` answer needs hand-written tests.

## Data-validation tests
`data-validation` (part of `all`) writes `test_graphql_data_validation.py`. Each case breaks exactly one thing on an otherwise valid request and expects the server to reject it (a 4xx or a well-formed `errors` answer; success or a 5xx is a finding):
a missing required field inside an input object, a wrong type for an input field or an enum value, an unknown field in an input object, and a breach of a `@constraint` limit.
Wrong types of top-level arguments stay with the negative tests.
`@constraint` is the validation directive many servers use (`graphql-constraint-directive`): `minLength`, `maxLength`, `min`, `max` and `format` (email, uuid, url, uri, date-time, date, ipv4) are read from the SDL and get a too-short, too-long, below, above or badly formatted value.
`pattern` is read but not mutated, and limits that are not a number or a string of the right kind are ignored. Introspection JSON does not carry directives, so constraint cases need the SDL file; without limits only the type cases are generated, and the generator says so.
A server that accepts a bad value stores it: these cases write like a positive mutation test does, so use a test environment. They are skipped on production targets, and `delete*`-style mutations still need `APITEST_ALLOW_DESTRUCTIVE=yes`.

## Integration flows
`integration` (part of `all`) writes `test_graphql_integration.py`: one lifecycle test per object type that has an `id`, a `create*`/`add*`/`new*`/`register*` mutation, a query that reads it by id and a `delete*`/`remove*`/`destroy*` mutation
(the delete name must end with the type name, so `deletePet` pairs with `Pet`). The flow is create -> read -> list (when a list query without required arguments exists) -> update (an `update*`/`edit*`/`modify*`/`patch*`/`set*` mutation with an `ID` argument and a String field the type also returns) -> read -> delete -> read (must be gone or an error).
It only touches the object it creates, so it needs no `APITEST_GQL_<ARG>` ids and no `APITEST_ALLOW_DESTRUCTIVE`; the object is deleted at the end even when a step fails. Writes are skipped on production targets.
String fields that the type also returns are tagged with a run id and compared after the read. Findings are MEDIUM: not readable after create, missing from the list (checked only when fewer than 50 items come back), old value after an update, still readable after a delete.
A schema with no such type gets a generator note instead of a test. Flows across unrelated types are not derived: write them by hand.

## Fuzz tests
`--types fuzz` (opt-in, never part of `all`, `intrusive`: non-production and authorized targets only) writes `test_graphql_fuzz.py`, one test per query and mutation. Subscriptions are skipped.
Payloads travel as variables: wrong types, boundary numbers, long and unicode strings, injection strings, missing and extra variables, plus malformed envelopes, damaged query text and other content types.
A 4xx or well-formed `errors` is the right answer; 5xx, hangs, dropped connections, stack traces and very slow answers are findings. Bounds and variables are the same as in `fuzzing.md`;
`delete*`-style mutations also need `APITEST_ALLOW_DESTRUCTIVE=yes`.

## Contract tests
`contract` runs each query that returns an object (no mutations: they write) with its default selection and checks the answer against what the schema declares:
every selected field is present, scalars have the right JSON type (`Int` an integer, `Float` a number, `Boolean` a boolean, `String` a string, `ID` a string or integer),
enum values are ones the schema lists, `null` only where the type is nullable (also list items), and list types arrive as arrays. Custom scalars are not checked. A scalar `Int` answered as `1.0` is reported as a wrong type, because JSON has no integer-valued floats here: fix the server or ignore that finding.
Only the selected scalar and enum fields are checked, up to 20 list items per answer; nested objects are not walked. Mismatches are MEDIUM findings.
Needs the same ids as the positive tests (`APITEST_GQL_<ARG>`), otherwise the test skips and names the variable.

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
All pytest test types are available for GraphQL; load testing with k6 is in `load.md`.

Only test APIs the user owns or is authorized to test. The safety gate applies to load/stress/fuzz as well.
