# GraphQL testing (stub — build in Phase 2)

Intake: introspection query or SDL file. Single endpoint (usually POST /graphql).

## Differences from REST to handle
- Errors often return HTTP 200 with an `errors` array: assert on the body, not only status.
- Partial success: `data` and `errors` can both be present.
- Clients choose fields: test with minimal and maximal selections.

## Functional
TODO: positive queries/mutations; negative (invalid args, wrong types, missing required variables,
unknown fields); edge (null vs missing, empty lists, pagination boundaries, large inputs, unicode).

## Security
TODO: query depth and complexity limits, introspection exposure in production, batching and alias
abuse, field-level authorization (BOLA via IDs in arguments), injection via variables, verbose errors,
rate limiting.

## Load
GraphQL over HTTP works with k6; vary query cost, not only request rate.

Only test APIs the user owns or is authorized to test. Safety gate applies to load/stress/fuzz.
