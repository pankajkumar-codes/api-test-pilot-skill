# Integration flows

`gen_pytest.py --types integration` writes `test_integration.py`. Resources are derived from path structure:
`/pets` (collection: POST create, GET list) and `/pets/{petId}` (item: GET, PUT/PATCH, DELETE). Each resource that can be
created and read gets one lifecycle test:

1. create (valid baseline body) -> status, contract, **id extracted** from the JSON (`<param>`, `id`, `_id`, `uuid`) or the `Location` header
2. read it back -> status, contract, key fields equal what was created
3. list the collection -> the new id is present (skipped when the page looks truncated: 10+ items)
4. update (PUT/PATCH with one string field changed) -> read again -> the change is visible
5. delete -> status; read again -> must be a 4xx (no ghost resource)

Nested resources (`/pets/{petId}/toys`) create their ancestors first; cleanup runs child-before-parent even when an
assertion fails (`cleanup` fixture, best effort, never masks the test result). A resource whose ancestor cannot be created
through the API is reported as a generator note instead of a broken test.

Because flows create and delete **their own data**, they need no existing ids and no `APITEST_ALLOW_DESTRUCTIVE`.
They do write to the target: use a test environment.

Not derived automatically (write by hand, using the same runtime helpers): flows across unrelated resources
(order -> payment -> refund), flows that depend on async processing, and state machines. Ask the user for the business
rules before inventing them; list the flow in the plan and extend `test_integration.py`.
