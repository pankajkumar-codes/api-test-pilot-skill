# Functional testing (stub — build in Phase 2)

## Positive
TODO: for each endpoint, valid request → assert status, schema, headers, key fields.

## Negative
TODO: missing/invalid params, wrong types, bad/expired auth, nonexistent IDs → correct 4xx and error body shape.

## Edge
TODO: boundaries (min/max/length), empty and huge payloads, unicode, null, special characters, duplicates.

## Output
pytest files, one per resource, with a rerun command printed at the end.
