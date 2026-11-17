"""GraphQL integration flows (shipped next to gql_runtime.py). Generated file: regenerate, do not edit.

One flow per object type the schema can create, read and delete: create -> read -> list -> update -> read -> delete -> read (gone).
The flow only touches the object it creates, so it needs no existing ids, and the object is deleted at the end even when a step fails.
Writes are skipped on production targets, like every other write in the suite.
"""
from __future__ import annotations

import copy
from typing import Any

import apitest_runtime as rt
import gql_runtime as gr


def _get(value: Any, path: tuple) -> Any:
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _set(value: dict, path: tuple, new: Any) -> dict:
    out = copy.deepcopy(value)
    cur = out
    for key in path[:-1]:
        if not isinstance(cur.get(key), dict):
            cur[key] = {}
        cur = cur[key]
    cur[path[-1]] = new
    return out


def _vars(gql: Any, op: Any, arg: str | None = None, value: Any = None) -> dict:
    """The operation's baseline variables, with one id argument filled in by the id the flow created."""
    base = gql.resolve(op, variables={k: v for k, v in op.variables.items() if k != arg})
    if arg:
        base[arg] = value
    return base


def _object(resp: Any, op: Any) -> dict | None:
    value = gr.expect_data(resp, op.name)
    return value if isinstance(value, dict) else None


def _create(gql: Any, ops: dict, flow: dict) -> tuple[Any, dict, dict]:
    """Create the flow's object with a run-tagged body; returns (id, values that were sent, the variables used)."""
    create, id_field = ops[flow["create"]], flow["id_field"]
    sent: dict[str, Any] = {}
    base = _vars(gql, create)
    for path in flow.get("tag", []):
        cur = _get(base, tuple(path))
        if isinstance(cur, str):
            base = _set(base, tuple(path), rt._tagged(cur, {}))
            sent[path[-1]] = _get(base, tuple(path))
    resp = gql.post(create.query, base)
    made = _object(resp, create)
    obj_id = made.get(id_field) if made else None
    if obj_id is None:
        gr.fail(resp, f"{create.name} did not return `{id_field}`, so the flow cannot continue", "MEDIUM")
    return obj_id, sent, base


def run_repeated_delete(gql: Any, ops: dict, flow: dict) -> None:
    """Deleting an object twice must not crash the server (the second call may report an error)."""
    rt.skip_writes_on_production()
    delete = ops[flow["delete"]]
    obj_id, _, _ = _create(gql, ops, flow)
    body = _vars(gql, delete, flow["delete_arg"], obj_id)
    gql.post(delete.query, body)
    again = gql.post(delete.query, body)
    gr.expect_no_crash(again, f"second {delete.name} of the same id")


def run_flow(gql: Any, ops: dict, flow: dict) -> None:
    rt.skip_writes_on_production()
    create, read, delete = ops[flow["create"]], ops[flow["read"]], ops[flow["delete"]]
    listing = ops[flow["list"]] if flow.get("list") else None
    update = ops[flow["update"]] if flow.get("update") else None
    id_field = flow["id_field"]
    obj_id, sent, base = _create(gql, ops, flow)
    deleted = False
    try:
        resp = gql.post(read.query, _vars(gql, read, flow["read_arg"], obj_id))
        found = _object(resp, read)
        if found is None or str(found.get(id_field)) != str(obj_id):
            gr.fail(resp, f"{read.name} did not return the object {create.name} just created", "MEDIUM")
        for key, value in sent.items():
            if key in found and found[key] != value:
                gr.fail(resp, f"`{key}` was stored as a different value than the one sent in {create.name}", "MEDIUM")

        if listing:
            resp = gql.post(listing.query, _vars(gql, listing))
            items = gr.expect_data(resp, listing.name)
            if isinstance(items, list) and len(items) < 50 and not any(isinstance(i, dict) and str(i.get(id_field)) == str(obj_id) for i in items):
                gr.fail(resp, f"{listing.name} does not list the object {create.name} just created", "MEDIUM")

        if update and flow.get("update_path"):
            path = tuple(flow["update_path"])
            cur = _get(base, path)
            changed = (f"{cur}-upd" if isinstance(cur, str) else "apitest-updated")[:40]
            body = _set(_vars(gql, update, flow["update_arg"], obj_id), path, changed)
            resp = gql.post(update.query, body)
            gr.expect_data(resp, update.name)
            resp = gql.post(read.query, _vars(gql, read, flow["read_arg"], obj_id))
            found = _object(resp, read)
            if found is not None and path[-1] in found and found[path[-1]] != changed:
                gr.fail(resp, f"`{path[-1]}` still has its old value after {update.name} reported success", "MEDIUM")

        resp = gql.post(delete.query, _vars(gql, delete, flow["delete_arg"], obj_id))
        result = gr.expect_data(resp, delete.name)
        if result is False:
            gr.fail(resp, f"{delete.name} returned false for an object the flow created", "MEDIUM")
        deleted = True
        resp = gql.post(read.query, _vars(gql, read, flow["read_arg"], obj_id))
        if resp.status_code >= 500:
            gr.fail(resp, f"{read.name} crashed on a deleted object (HTTP {resp.status_code})")
        data = (gr.body_of(resp) or {}).get("data") if isinstance(gr.body_of(resp), dict) else None
        if not gr.errors_of(resp) and isinstance(data, dict) and data.get(read.name) is not None:
            gr.fail(resp, f"the object is still readable through {read.name} after {delete.name}", "MEDIUM")
    finally:
        if not deleted:
            try:
                gql.post(delete.query, _vars(gql, delete, flow["delete_arg"], obj_id), transient="skip")
            except BaseException:  # cleanup must not hide the real failure
                pass
