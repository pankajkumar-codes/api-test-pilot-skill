# Contract testing

Two tools, two questions.

## 1. Does the live API match its spec? `gen_pytest.py --types contract`
`test_contract.py` calls GET endpoints (and POST endpoints without path params) and checks:
- the status is documented (or `default` exists);
- `Content-Type` matches the documented one;
- the body validates against the documented response schema (`apitest_runtime.validate`: type, required, enum,
  min/max, length, pattern, format, items, nested objects, `nullable`, `oneOf/anyOf`, `additionalProperties`);
- documented 401/404 error bodies match their error schema.
Violations are reported as `[MEDIUM]` with a redacted curl reproduction. Schemas carrying `$circular`/`$unresolved`
markers are skipped, not guessed. Integration flows also call `expect_contract` after every step.

## 2. Did a new spec version break old clients? `scripts/spec_diff.py`
```
python scripts/intake.py v1.yaml -o old.json && python scripts/intake.py v2.yaml -o new.json
python scripts/spec_diff.py old.json new.json --format markdown -o api-diff.md     # exit 1 when breaking
```
Breaking: endpoint/method removed; success status removed; response field removed, type changed, made nullable, or enum
gained values; new required parameter/body field; request type changed; constraint tightened (`minimum`, `maximum`,
`minLength`, `maxLength`, new constraint); request enum value removed; auth newly required. GraphQL: operation, argument,
field or enum value removed; new required argument; argument/return/field type changed; new required input field.
Non-breaking is listed as `info` (added endpoints/fields, optional params, deprecations).
Use `--fail-on any|breaking|never` in CI. This is also the versioning/compatibility check (taxonomy #17).

Limits: it compares the simplified schemas from intake (no discriminators, no `readOnly` semantics, no semantic
changes such as meaning of a field). It tells you what changed, not whether your clients use it.
