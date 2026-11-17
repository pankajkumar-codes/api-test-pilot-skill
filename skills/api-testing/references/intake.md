# Intake: normalized JSON

All parsers (`parse_openapi.py`, `parse_postman.py`, `parse_curl.py`, dispatcher `intake.py`) emit the
same shape, so planning and generators never care about the input format. Python 3.11+, stdlib only;
YAML uses PyYAML when installed, otherwise a built-in subset reader (no anchors/aliases: it errors and
tells the user to install PyYAML or convert to JSON). Parsers are offline: remote URLs are never fetched.
An OpenAPI spec split over several files works: relative file `$ref`s (`./schemas/pet.yaml#/Pet`, a path item in its own file,
a ref back to the root file) are read from disk. Only `.yaml`, `.yml` and `.json` files inside the root spec's folder (and below it) are read, at most 50 files of 5 MB each. The trust boundary is that folder: any such file there that a `$ref` names is loaded, so keep unrelated config out of it. A spec without file refs is parsed as before;
absolute paths, URLs, a `..` that leaves the folder, symlinks pointing out of it, other file types, unreadable or deeply nested files are skipped with a warning that does not quote the file. A spec that uses the top-level key `x-apitest-bundled` is refused.

```
{
  "source":  {"type": "openapi|postman|curl|graphql", "version": "3.0.3", "file": "spec.yaml"},
  "info":    {"title": "...", "version": "..."},
  "servers": ["http://localhost:8080/v1"],
  "security_schemes": {"bearerAuth": {"type": "http", "scheme": "bearer", "env": ["APITEST_BEARERAUTH_TOKEN"]}},
  "webhooks": [],                           // optional, see Rules
  "endpoints": [{
    "id": "createPet", "method": "POST", "path": "/pets", "summary": "", "tags": [], "deprecated": false,
    "auth_required": true,
    "security": [["bearerAuth"]],            // OR of ANDs; [] = public
    "parameters": [{"name": "limit", "in": "query|path|header|cookie", "required": false, "schema": {}}],
    "request_body": {"content_type": "application/json", "required": true, "schema": {}, "example": {}},
    "responses": {"201": {"description": "", "content_type": "application/json", "schema": {}}}
  }],
  "warnings": ["..."]
}
```

## Rules
- **No secrets in output.** Auth values, `Authorization`/cookie/API-key headers, `-u user:pass`,
  `--oauth2-bearer`, URL userinfo (`user:pass@host`) and values of secret-looking names (body fields,
  query params, header/param examples; one shared regex `SECRET_KEY_RE` in `_common.py`) are dropped or
  replaced with `<redacted>`. Regression tests use canary strings across all three parsers.
  Credentials are referenced by env-var name (`APITEST_<SCHEME>_TOKEN|USER|PASS|KEY`).
- **Schemas** are simplified: `$ref`s resolved (examples/defaults of secret-named properties dropped), `allOf` merged, circular refs become `{"$circular": "Name"}`,
  external refs `{"$unresolved": ref}` plus a warning. Depth limit 12.
- **Auth** is effective per endpoint: operation `security` overrides global; `security: []` = public.
- **Postman**: folders become `tags`; saved responses give expected statuses; `{{var}}` stays a variable.
  Collection v2.0/2.1 only.
- **curl**: only `curl` lines are read; numeric/UUID path segments become `{id}` params; no response
  expectations exist, so ask the user what "correct" means.
- **GraphQL** (SDL or introspection JSON, `parse_graphql.py`): same top-level keys; the single endpoint is `POST /graphql`; an extra
  `graphql` block lists operations and types. Details in `references/graphql.md`.
- **Webhooks and callbacks** (OpenAPI 3.1 `webhooks`, operation `callbacks`): listed under a top-level `webhooks` array (only present
  when there are some) as `{kind: webhook|callback, name, method, summary, request_body: {content_type, schema, example}, responses: ["200", ...]}`,
  callbacks also carry `parent` (the endpoint id) and `expression`. They are calls the API makes to the client, so there is nothing to send
  them to: no tests are generated, the summary lists them, and a plan can mention them as untested. Secret-looking payload values are dropped like everywhere else. At most 200 are listed.
- Unsupported (warn the user): gRPC (v2).

## Ask the user when
No `servers` (base URL), only variable base URLs, no 2xx responses documented, or the target environment
is unclear. Never guess a production URL.
