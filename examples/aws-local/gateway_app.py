"""A small local API that behaves like an API Gateway REST API in front of a Lambda proxy integration (example target, not production code).

Run:  python gateway_app.py --port 8081            (needs: pip install fastapi uvicorn)
It mimics the visible behaviour of the real service, so the generated tests meet the same status codes and error bodies:
- every route lives under a stage prefix (`/prod`) and needs an `x-api-key` header; a missing or wrong key is `403 {"message": "Forbidden"}`
- a route that does not exist answers `403 {"message": "Missing Authentication Token"}`, as the real service does
- a body that fails the request validator is `400 {"message": "Invalid request body"}`
- a usage plan throttles the key: over the rate it answers `429 {"message": "Too Many Requests"}` with a Retry-After header
- a handler that raises is `502 {"message": "Internal server error"}`
Seeded flaws come from the environment variable GATEWAY_BUGS (comma separated): nokey, leak, crash, throttle_off, acceptsbad, idor.
"""
from __future__ import annotations

import argparse
import os
import time
from collections import deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

BUGS = set(filter(None, os.environ.get("GATEWAY_BUGS", "").split(",")))
KEY = os.environ.get("GATEWAY_API_KEY", "local-key-A")
RATE, WINDOW = int(os.environ.get("GATEWAY_RATE", "40")), 1.0
STAGE = "/prod"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
PETS: dict[str, dict] = {"1": {"id": "1", "name": "Rex", "tag": "dog", "owner": "A"}, "2": {"id": "2", "name": "Tom", "tag": "cat", "owner": "B"}}
HITS: deque = deque()
NEXT = [100]


def reply(status: int, body: dict, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers=headers or {})


@app.middleware("http")
async def gateway(request: Request, call_next):
    path = request.url.path
    if not path.startswith(STAGE + "/") and path != STAGE:
        return reply(403, {"message": "Missing Authentication Token"})
    if "nokey" not in BUGS and request.headers.get("x-api-key") != KEY and path != STAGE + "/health":
        return reply(403, {"message": "Forbidden"})
    if "throttle_off" not in BUGS:
        now = time.monotonic()
        HITS.append(now)
        while HITS and now - HITS[0] > WINDOW:
            HITS.popleft()
        if len(HITS) > RATE:
            return reply(429, {"message": "Too Many Requests"}, {"Retry-After": "1"})
    try:
        response = await call_next(request)
    except Exception as exc:                                  # a handler that raises: the gateway hides the detail
        if "leak" in BUGS:
            return reply(502, {"message": "Internal server error", "trace": f"File \"/var/task/app.py\", line 12, in handler: {exc!r}"})
        return reply(502, {"message": "Internal server error"})
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


def invalid(body, required: dict) -> bool:
    if not isinstance(body, dict):
        return True
    for name, kind in required.items():
        if name not in body or not isinstance(body[name], kind):
            return True
    return False


@app.get(STAGE + "/health")
async def health():
    return {"status": "ok"}


@app.get(STAGE + "/pets")
async def list_pets(request: Request):
    raw = request.query_params.get("limit", "20")
    if not raw.isdigit() or not 1 <= int(raw) <= 100:
        return reply(400, {"message": "Invalid request parameters"})
    return list(PETS.values())[: int(raw)]


@app.post(STAGE + "/pets", status_code=201)
async def create_pet(request: Request):
    try:
        body = await request.json()
    except Exception:
        return reply(400, {"message": "Invalid request body"})
    if "acceptsbad" not in BUGS and (invalid(body, {"name": str}) or not body["name"] or len(body["name"]) > 100
                                     or not isinstance(body.get("tag", ""), str)):
        return reply(400, {"message": "Invalid request body"})
    if "crash" in BUGS and "'" in str(body.get("name")):
        raise RuntimeError("sql: syntax error near \"'\"")
    NEXT[0] += 1
    pet = {"id": str(NEXT[0]), "name": body.get("name"), "tag": body.get("tag"), "owner": "A"}
    PETS[pet["id"]] = pet
    return pet


@app.get(STAGE + "/pets/{pet_id}")
async def get_pet(pet_id: str):
    if "crash" in BUGS and "'" in pet_id:
        raise RuntimeError("sql: syntax error")
    pet = PETS.get(pet_id)
    if pet is None:
        return reply(404, {"message": "Pet not found"})
    if pet["owner"] != "A" and "idor" not in BUGS:
        return reply(403, {"message": "Forbidden"})
    return pet


@app.delete(STAGE + "/pets/{pet_id}", status_code=204)
async def delete_pet(pet_id: str):
    if pet_id not in PETS:
        return reply(404, {"message": "Pet not found"})
    del PETS[pet_id]
    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8081)
    uvicorn.run(app, host="127.0.0.1", port=ap.parse_args().port, log_level="warning")
