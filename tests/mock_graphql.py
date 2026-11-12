"""Local mock GraphQL server for validating generated suites (stdlib only; reuses parse_graphql's lexer/SDL parser).

Seedable flaws (`bugs`): introspection nodepth noalias batching fieldauth bola suggest verbose crash
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import parse_graphql as pg

SDL = pg.parse_sdl((Path(__file__).parent / "fixtures" / "shop.graphql").read_text())
TYPES = SDL["types"]
TOKENS = {"tokA": 1, "tokB": 2}
MAX_DEPTH, MAX_ROOT_FIELDS = 10, 20


class GqlError(Exception):
    def __init__(self, msg, status=200):
        super().__init__(msg)
        self.status = status


def parse_document(text: str):
    p = pg.Parser(pg.tokenize(text))
    ops = []

    def selection():
        out = []
        p.expect("punct", "{")
        while not p.accept("punct", "}"):
            name = p.expect("name")
            alias = None
            if p.accept("punct", ":"):
                alias, name = name, p.expect("name")
            args = {}
            if p.accept("punct", "("):
                while not p.accept("punct", ")"):
                    k = p.expect("name")
                    p.expect("punct", ":")
                    args[k] = p.value()
            sub = selection() if p.peek("punct", "{") else None
            out.append({"alias": alias or name, "name": name, "args": args, "sub": sub})
        return out

    while not p.peek("eof"):
        kind, name, vars_ = "query", None, {}
        if p.peek("punct", "{"):
            ops.append({"kind": kind, "name": name, "vars": vars_, "sel": selection()})
            continue
        kind = p.expect("name")
        if p.peek("name"):
            name = p.expect("name")
        if p.accept("punct", "("):
            while not p.accept("punct", ")"):
                p.expect("punct", "$")
                v = p.expect("name")
                p.expect("punct", ":")
                ref = p.type_ref()
                default = p.value() if p.accept("punct", "=") else None
                vars_[v] = {"ref": ref, "default": default}
        ops.append({"kind": kind, "name": name, "vars": vars_, "sel": selection()})
    return ops


def coerce(value, ref, path):
    if value is None:
        if ref.get("non_null"):
            raise GqlError(f'Variable "{path}" of non-null type "{pg.ref_str(ref)}" must not be null.')
        return None
    if "list" in ref:
        items = value if isinstance(value, list) else [value]
        return [coerce(v, ref["list"], path) for v in items]
    name = ref["name"]
    kind = "SCALAR" if name in pg.SCALARS else TYPES.get(name, {}).get("kind")
    ok = {
        "Int": lambda v: isinstance(v, int) and not isinstance(v, bool) and -2**31 <= v < 2**31,
        "Float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "String": lambda v: isinstance(v, str),
        "Boolean": lambda v: isinstance(v, bool),
        "ID": lambda v: isinstance(v, (str, int)) and not isinstance(v, bool),
    }
    if name in ok:
        if not ok[name](value):
            raise GqlError(f'Variable "{path}" got invalid value {json.dumps(value)[:40]}; Expected type "{name}".')
        return value
    if kind == "ENUM":
        if value not in TYPES[name]["values"]:
            raise GqlError(f'Variable "{path}" got invalid value {json.dumps(value)[:40]}; Value is not a valid {name}.')
        return value
    if kind == "INPUT_OBJECT":
        if not isinstance(value, dict):
            raise GqlError(f'Variable "{path}" got invalid value; Expected type "{name}" to be an object.')
        out = {}
        for f in TYPES[name]["fields"]:
            fr = f["ref"]
            if f["name"] in value:
                out[f["name"]] = coerce(value[f["name"]], fr, f"{path}.{f['name']}")
            elif fr.get("non_null") and f.get("default") is None:
                raise GqlError(f'Variable "{path}" got invalid value; Field "{f["name"]}" of required type "{pg.ref_str(fr)}" was not provided.')
            elif f.get("default") is not None:
                out[f["name"]] = f["default"]
        return out
    return value


def depth(sel):
    return 1 + max((depth(s["sub"]) for s in sel if s["sub"]), default=0)


def make_server(bugs=None) -> ThreadingHTTPServer:
    bugs = set(bugs or ())
    pets = {1: {"id": 1, "name": "Rex", "tag": None, "species": "DOG", "owner": 1},
            2: {"id": 2, "name": "Fido", "tag": "vip", "species": "DOG", "owner": 2},
            3: {"id": 3, "name": "Tom", "tag": None, "species": "CAT", "owner": 1}}
    users = {1: {"id": 1, "name": "Ann", "email": "ann@example.test", "passwordHash": "not-a-real-hash"},
             2: {"id": 2, "name": "Bob", "email": "bob@example.test", "passwordHash": "not-a-real-hash"}}
    lock = threading.Lock()
    nxt = [100]

    class Ctx:
        def __init__(self, uid):
            self.uid, self.errors = uid, []

        def err(self, msg, path):
            ex = {"extensions": {"exception": {"stacktrace": ["Error: " + msg, "    at resolve (/app/server.js:10:5)"]}}} if "verbose" in bugs else {}
            self.errors.append({"message": msg, "path": path, **ex})

    def resolve_args(field_def, args, variables, ctx, path):
        out = {}
        for a in field_def["args"]:
            if a["name"] in args:
                v = args[a["name"]]
                if isinstance(v, dict) and "$var" in v:
                    if v["$var"] not in variables:
                        if a["ref"].get("non_null") and a.get("default") is None:
                            raise GqlError(f'Variable "${v["$var"]}" of required type "{pg.ref_str(a["ref"])}" was not provided.')
                        continue
                    v = variables[v["$var"]]
                out[a["name"]] = v
            elif a["ref"].get("non_null") and a.get("default") is None:
                raise GqlError(f'Field "{field_def["name"]}" argument "{a["name"]}" of type "{pg.ref_str(a["ref"])}" is required, but it was not provided.')
        unknown = set(args) - {a["name"] for a in field_def["args"]}
        if unknown:
            raise GqlError(f'Unknown argument "{sorted(unknown)[0]}" on field "{field_def["name"]}".')
        return out

    def field_value(tname, obj, fname, args, ctx, path):
        if tname == "Query":
            if fname == "health":
                return "ok"
            if fname == "me":
                return users.get(ctx.uid)
            if fname == "pet":
                p = pets.get(int(args["id"])) if str(args["id"]).isdigit() else None
                if p and p["owner"] != ctx.uid and "bola" not in bugs:
                    ctx.err("Not found", path)
                    return None
                return p
            if fname == "pets":
                s = args.get("search")
                if "crash" in bugs and s and "'" in s:
                    raise RuntimeError("SQLSTATE[42000]: Syntax error near '" + s + "'")
                items = list(pets.values())
                first = args.get("first")
                if first is not None:
                    if first < 0:
                        raise GqlError("first must be >= 0")
                    items = items[:min(first, 100)]
                return items
        if tname == "Mutation":
            if ctx.uid is None:
                ctx.err("Unauthorized", path)
                return None
            if fname == "createPet":
                inp = args["input"]
                if not 1 <= len(inp["name"]) <= 50:
                    raise GqlError("name must be 1..50 characters")
                with lock:
                    pid = nxt[0]
                    nxt[0] += 1
                    pets[pid] = {"id": pid, "name": inp["name"], "tag": inp.get("tag"), "species": inp.get("species", "DOG"), "owner": ctx.uid}
                return pets[pid]
            if fname == "deletePet":
                p = pets.get(int(args["id"])) if str(args["id"]).isdigit() else None
                if not p or p["owner"] != ctx.uid:
                    ctx.err("Not found", path)
                    return False
                del pets[p["id"]]
                return True
        if tname == "Pet":
            if fname == "owner":
                return users[obj["owner"]]
            if fname == "friends":
                return [p for p in pets.values() if p["owner"] == obj["owner"] and p["id"] != obj["id"]]
            return obj.get(fname)
        if tname == "User":
            if fname in ("email", "passwordHash") and ctx.uid is None and "fieldauth" not in bugs:
                ctx.err("Not authorized to access " + fname, path)
                return None
            return obj.get(fname)
        return None

    def exec_sel(tname, obj, sel, variables, ctx, path):
        out = {}
        for s in sel:
            key, name = s["alias"], s["name"]
            p = path + [key]
            if name == "__typename":
                out[key] = tname
                continue
            if name == "__schema" and tname == "Query":
                if "introspection" not in bugs:
                    ctx.err("GraphQL introspection is not allowed", p)
                    out[key] = None
                else:
                    out[key] = {"queryType": {"name": "Query"}}
                continue
            fd = next((f for f in TYPES[tname]["fields"] if f["name"] == name), None)
            if not fd:
                msg = f'Cannot query field "{name}" on type "{tname}".'
                if "suggest" in bugs:
                    msg += ' Did you mean "name"?'
                raise GqlError(msg)
            args = resolve_args(fd, s["args"], variables, ctx, p)
            val = field_value(tname, obj, name, args, ctx, p)
            named = pg.ref_named(fd["ref"])
            out[key] = project(named, val, s["sub"], variables, ctx, p) if s["sub"] else val
        return out

    def project(tname, val, sub, variables, ctx, path):
        if val is None:
            return None
        if isinstance(val, list):
            return [project(tname, v, sub, variables, ctx, path + [i]) for i, v in enumerate(val)]
        return exec_sel(tname, val, sub, variables, ctx, path)

    def execute(body, uid):
        if not isinstance(body, dict) or not isinstance(body.get("query"), str):
            raise GqlError("Must provide query string.", 400)
        try:
            ops = parse_document(body["query"])
        except Exception as exc:
            raise GqlError("Syntax Error: " + str(exc), 400)
        name = body.get("operationName")
        if name:
            ops = [o for o in ops if o["name"] == name]
            if not ops:
                raise GqlError(f'Unknown operation named "{name}".', 400)
        elif len(ops) != 1:
            raise GqlError("Must provide operation name if query contains multiple operations.", 400)
        op = ops[0]
        variables = body.get("variables") or {}
        if not isinstance(variables, dict):
            raise GqlError("Variables must be an object.", 400)
        coerced = {}
        for v, d in op["vars"].items():
            if v in variables:
                coerced[v] = coerce(variables[v], d["ref"], "$" + v)
            elif d["default"] is not None:
                coerced[v] = d["default"]
            elif d["ref"].get("non_null"):
                raise GqlError(f'Variable "${v}" of required type "{pg.ref_str(d["ref"])}" was not provided.')
        if "nodepth" not in bugs and depth(op["sel"]) > MAX_DEPTH:
            raise GqlError(f"Query depth {depth(op['sel'])} exceeds the maximum of {MAX_DEPTH}")
        if "noalias" not in bugs and len(op["sel"]) > MAX_ROOT_FIELDS:
            raise GqlError(f"Too many root fields ({len(op['sel'])})")
        ctx = Ctx(uid)
        root = "Query" if op["kind"] == "query" else "Mutation"
        data = exec_sel(root, None, op["sel"], coerced, ctx, [])
        out = {"data": data}
        if ctx.errors:
            out["errors"] = ctx.errors
        return out

    class H(BaseHTTPRequestHandler):
        server_version = "mock"
        sys_version = ""

        def log_message(self, *a):
            pass

        def send(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.path.split("?")[0] != "/graphql":
                return self.send(404, {"errors": [{"message": "not found"}]})
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n)
            h = self.headers.get("Authorization", "")
            uid = TOKENS.get(h[7:]) if h.startswith("Bearer ") else None
            try:
                body = json.loads(raw)
            except (ValueError, RecursionError):
                return self.send(400, {"errors": [{"message": "POST body sent invalid JSON."}]})
            try:
                if isinstance(body, list):
                    if "batching" not in bugs:
                        return self.send(400, {"errors": [{"message": "Batching is not supported."}]})
                    return self.send(200, [self.one(b, uid) for b in body])
                return self.send(200, self.one(body, uid))
            except GqlError as e:
                return self.send(e.status, {"errors": [self.err_obj(str(e))]})
            except Exception as e:  # seeded crash: leaks like a careless server
                return self.send(500, {"errors": [{"message": str(e)}]})

        def one(self, body, uid):
            try:
                return execute(body, uid)
            except GqlError as e:
                if e.status == 400:
                    raise
                return {"errors": [self.err_obj(str(e))]}

        @staticmethod
        def err_obj(msg):
            if "verbose" in bugs:
                return {"message": msg, "extensions": {"exception": {"stacktrace": ["Error: " + msg, "    at resolve (/app/server.js:10:5)"]}}}
            return {"message": msg}

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    return srv


class running:
    def __init__(self, bugs=None):
        self.srv = make_server(bugs)

    def __enter__(self) -> str:
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_address[1]}"

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()
