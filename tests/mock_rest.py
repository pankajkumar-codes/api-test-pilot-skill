"""Local mock REST server for validating generated pytest suites (stdlib only).

`bugs` seeds specific flaws so tests can prove the generated suite detects them:
  bola noauth leak contract mass noval crash stacktrace admin headers cors ssrf paging noretry
  down (every request answers 500)  jsoncrash (malformed JSON -> 500 + traceback)  hang (template-looking names stall 3 s)  crlf (name reflected into a header)
`rate_limit=N` throttles GET /pets to N requests per second (429 + Retry-After).
"""
from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

TOKENS = {"tokA": 1, "tokB": 2, "tokAdmin": 99}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class State:
    def __init__(self, bugs: set[str], rate_limit: int | None):
        self.bugs, self.rate_limit = bugs, rate_limit
        self.lock = threading.Lock()
        self.next_id = 100
        self.pets = {1: self.pet(1, "Rex", 1), 2: self.pet(2, "Fido", 2), 3: self.pet(3, "Tom", 1), 4: self.pet(4, "Kit", 1)}
        self.toys = {7: {"id": 7, "label": "ball", "pet_id": 1}, 8: {"id": 8, "label": "bone", "pet_id": 2}}
        self.hits: list[float] = []
        self.log: list[tuple[str, str]] = []

    @staticmethod
    def pet(pid, name, owner):
        return {"id": pid, "name": name, "tag": None, "age": 3, "contact": "o@example.com", "owner_id": owner}


def make_server(bugs: set[str] | None = None, rate_limit: int | None = None) -> ThreadingHTTPServer:
    state = State(set(bugs or ()), rate_limit)

    class H(BaseHTTPRequestHandler):
        server_version = "mock"
        sys_version = ""

        def log_message(self, *a):
            pass

        def reply(self, status, body=None, headers=None):
            raw = b"" if body is None else json.dumps(body).encode()
            self.send_response(status)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            if "headers" not in state.bugs:
                self.send_header("X-Content-Type-Options", "nosniff")
            else:
                self.send_header("X-Powered-By", "Express 4.17")
            if "cors" in state.bugs and self.headers.get("Origin"):
                self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
                self.send_header("Access-Control-Allow-Credentials", "true")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def err(self, status, msg, extra=None):
            if "stacktrace" in state.bugs and status == 400:
                msg += "\nTraceback (most recent call last):\n  File \"app.py\", line 1, in handler"
            self.reply(status, {"code": status, "message": msg}, extra)

        def user(self, need=True):
            if "noauth" in state.bugs:
                return 1
            h = self.headers.get("Authorization", "")
            tok = h[7:] if h.startswith("Bearer ") else None
            uid = TOKENS.get(tok)
            if uid is None and need:
                self.err(401, "unauthorized")
            return uid

        def body_json(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if "json" not in (self.headers.get("Content-Type") or ""):
                self.err(415, "content type must be application/json")
                return None
            try:
                value = json.loads(raw)
                if value is None:  # found by the fuzzer: a bare `null` body used to leave the connection hanging
                    self.err(400, "body must not be null")
                return value
            except (ValueError, RecursionError):
                if "jsoncrash" in state.bugs:
                    raise RuntimeError("Traceback (most recent call last):\n  File \"app.py\", line 9, in parse")
                self.err(400, "invalid json")
                return None

        def validate_pet(self, b):
            if not isinstance(b, dict):
                return "body must be an object"
            name = b.get("name")
            if not isinstance(name, str):
                return "name is required"
            if "hang" in state.bugs and "{{" in name:
                time.sleep(3)
            if "crash" in state.bugs and "'" in name:
                raise RuntimeError("SQLSTATE[42000]: Syntax error or access violation near '" + name + "'")
            if "noval" in state.bugs:
                return None
            if not 1 <= len(name) <= 50:
                return "name length must be 1..50"
            tag = b.get("tag")
            if tag is not None and (not isinstance(tag, str) or len(tag) > 20):
                return "invalid tag"
            age = b.get("age")
            if age is not None and (isinstance(age, bool) or not isinstance(age, int) or not 0 <= age <= 30):
                return "invalid age"
            c = b.get("contact")
            if c is not None and (not isinstance(c, str) or not EMAIL.match(c)):
                return "invalid contact"
            return None

        def render_pet(self, p):
            out = {k: v for k, v in p.items() if v is not None or k == "tag"}
            if "leak" in state.bugs:
                out["password_hash"] = "not-a-real-hash"
            if "contract" in state.bugs:
                out["id"] = str(out["id"])
            return out

        def route(self):
            parts = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
            segs = [s for s in parts.path.split("/") if s]
            m = self.command
            if "down" in state.bugs:
                return self.err(500, "boom")
            if segs == ["health"] and m == "GET":
                return self.reply(200, {"status": "ok"})
            uid = self.user()
            if uid is None:
                return
            if segs == ["pets"]:
                if m == "GET":
                    return self.list_pets(uid, q)
                if m == "POST":
                    b = self.body_json()
                    if b is None:
                        return
                    msg = self.validate_pet(b)
                    if msg:
                        return self.err(400, msg)
                    with state.lock:
                        pid = state.next_id
                        state.next_id += 1
                        pet = {"id": pid, "name": b["name"], "tag": b.get("tag"), "age": b.get("age"),
                               "contact": b.get("contact"), "owner_id": uid}
                        if "mass" in state.bugs:
                            pet["owner_id"] = b.get("owner_id", uid)
                            pet["is_admin"] = b.get("is_admin")
                        state.pets[pid] = pet
                    extra = {"Location": f"/pets/{pid}"}
                    if "crlf" in state.bugs:
                        extra["X-Echo"] = b["name"]  # raw reflection: CR/LF in the name splits the header block
                    return self.reply(201, self.render_pet(pet), extra)
            if len(segs) >= 2 and segs[0] == "pets":
                try:
                    pid = int(segs[1])
                except ValueError:
                    return self.err(400, "petId must be an integer")
                pet = state.pets.get(pid)
                visible = pet and (pet["owner_id"] == uid or "bola" in state.bugs)
                if len(segs) == 2:
                    if not visible:
                        return self.err(404, "pet not found")
                    if m == "GET":
                        return self.reply(200, self.render_pet(pet))
                    if m == "PUT":
                        b = self.body_json()
                        if b is None:
                            return
                        msg = self.validate_pet(b)
                        if msg:
                            return self.err(400, msg)
                        pet.update({"name": b["name"], "tag": b.get("tag"), "age": b.get("age"), "contact": b.get("contact")})
                        return self.reply(200, self.render_pet(pet))
                    if m == "DELETE":
                        del state.pets[pid]
                        return self.reply(204)
                if len(segs) >= 3 and segs[2] == "toys":
                    if not visible:
                        return self.err(404, "pet not found")
                    if len(segs) == 3 and m == "GET":
                        return self.reply(200, [t for t in state.toys.values() if t["pet_id"] == pid])
                    if len(segs) == 3 and m == "POST":
                        b = self.body_json()
                        if b is None:
                            return
                        lab = b.get("label") if isinstance(b, dict) else None
                        if not isinstance(lab, str) or not 1 <= len(lab) <= 30:
                            return self.err(400, "label must be 1..30 characters")
                        with state.lock:
                            tid = state.next_id
                            state.next_id += 1
                            state.toys[tid] = {"id": tid, "label": lab, "pet_id": pid}
                        return self.reply(201, state.toys[tid])
                    if len(segs) == 4:
                        try:
                            tid = int(segs[3])
                        except ValueError:
                            return self.err(400, "toyId must be an integer")
                        toy = state.toys.get(tid)
                        if not toy or toy["pet_id"] != pid:
                            return self.err(404, "toy not found")
                        if m == "GET":
                            return self.reply(200, toy)
                        if m == "DELETE":
                            del state.toys[tid]
                            return self.reply(204)
            if segs == ["admin", "stats"] and m == "GET":
                if uid != 99 and "admin" not in state.bugs:
                    return self.err(403, "admin only")
                return self.reply(200, {"pets": len(state.pets)})
            if segs == ["preview"] and m == "GET":
                url = q.get("url", "")
                host = urlsplit(url).hostname or ""
                internal = host in ("localhost", "::1") or host.startswith(("127.", "169.254.", "10.", "192.168."))
                if "ssrf" not in state.bugs and (urlsplit(url).scheme not in ("http", "https") or internal):
                    return self.err(400, "url not allowed")
                return self.reply(200, {"preview": f"fetched {url}"})
            return self.err(404, "no such route")

        def list_pets(self, uid, q):
            if state.rate_limit:
                now = time.time()
                with state.lock:
                    state.hits = [t for t in state.hits if now - t < 1.0]
                    state.hits.append(now)
                    over = len(state.hits) > state.rate_limit
                if over:
                    return self.err(429, "slow down", None if "noretry" in state.bugs else {"Retry-After": "1"})
            try:
                limit = int(q["limit"]) if "limit" in q else 20
                offset = int(q["offset"]) if "offset" in q else 0
            except ValueError:
                return self.err(400, "limit/offset must be integers")
            if (not 1 <= limit <= 100 or offset < 0) and "noval" not in state.bugs:
                return self.err(400, "limit must be 1..100 and offset >= 0")
            if q.get("status") not in (None, "available", "sold") and "noval" not in state.bugs:
                return self.err(400, "status must be available or sold")
            items = [self.render_pet(p) for p in state.pets.values() if p["owner_id"] == uid]
            if "paging" not in state.bugs:
                items = items[offset:offset + limit]
            return self.reply(200, items)

        def dispatch(self):
            state.log.append((self.command, self.path))
            try:
                self.route()
            except Exception as exc:  # seeded crash bug: leak the error like a careless server would
                self.reply(500, {"code": 500, "message": str(exc)})

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = dispatch

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.handle_error = lambda *a: None  # clients that time out or hang up are expected in fuzz/load tests
    srv.state = state  # type: ignore[attr-defined]
    return srv


LAST_LOG: list = []


class running:
    """Context manager: start the server on a free port, yield its base URL."""

    def __init__(self, bugs=None, rate_limit=None):
        self.srv = make_server(bugs, rate_limit)

    def __enter__(self) -> str:
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_address[1]}"

    def __exit__(self, *a):
        LAST_LOG[:] = self.srv.state.log  # type: ignore[attr-defined]
        self.srv.shutdown()
        self.srv.server_close()
