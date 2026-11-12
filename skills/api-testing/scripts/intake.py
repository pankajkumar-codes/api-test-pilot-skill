#!/usr/bin/env python3
"""Auto-detect the input type and emit normalized intake JSON.

Usage:  python intake.py <file> [-o intake.json] [--summary] [--type openapi|postman|curl|graphql] [--url GRAPHQL_URL]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parse_curl  # noqa: E402
import parse_graphql  # noqa: E402
import parse_openapi  # noqa: E402
import parse_postman  # noqa: E402
from _common import IntakeError, emit, load_document  # noqa: E402


def detect(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        raise IntakeError(f"File not found: {path}")
    head = p.read_text(encoding="utf-8-sig", errors="replace")[:4000].lstrip()
    if p.suffix.lower() in (".graphql", ".graphqls", ".gql") or re.search(r"^\s*(extend\s+)?(type\s+Query\b|schema\s*\{)", head, re.M):
        return "graphql"
    if head.startswith("curl") or "\ncurl " in head or head.startswith("$ curl"):
        return "curl"
    doc = load_document(path)
    if isinstance(doc, dict):
        if "openapi" in doc or "swagger" in doc:
            return "openapi"
        if "info" in doc and "item" in doc:
            return "postman"
        if parse_graphql.looks_like_introspection(doc):
            return "graphql"
    raise IntakeError("Unrecognized input. Expected OpenAPI/Swagger, a Postman collection, or curl commands.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="intake.py", description=__doc__)
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--type", choices=("openapi", "postman", "curl", "graphql"))
    ap.add_argument("--url", help="GraphQL endpoint URL (only used for GraphQL inputs)")
    args = ap.parse_args(argv)
    try:
        kind = args.type or detect(args.input)
        if kind == "graphql":
            doc = parse_graphql.build(args.input, args.url)
        else:
            builder = {"openapi": parse_openapi.build, "postman": parse_postman.build, "curl": parse_curl.build}[kind]
            doc = builder(args.input)
    except IntakeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if kind == "graphql" and args.summary:
        print(parse_graphql.summarize_graphql(doc))
        if args.output:
            emit(doc, args.output, False)
        return 0
    emit(doc, args.output, args.summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
