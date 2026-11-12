"""Tiny JSON-schema subset for the eval mocks (stdlib only): type, required, enum, items, properties, date-time.

Used so the *reference* ("clean") mocks behave like a careful implementation of the documented schemas:
`check` rejects wrong types and missing required fields, `project` drops properties the schema does not declare.
"""
from __future__ import annotations

import re

DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")


def check(schema: dict, value, path: str = "body") -> str | None:
    t = schema.get("type")
    if t == "object":
        if not isinstance(value, dict):
            return f"{path} must be an object"
        for req in schema.get("required", []):
            if req not in value:
                return f"{path}.{req} is required"
        for key, sub in (schema.get("properties") or {}).items():
            if key in value and value[key] is not None:
                err = check(sub, value[key], f"{path}.{key}")
                if err:
                    return err
    elif t == "array":
        if not isinstance(value, list):
            return f"{path} must be an array"
        for i, item in enumerate(value):
            err = check(schema.get("items") or {}, item, f"{path}[{i}]")
            if err:
                return err
    elif t == "string":
        if not isinstance(value, str):
            return f"{path} must be a string"
        if schema.get("format") == "date-time" and not DATE_TIME.match(value):
            return f"{path} must be an RFC 3339 date-time"
    elif t == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{path} must be an integer"
    elif t == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{path} must be a number"
    elif t == "boolean":
        if not isinstance(value, bool):
            return f"{path} must be a boolean"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} must be one of {schema['enum']}"
    return None


def project(schema: dict, value):
    """Keep only declared properties (objects, recursively); everything else is ignored like a strict DTO would."""
    if schema.get("type") == "object" and isinstance(value, dict):
        props = schema.get("properties") or {}
        return {k: project(props[k], v) for k, v in value.items() if k in props}
    if schema.get("type") == "array" and isinstance(value, list):
        return [project(schema.get("items") or {}, v) for v in value]
    return value
