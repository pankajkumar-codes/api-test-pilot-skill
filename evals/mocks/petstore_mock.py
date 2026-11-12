"""Local mock of the Swagger Petstore 3 API (stdlib only), written against the upstream OpenAPI document.

MOCK, NOT THE REAL SERVICE. It implements the paths, parameters, status codes and schemas of
`evals/specs/petstore-openapi.yaml` (vendored from swagger-petstore, Apache-2.0) the way the document says they behave.
The default variant is therefore the reference behaviour ("clean"): any finding on it is a false positive or a tool bug.

Seeded flaws (`bugs`) are deliberate departures from that document, NOT claims about what the real service does:
  noauth   protected routes answer without credentials (spec: 401/403)
  noval    pet bodies without `name`/`photoUrls` and unknown `status` values are accepted (spec: 400/422)
  badid    a non-numeric petId is a 500, an unknown pet is a 200 with an empty body (spec: 400 / 404)
  trace    error bodies contain a stack trace
  crash    a quote in a pet name raises a database error (500)

The server mounts under /api/v3 like the upstream one. Credentials: `api_key` header or `Authorization: Bearer <token>`;
the accepted values come from EVAL_PETSTORE_KEY / EVAL_PETSTORE_TOKEN (throwaway local defaults).
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from _schema import check, project

PREFIX = "/api/v3"
STATUSES = ("available", "pending", "sold")
ORDER_STATUSES = ("placed", "approved", "delivered")
KEY = os.environ.get("EVAL_PETSTORE_KEY", "eval-key")
TOKEN = os.environ.get("EVAL_PETSTORE_TOKEN", "eval-token")


# Schemas copied from the vendored upstream document (components.schemas.Pet, Order, User).
INT, STR = {"type": "integer"}, {"type": "string"}
PET = {"type": "object", "required": ["name", "photoUrls"], "properties": {
    "id": INT, "name": STR, "category": {"type": "object", "properties": {"id": INT, "name": STR}},
    "photoUrls": {"type": "array", "items": STR},
    "tags": {"type": "array", "items": {"type": "object", "properties": {"id": INT, "name": STR}}},
    "status": {"type": "string", "enum": list(STATUSES)}}}
ORDER = {"type": "object", "properties": {
    "id": INT, "petId": INT, "quantity": INT, "shipDate": {"type": "string", "format": "date-time"},
    "status": {"type": "string", "enum": list(ORDER_STATUSES)}, "complete": {"type": "boolean"}}}
USER = {"type": "object", "properties": {k: STR for k in ("username", "firstName", "lastName", "email", "password", "phone")}}
USER["properties"].update({"id": INT, "userStatus": INT})


def pet_error(body) -> str | None:
    return check(PET, body)


def make_server(bugs: set[str] | None = None) -> ThreadingHTTPServer:
    bugs = set(bugs or ())
    lock = threading.Lock()
    pets = {1: {"id": 1, "name": "doggie", "photoUrls": ["http://example.test/1.png"], "status": "available", "tags": []},
            2: {"id": 2, "name": "kitty", "photoUrls": [], "status": "sold", "tags": [{"id": 1, "name": "cat"}]}}
    pets[10] = {"id": 10, "name": "doggie", "photoUrls": [], "status": "available", "tags": []}
    orders = {1: {"id": 1, "petId": 1, "quantity": 1, "status": "placed", "complete": False}}
    orders[10] = {"id": 10, "petId": 10, "quantity": 1, "status": "placed", "complete": False}
    users = {"theUser": {"id": 1, "username": "theUser", "firstName": "John", "lastName": "James", "email": "john@email.com",
                         "phone": "12345", "userStatus": 1}}

    class H(BaseHTTPRequestHandler):
        server_version = "mock-petstore"
        sys_version = ""

        def log_message(self, *a):
            pass

        def reply(self, status, body=None, raw=None):
            data = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())
            self.send_response(status)
            if data:
                self.send_header("Content-Type", "application/json")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def err(self, status, msg):
            if "trace" in bugs:
                msg += "\nTraceback (most recent call last):\n  File \"petstore.py\", line 1, in handle"
            self.reply(status, {"code": status, "type": "error", "message": msg})

        def authorized(self):
            if "noauth" in bugs:
                return True
            auth = self.headers.get("Authorization", "")
            return self.headers.get("api_key") == KEY or auth == f"Bearer {TOKEN}"

        def json_body(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw) if raw else None
            except (ValueError, RecursionError):
                return "__invalid__"

        def route(self):
            parts = urlsplit(self.path)
            if not parts.path.startswith(PREFIX):
                return self.err(404, "not found")
            segs = [unquote(s) for s in parts.path[len(PREFIX):].split("/") if s]
            q = {k: v for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
            m = self.command
            protected = bool(segs) and (segs[0] == "pet" or segs == ["store", "inventory"])
            if protected and not self.authorized():
                return self.err(401, "unauthorized")
            with lock:
                return self.dispatch(m, segs, q)

        def dispatch(self, m, segs, q):
            if segs == ["pet"] and m in ("POST", "PUT"):
                body = self.json_body()
                if body == "__invalid__":
                    return self.err(400, "invalid input")
                if "crash" in bugs and isinstance(body, dict) and "'" in str(body.get("name", "")):
                    raise RuntimeError("SQLSTATE[42000]: syntax error near '" + str(body["name"]) + "'")
                problem = None if "noval" in bugs else pet_error(body)
                if problem:
                    return self.err(400 if m == "POST" else 422, problem)
                if not isinstance(body, dict):
                    return self.err(400, "invalid input")
                pid = body.get("id") if isinstance(body.get("id"), int) else max(pets, default=0) + 1
                if m == "PUT" and pid not in pets:
                    return self.err(404, "Pet not found")
                pets[pid] = {**(body if "noval" in bugs else project(PET, body)), "id": pid}
                return self.reply(200, pets[pid])
            if segs == ["pet", "findByStatus"] and m == "GET":
                if "status" not in q and "noval" not in bugs:
                    return self.err(400, "status is required")
                st = (q.get("status") or ["available"])[0]
                if st not in STATUSES and "noval" not in bugs:
                    return self.err(400, "Invalid status value")
                return self.reply(200, [p for p in pets.values() if p.get("status") == st])
            if segs == ["pet", "findByTags"] and m == "GET":
                tags = {t for v in q.get("tags", []) for t in v.split(",")}
                if not tags:
                    return self.err(400, "Invalid tag value")
                return self.reply(200, [p for p in pets.values() if tags & {t.get("name") for t in p.get("tags", [])}])
            if len(segs) >= 2 and segs[0] == "pet" and (len(segs) == 2 or segs[2:] == ["uploadImage"]):
                try:
                    pid = int(segs[1])
                except ValueError:
                    return self.err(500 if "badid" in bugs else 400, "Invalid ID supplied")
                if segs[2:] == ["uploadImage"]:
                    return self.err(404, "Pet not found") if pid not in pets else self.reply(200, {"code": 200, "type": "ok", "message": "uploaded"})
                if m == "GET":
                    if pid not in pets:
                        return self.reply(200, None) if "badid" in bugs else self.err(404, "Pet not found")
                    return self.reply(200, pets[pid])
                if m == "POST":
                    if pid not in pets:
                        return self.err(400, "Invalid input")
                    if "name" in q:
                        pets[pid]["name"] = q["name"][0]
                    if "status" in q:
                        if q["status"][0] not in STATUSES and "noval" not in bugs:
                            return self.err(400, "Invalid input")
                        pets[pid]["status"] = q["status"][0]
                    return self.reply(200, pets[pid])
                if m == "DELETE":
                    if pid in pets:
                        del pets[pid]
                        return self.reply(200, None)
                return self.err(400, "Invalid pet value")  # the document lists 400 only
            if segs == ["store", "inventory"] and m == "GET":
                counts: dict[str, int] = {}
                for p in pets.values():
                    counts[p.get("status", "available")] = counts.get(p.get("status", "available"), 0) + 1
                return self.reply(200, counts)
            if segs == ["store", "order"] and m == "POST":
                body = self.json_body()
                if check(ORDER, body):
                    return self.err(400, "Invalid input")
                oid = body.get("id") if isinstance(body.get("id"), int) else max(orders, default=0) + 1
                orders[oid] = {**project(ORDER, body), "id": oid}
                return self.reply(200, orders[oid])
            if len(segs) == 3 and segs[:2] == ["store", "order"]:
                try:
                    oid = int(segs[2])
                except ValueError:
                    return self.err(400, "Invalid ID supplied")
                if m == "GET":
                    return self.reply(200, orders[oid]) if oid in orders else self.err(404, "Order not found")
                if m == "DELETE":
                    if oid in orders:
                        del orders[oid]
                        return self.reply(200, None)
                    return self.err(404, "Order not found")
            if segs == ["user"] and m == "POST":
                body = self.json_body()
                if check(USER, body):
                    return self.err(400, "invalid input")
                users[str(body.get("username", ""))] = project(USER, body)
                return self.reply(200, users[str(body.get("username", ""))])
            if segs == ["user", "createWithList"] and m == "POST":
                body = self.json_body()
                if check({"type": "array", "items": USER}, body):
                    return self.err(400, "invalid input")
                for u in body:
                    users[str(u.get("username", ""))] = project(USER, u)
                return self.reply(200, project(USER, body[0]) if body else {})
            if segs == ["user", "login"] and m == "GET":
                return self.reply(200, raw=json.dumps("logged in user session:eval").encode())
            if segs == ["user", "logout"] and m == "GET":
                return self.reply(200, None)
            if len(segs) == 2 and segs[0] == "user":
                name = segs[1]
                if m == "GET":
                    return self.reply(200, users[name]) if name in users else self.err(404, "User not found")
                if m == "PUT":
                    body = self.json_body()
                    if check(USER, body):
                        return self.err(400, "invalid input")
                    if name not in users:
                        return self.err(404, "user not found")
                    users[name] = project(USER, body)
                    return self.reply(200, None)
                if m == "DELETE":
                    if name in users:
                        del users[name]
                        return self.reply(200, None)
                    return self.err(404, "User not found")
            return self.err(404, "not found")

        def handle_one(self):
            try:
                self.route()
            except Exception as exc:  # seeded `crash`: leak the error like a careless server would
                self.reply(500, {"code": 500, "type": "error", "message": str(exc)})

        do_GET = do_POST = do_PUT = do_DELETE = handle_one

    class Server(ThreadingHTTPServer):
        daemon_threads = True

    return Server(("127.0.0.1", 0), H)


class running:
    """`with running({'noval'}) as base_url:` serves in a thread; base_url includes /api/v3."""

    def __init__(self, bugs=None):
        self.srv = make_server(bugs)

    def __enter__(self) -> str:
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_address[1]}{PREFIX}"

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()
