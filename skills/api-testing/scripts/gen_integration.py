"""Generator for integration flows: create -> read -> list -> update -> read -> delete -> read (404).

Resources are derived from path structure (`/pets` + `/pets/{petId}`); nested resources create their
ancestors first. Every flow uses data it creates itself and cleans up after the run, even on failure.
"""
from __future__ import annotations

import re

from gen_common import Module, has_json_body, is_id_param, path_params, py, sample_body, success_schema, supported_body, writable_props


def _item_param(path: str) -> str | None:
    m = re.search(r"/\{([^}/]+)\}/?$", path)
    return m.group(1) if m else None


def _collection_of(path: str) -> str:
    return re.sub(r"/\{[^}/]+\}/?$", "", path)


def build_resources(doc: dict) -> dict[str, dict]:
    """Collection path -> {create, list, item: {read, update, delete}, param, parent}."""
    res: dict[str, dict] = {}
    for ep in doc["endpoints"]:
        if not supported_body(ep):
            continue
        param = _item_param(ep["path"])
        if param:
            coll = _collection_of(ep["path"])
            r = res.setdefault(coll, {"collection": coll})
            r["param"], r["item_path"] = param, ep["path"]
            kind = {"GET": "read", "PUT": "update", "PATCH": "patch", "DELETE": "delete"}.get(ep["method"])
            if kind:
                r.setdefault(kind, ep)
        else:
            r = res.setdefault(ep["path"], {"collection": ep["path"]})
            if ep["method"] == "POST":
                r.setdefault("create", ep)
            elif ep["method"] == "GET":
                r.setdefault("list", ep)
    for coll, r in res.items():
        parent_item = _collection_of(coll) if "{" in coll else None
        parent = None
        if parent_item:
            m = re.match(r"(.*\{[^}/]+\})(/|$)", coll)
            cand = m.group(1) if m else None
            parent = next((p for p in res.values() if p.get("item_path") == cand), None)
        r["parent"] = parent
    return res


def _chain(r: dict) -> list[dict] | None:
    """Ancestors first. None when an ancestor cannot be created."""
    out, cur = [], r
    while cur:
        if not cur.get("create") or not cur.get("param"):
            return None
        out.append(cur)
        cur = cur.get("parent")
        if len(out) > 4:
            return None
    return out[::-1]


def _echo_keys(req_ep: dict, resp_ep: dict | None) -> list[str]:
    req = writable_props((req_ep.get("request_body") or {}).get("schema"))
    schema = None
    if resp_ep:
        for code, spec in sorted(resp_ep.get("responses", {}).items()):
            if str(code).startswith("2") and spec.get("schema"):
                schema = spec["schema"]
                break
    rprops = (schema or {}).get("properties") or {}
    return [k for k, v in req.items() if k in rprops and v.get("type") in ("string", "integer", "number", "boolean")
            and "password" not in k.lower()]


def _updated_body(ep: dict):
    body = sample_body(ep)
    if not isinstance(body, dict):
        return None
    out = dict(body)
    for k, sch in writable_props(ep["request_body"].get("schema")).items():
        if sch.get("type") == "string" and not sch.get("enum") and not sch.get("format") and isinstance(out.get(k), str):
            hi = sch.get("maxLength")
            cand = out[k] + "-updated"
            if hi is None or len(cand) <= hi:
                out[k] = cand
                return out
    return None


def generate(doc: dict, mod: Module) -> list[str]:
    notes: list[str] = []
    for coll, r in sorted(build_resources(doc).items()):
        if not r.get("create") or not r.get("read") or not r.get("param"):
            continue
        item = next((p for p in path_params(r["read"]) if p["name"] == r["param"]), None)
        created = (success_schema(r["create"]) or {}).get("properties") or {}
        if item is not None and r["param"] not in created and not is_id_param(item, r["read"]):
            notes.append(f"integration: no flow for {coll} ('{{{r['param']}}}' is a value the server echoes or computes, "
                         "not the id of a stored resource, so a create-then-read flow would be a false failure)")
            continue
        chain = _chain(r)
        if not chain:
            notes.append(f"integration: no flow for {coll} (an ancestor resource cannot be created through the API)")
            continue
        if not all(has_json_body(c["create"]) or not c["create"].get("request_body") for c in chain):
            notes.append(f"integration: no flow for {coll} (create endpoint is not JSON)")
            continue
        L = [f"def {mod.unique('test_flow_' + coll.strip('/').replace('/', '_').replace('{', '').replace('}', '') + '_lifecycle')}(api, cleanup):",
             "    ids = {}"]
        for i, c in enumerate(chain):
            cep, param = c["create"], c["param"]
            known = {p["name"] for p in path_params(cep)}
            path = "path={k: ids[k] for k in " + py(sorted(known)) + "}" if known else ""
            L += [f"    # create {ascii(c['collection'])[1:-1]}", f"    ep = EP[{cep['id']!r}]",
                  f"    resp = api.send(ep{', ' + path if path else ''})",
                  "    rt.expect_ok(resp, ep)", "    rt.expect_contract(resp, ep)",
                  f"    ids[{param!r}] = rt.extract_id(resp, {param!r})",
                  f"    if ids[{param!r}] is None:",
                  "        pytest.skip(\"create succeeded but the response has no id (no JSON id-like field, no Location header): "
                  "cannot continue this flow; the created data was not removed\")"]
            if c.get("delete"):
                L.append(f"    cleanup.add(lambda p=dict(ids): api.send(EP[{c['delete']['id']!r}], path=p))")
            if i < len(chain) - 1:
                L.append("")
        L += ["    created_body = ep.body", "", "    # read it back", f"    rd = EP[{r['read']['id']!r}]",
              "    resp = api.send(rd, path=dict(ids))", "    rt.expect_ok(resp, rd)", "    rt.expect_contract(resp, rd)"]
        keys = _echo_keys(r["create"], r["read"])
        if keys:
            L.append(f"    rt.expect_echo(resp, created_body, {py(keys)})")
        if r.get("list") and not any(p.get("required") for p in r["list"].get("parameters", []) if p["in"] != "path"):
            lst = r["list"]
            known = {p["name"] for p in path_params(lst)}
            path = ", path={k: ids[k] for k in " + py(sorted(known)) + "}" if known else ""
            L += ["", "    # it appears in the collection", f"    ls = EP[{lst['id']!r}]", f"    resp = api.send(ls{path})",
                  "    rt.expect_ok(resp, ls)", f"    rt.expect_in_list(resp, {r['param']!r}, ids[{r['param']!r}])"]
        upd = r.get("update") or r.get("patch")
        new_body = _updated_body(upd) if upd else None
        if upd and new_body is not None:
            ukeys = _echo_keys(upd, r["read"])
            L += ["", "    # update, then read the change back", f"    up = EP[{upd['id']!r}]",
                  f"    resp = api.send(up, path=dict(ids), body={py(new_body)})", "    rt.expect_ok(resp, up)",
                  "    resp = api.send(rd, path=dict(ids))", "    rt.expect_ok(resp, rd)"]
            if ukeys:
                L.append(f"    rt.expect_echo(resp, {py(new_body)}, {py(ukeys)})")
        if r.get("delete"):
            L += ["", "    # delete, then it must be gone", f"    dl = EP[{r['delete']['id']!r}]",
                  "    resp = api.send(dl, path=dict(ids))", "    rt.expect_ok(resp, dl)",
                  "    resp = api.send(rd, path=dict(ids))",
                  '    rt.expect_rejected(resp, rd, "deleted resource is still readable")']
        mod.add("\n".join(L) + "\n")
    return notes
