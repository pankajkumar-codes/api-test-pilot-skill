#!/usr/bin/env python3
"""A stand-in for the grpcurl command line tool, backed by a tiny in-memory pet service (tests only).

It understands the arguments the generated suites pass (-plaintext, -H, -d @, -proto, -import-path, -max-time, -format, host and method)
and answers the way grpcurl does: JSON on stdout with exit 0, or `ERROR:` with Code and Message on stderr with exit 64.
Seeded flaws come from FAKE_GRPC_BUGS (comma separated): noauth, crash, leak, acceptsbad, reflection, badshape, slow.
"""
import json
import os
import sys
import time

BUGS = set(filter(None, os.environ.get("FAKE_GRPC_BUGS", "").split(",")))
TOKEN = "tokA"
PETS = {"1": {"id": "1", "name": "Rex", "tag": "dog", "species": "DOG", "vaccinated": True, "ownerId": "10"},
        "2": {"id": "2", "name": "Tom", "tag": "cat", "species": "CAT", "ownerId": "11"}}


def die(code: str, message: str) -> None:
    sys.stderr.write(f"ERROR:\n  Code: {code}\n  Message: {message}\n")
    sys.exit(64)


def main(argv: list[str]) -> None:
    headers = {}
    positional, i = [], 0
    takes = {"-max-time", "-format", "-import-path", "-proto", "-d", "-H", "-cacert"}
    while i < len(argv):
        a = argv[i]
        if a == "-H":
            k, _, v = argv[i + 1].partition(":")
            headers[k.strip().lower()] = v.strip()
            i += 2
        elif a in takes:
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            positional.append(a)
            i += 1
    if len(positional) < 2:
        die("Unknown", "usage")
    target, method = positional[0], positional[1]
    if target.endswith(":1") or target.endswith(":9"):      # nothing listens here
        sys.stderr.write("Failed to dial target host \"%s\": connection refused\n" % target)
        sys.exit(1)
    if method == "list":
        if "reflection" in BUGS:
            print("shop.v1.PetService")
            return
        die("Unimplemented", "server does not support the reflection API")
    raw = sys.stdin.read()
    try:
        msg = json.loads(raw) if raw.strip() else {}
    except ValueError:
        sys.stderr.write("Error invoking method: failed to query for service descriptor: bad json\n")
        sys.exit(1)
    if "slow" in BUGS:
        time.sleep(float(os.environ.get("FAKE_GRPC_DELAY", "2")))
    if "noauth" not in BUGS:
        token = headers.get("authorization", "")
        if token not in (f"Bearer {TOKEN}", TOKEN):
            die("Unauthenticated", "missing or invalid token")
    name = method.rsplit("/", 1)[-1]
    if not isinstance(msg, dict):
        die("InvalidArgument", "message must be an object")
    if name == "GetPet":
        pid = msg.get("id", "")
        if "crash" in BUGS and "'" in str(pid):
            die("Internal", "sql: syntax error near \"'\"")
        pet = PETS.get(str(pid))
        if pet is None:
            if "acceptsbad" in BUGS:
                print(json.dumps({}))
                return
            if "leak" in BUGS:
                die("NotFound", "lookup failed in /app/server/pets.go:88 goroutine 7")
            die("NotFound", "pet not found")
        out = dict(pet)
        if "badshape" in BUGS:
            out["vaccinated"] = "yes"
        print(json.dumps(out))
    elif name == "ListPets":
        size = msg.get("pageSize", msg.get("page_size", 0))
        if not isinstance(size, int) or size < 0:
            die("InvalidArgument", "page_size must be >= 0")
        print(json.dumps({"pets": list(PETS.values())[: size or 10]}))
    elif name == "CreatePet":
        n = msg.get("name", "")
        if not isinstance(n, str) or not n or len(n) > 100:
            if "acceptsbad" not in BUGS:
                die("InvalidArgument", "name is required and at most 100 characters")
        if "crash" in BUGS and "'" in str(n):
            die("Internal", "sql: syntax error")
        print(json.dumps({"id": "99", "name": n, "tag": msg.get("tag", "")}))
    elif name == "DeletePet":
        if str(msg.get("id", "")) not in PETS:
            die("NotFound", "pet not found")
        print(json.dumps({"ok": True, "message": "deleted"}))
    else:
        die("Unimplemented", f"unknown method {method}")


if __name__ == "__main__":
    main(sys.argv[1:])
