#!/usr/bin/env python3
"""Save a GraphQL endpoint's introspection result as JSON for `intake.py` (stdlib only).

    python evals/dump_introspection.py http://localhost:5013/graphql -o dvga-introspection.json

Sends the standard introspection query once (a read). Credentials, if the endpoint needs them, come from the
environment: set APITEST_GQL_AUTH_HEADER to the full header value, e.g. "Bearer <token>"; it is never printed or saved.
Run it only against an endpoint you are allowed to query.
"""
import argparse
import json
import os
import sys
import urllib.request

QUERY = """query IntrospectionQuery { __schema { queryType { name } mutationType { name } subscriptionType { name }
 types { ...FullType } directives { name description locations args { ...InputValue } } } }
fragment FullType on __Type { kind name description fields(includeDeprecated: true) { name description args { ...InputValue }
 type { ...TypeRef } isDeprecated deprecationReason } inputFields { ...InputValue } interfaces { ...TypeRef }
 enumValues(includeDeprecated: true) { name description isDeprecated deprecationReason } possibleTypes { ...TypeRef } }
fragment InputValue on __InputValue { name description type { ...TypeRef } defaultValue }
fragment TypeRef on __Type { kind name ofType { kind name ofType { kind name ofType { kind name ofType { kind name ofType { kind name
 ofType { kind name ofType { kind name } } } } } } } }"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args()
    if not a.url.startswith(("http://", "https://")):
        ap.error("url must be http(s)")
    headers = {"Content-Type": "application/json"}
    if os.environ.get("APITEST_GQL_AUTH_HEADER"):
        headers["Authorization"] = os.environ["APITEST_GQL_AUTH_HEADER"]
    req = urllib.request.Request(a.url, data=json.dumps({"query": QUERY}).encode(), headers=headers)
    try:
        body = json.load(urllib.request.urlopen(req, timeout=30))
    except Exception as exc:  # network, HTTP or JSON problem: say which, without echoing headers
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(body, dict) or "data" not in body or not (body["data"] or {}).get("__schema"):
        print("error: no __schema in the answer (introspection may be disabled: save the SDL instead)", file=sys.stderr)
        return 1
    with open(a.output, "w", encoding="utf-8") as fh:
        json.dump(body, fh)
    print(f"wrote {a.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
