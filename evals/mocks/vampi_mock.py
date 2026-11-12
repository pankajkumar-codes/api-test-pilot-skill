"""Local mock of VAmPI (Vulnerable API) built from its published OpenAPI document (stdlib only).

MOCK, NOT THE REAL SERVICE. It follows `evals/specs/vampi-openapi.yaml` (vendored from erev0s/VAmPI) and reproduces 7 of the 9
vulnerability classes the VAmPI README lists (not RegexDoS, not the weak JWT key) plus one extra seeded flaw (`noauthdel`, not
from the README), each behind a switch so a "fixed" variant exists too:

  sqli      GET /users/v1/{username} builds SQL from the path: a quote leaks a database error (500)
  bola      GET /books/v1/{title} returns books (and their secret) owned by other users
  pwchange  PUT /users/v1/{username}/password lets any logged-in user change another user's password
  mass      POST /users/v1/register accepts an `admin` field and stores it
  debug     GET /users/v1/_debug lists users with their passwords (excessive data exposure)
  enum      login answers differently for "unknown user" and "wrong password"
  norate    login and GET /me are never throttled (the fixed variant throttles both)
  noauthdel DELETE /users/v1/{username} works without admin rights

All switches on = `vulnerable` (the VAmPI default); none = `fixed` (the clean variant used to count false positives).
Tokens are opaque strings issued by /users/v1/login. The seeded users (name1, name2, admin) are mock data with throwaway
passwords chosen for this mock; they are not claimed to match what the real /createdb creates.
"""
from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

ALL_BUGS = frozenset({"sqli", "bola", "pwchange", "mass", "debug", "enum", "norate", "noauthdel"})
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SEED_USERS = {"name1": ("pass1", "mail1@mail.com", False), "name2": ("pass2", "mail2@mail.com", False),
              "admin": ("pass1", "admin@mail.com", True)}
SEED_BOOKS = [("bookTitle77", "secret for bookstore", "name1"), ("bookTitle3", "secret for bookstore", "name2")]
LOGIN_WINDOW, LOGIN_LIMIT = 10.0, 5  # fixed variant: 5 login attempts per username per 10 s, then 429
ME_WINDOW, ME_LIMIT = 1.0, 15  # fixed variant: 15 calls to GET /me per account per second, then 429


def make_server(bugs=ALL_BUGS) -> ThreadingHTTPServer:
    bugs = set(bugs)
    lock = threading.Lock()
    users = {n: {"password": p, "email": e, "admin": a} for n, (p, e, a) in SEED_USERS.items()}
    books = {t: {"secret": s, "owner": o} for t, s, o in SEED_BOOKS}
    tokens: dict[str, str] = {}
    attempts: dict[str, list[float]] = {}

    class H(BaseHTTPRequestHandler):
        server_version = "mock-vampi"
        sys_version = ""

        def log_message(self, *a):
            pass

        def reply(self, status, body=None, headers=None):
            data = b"" if body is None else json.dumps(body).encode()
            self.send_response(status)
            if data:
                self.send_header("Content-Type", "application/json")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def fail(self, status, message):
            self.reply(status, {"status": "fail", "message": message})

        def caller(self):
            h = self.headers.get("Authorization", "")
            who = tokens.get(h[7:]) if h.startswith("Bearer ") else None
            return who if who in users else None  # a deleted user's token stops working

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                value = json.loads(self.rfile.read(n)) if n else None
            except (ValueError, RecursionError):
                return None
            return value if isinstance(value, dict) else None

        def route(self):
            segs = [unquote(s) for s in urlsplit(self.path).path.split("/") if s]
            m = self.command
            with lock:
                return self.dispatch(m, segs)

        def dispatch(self, m, segs):
            if segs == [] and m == "GET":
                return self.reply(200, {"message": "VAmPI mock", "help": "see openapi", "vulnerable": 1 if bugs else 0})
            if segs == ["createdb"] and m == "GET":
                return self.reply(200, {"message": "Database populated."})
            if segs == ["users", "v1"] and m == "GET":
                return self.reply(200, {"users": [{"username": n, "email": u["email"]} for n, u in users.items()]})
            if segs == ["users", "v1", "_debug"] and m == "GET":
                if "debug" in bugs:
                    return self.reply(200, {"users": [{"username": n, "email": u["email"], "password": u["password"],
                                                       "admin": u["admin"]} for n, u in users.items()]})
                return self.reply(200, {"users": []})
            if segs == ["users", "v1", "register"] and m == "POST":
                b = self.body()
                if not b or not all(isinstance(b.get(k), str) and b[k] for k in ("username", "password", "email")):
                    return self.fail(400, "username, password and email are required")
                if not EMAIL.match(b["email"]):
                    return self.fail(400, "Please Provide a valid email address.")
                if b["username"] in users:
                    return self.fail(400, "User already exists. Please Log in.")
                users[b["username"]] = {"password": b["password"], "email": b["email"],
                                        "admin": bool(b.get("admin")) if "mass" in bugs else False}
                return self.reply(200, {"message": "Successfully registered. Login to receive an auth token.", "status": "success"})
            if segs == ["users", "v1", "login"] and m == "POST":
                b = self.body()
                if not b or not isinstance(b.get("username"), str) or not isinstance(b.get("password"), str):
                    return self.fail(400, "username and password are required")
                if "norate" not in bugs:
                    now = time.monotonic()
                    hist = [t for t in attempts.get(b["username"], []) if now - t < LOGIN_WINDOW]
                    hist.append(now)
                    attempts[b["username"]] = hist
                    if len(hist) > LOGIN_LIMIT:
                        return self.reply(429, {"status": "fail", "message": "too many attempts"}, {"Retry-After": "10"})
                u = users.get(b["username"])
                if u is None:
                    return self.fail(400, "Username does not exist" if "enum" in bugs else "Username or Password Incorrect!")
                if u["password"] != b["password"]:
                    return self.fail(400, "Password is not correct for the given username." if "enum" in bugs
                                     else "Username or Password Incorrect!")
                tok = f"tok-{b['username']}-{len(tokens)}"
                tokens[tok] = b["username"]
                return self.reply(200, {"auth_token": tok, "message": "Successfully logged in.", "status": "success"})
            who = self.caller()
            if segs == ["me"] and m == "GET":
                if not who:
                    return self.fail(401, "Invalid token. Please log in again.")
                if "norate" not in bugs:  # fixed variant: a per-account limit on this endpoint
                    now = time.monotonic()
                    hist = [t for t in attempts.get("me:" + who, []) if now - t < ME_WINDOW]
                    hist.append(now)
                    attempts["me:" + who] = hist
                    if len(hist) > ME_LIMIT:
                        return self.reply(429, {"status": "fail", "message": "too many requests"}, {"Retry-After": "1"})
                return self.reply(200, {"data": {"username": who, "email": users[who]["email"], "admin": users[who]["admin"]},
                                        "status": "success"})
            if len(segs) == 3 and segs[:2] == ["users", "v1"] and m == "GET":
                name = segs[2]
                if "sqli" in bugs and "'" in name:
                    return self.reply(500, {"status": "fail", "message": "sqlite3.OperationalError: unrecognized token: \"'" + name + "'\""})
                if name not in users:
                    return self.fail(404, "User not found")
                return self.reply(200, {"username": name, "email": users[name]["email"]})
            if len(segs) == 3 and segs[:2] == ["users", "v1"] and m == "DELETE":
                if not who:
                    return self.fail(401, "Invalid token. Please log in again.")
                if not users[who]["admin"] and "noauthdel" not in bugs:
                    return self.fail(401, "Only Admins may delete users!")
                if segs[2] not in users:
                    return self.fail(404, "User not found!")
                del users[segs[2]]
                return self.reply(200, {"message": "User deleted.", "status": "success"})
            if len(segs) == 4 and segs[:2] == ["users", "v1"] and segs[3] in ("email", "password") and m == "PUT":
                if not who:
                    return self.fail(401, "Invalid token. Please log in again.")
                b = self.body()
                field = segs[3]
                if not b or not isinstance(b.get(field), str) or not b[field]:
                    return self.fail(400, f"{field} is required")
                if field == "email" and not EMAIL.match(b["email"]):
                    return self.fail(400, "Please Provide a valid email address.")
                target = segs[2]
                if target not in users:
                    return self.fail(400, "User not found")
                if field == "password" and target != who and "pwchange" not in bugs:
                    return self.fail(401, "You may only change your own password")
                if field == "email" and target != who:
                    return self.fail(401, "You may only change your own email")
                users[target][field] = b[field]
                return self.reply(204)
            if segs == ["books", "v1"] and m == "GET":
                return self.reply(200, {"Books": [{"book_title": t, "user": b["owner"]} for t, b in books.items()]})
            if segs == ["books", "v1"] and m == "POST":
                if not who:
                    return self.fail(401, "Invalid token. Please log in again.")
                b = self.body()
                if not b or not isinstance(b.get("book_title"), str) or not isinstance(b.get("secret"), str):
                    return self.fail(400, "book_title and secret are required")
                if b["book_title"] in books:
                    return self.fail(400, "Book Already exists!")
                books[b["book_title"]] = {"secret": b["secret"], "owner": who}
                return self.reply(200, {"message": "Book has been added.", "status": "success"})
            if len(segs) == 3 and segs[:2] == ["books", "v1"] and m == "GET":
                if not who:
                    return self.fail(401, "Invalid token. Please log in again.")
                bk = books.get(segs[2])
                if bk is None or (bk["owner"] != who and "bola" not in bugs):
                    return self.fail(404, "Book not found!")
                # the vendored document declares an array of {book_title, owner, secret}; the mock follows the document
                return self.reply(200, [{"book_title": segs[2], "secret": bk["secret"], "owner": bk["owner"]}])
            return self.fail(404, "not found")

        do_GET = do_POST = do_PUT = do_DELETE = route

    class Server(ThreadingHTTPServer):
        daemon_threads = True

    return Server(("127.0.0.1", 0), H)


class running:
    def __init__(self, bugs=ALL_BUGS):
        self.srv = make_server(bugs)

    def __enter__(self) -> str:
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_address[1]}"

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()
