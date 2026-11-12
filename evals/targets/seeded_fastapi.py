"""Eval target 3: a small FastAPI app with seeded bugs (real server, real HTTP, runs locally).

Seeded bugs are switched with the SEEDED_BUGS environment variable (comma separated, or `all` / `none`):

  offbyone  GET /items?limit=N returns N+1 items
  noval     POST /items ignores the documented limits (empty/very long name, negative or huge price)
  status    POST /items answers 200 (spec: 201); DELETE of a missing item answers 200 (spec: 404)
  bola      GET /items/{id} returns items owned by other users (spec: 404 for someone else's item)
  noauth    every route answers without a token (spec: 401)

Run:  SEEDED_BUGS=all python -m uvicorn seeded_fastapi:app --app-dir evals/targets --port 8101

The documented contract lives in `openapi_extra` / `responses` below, so the OpenAPI document the skill reads is
what a developer would publish, while the handlers deviate from it only where a bug is switched on.
Tokens come from the environment (EVAL_TOKEN_A, EVAL_TOKEN_B); they default to throwaway local values.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response

BUGS = {b.strip() for b in os.environ.get("SEEDED_BUGS", "all").split(",") if b.strip()}
if "all" in BUGS:
    BUGS = {"offbyone", "noval", "status", "bola", "noauth"}
if "none" in BUGS:
    BUGS = set()

TOKENS = {os.environ.get("EVAL_TOKEN_A", "eval-token-a"): 1, os.environ.get("EVAL_TOKEN_B", "eval-token-b"): 2}

app = FastAPI(title="Seeded eval API", version="1.0.0", servers=[{"url": "http://127.0.0.1:8101"}])

@app.middleware("http")
async def hardening_headers(request: Request, call_next):
    """Not a seeded bug: the first eval run flagged the missing header on this app, so the clean variant sets it."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


ITEMS: dict[int, dict] = {i: {"id": i, "name": f"item-{i}", "price": 10.0 * i, "owner_id": 1 if i % 2 else 2} for i in range(1, 9)}
NEXT = {"id": 100}

ITEM_SCHEMA = {
    "type": "object",
    "required": ["id", "name", "price"],
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string", "minLength": 1, "maxLength": 40},
        "price": {"type": "number", "minimum": 0, "maximum": 10000},
    },
}
NEW_ITEM_SCHEMA = {
    "type": "object",
    "required": ["name", "price"],
    "properties": {k: v for k, v in ITEM_SCHEMA["properties"].items() if k != "id"},
}
ERROR_SCHEMA = {"type": "object", "properties": {"detail": {"type": "string"}}}
SECURITY = [{"bearerAuth": []}]


def _openapi():
    if not app.openapi_schema:
        doc = get_openapi(title=app.title, version=app.version, routes=app.routes, servers=app.servers)
        doc.setdefault("components", {})["securitySchemes"] = {"bearerAuth": {"type": "http", "scheme": "bearer"}}
        for path, ops in doc["paths"].items():
            for op in ops.values():
                if path != "/health":
                    op["security"] = SECURITY
        app.openapi_schema = doc
    return app.openapi_schema


app.openapi = _openapi


def _json(content_schema):
    return {"application/json": {"schema": content_schema}}


def _responses(ok: dict, *errors: int):
    out = {str(k): v for k, v in ok.items()}
    names = {400: "Invalid input", 401: "Unauthorized", 404: "Not found"}
    for code in errors:
        out[str(code)] = {"description": names[code], "content": _json(ERROR_SCHEMA)}
    return out


def caller(request: Request):
    """Returns (user_id, error_response)."""
    if "noauth" in BUGS:
        return 1, None
    header = request.headers.get("authorization", "")
    uid = TOKENS.get(header[7:]) if header.startswith("Bearer ") else None
    if uid is None:
        return None, JSONResponse({"detail": "unauthorized"}, status_code=401)
    return uid, None


def public(item: dict) -> dict:
    return {k: item[k] for k in ("id", "name", "price")}


@app.get("/health", include_in_schema=True, responses={"200": {"description": "ok"}})
def health():
    return {"status": "ok"}


@app.get(
    "/items",
    operation_id="listItems",
    openapi_extra={"parameters": [{"name": "limit", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}}]},
    responses=_responses({200: {"description": "items", "content": _json({"type": "array", "items": ITEM_SCHEMA})}}, 401),
)
def list_items(request: Request):
    uid, err = caller(request)
    if err:
        return err
    raw = request.query_params.get("limit", "10")
    try:
        limit = int(raw)
    except ValueError:
        return JSONResponse({"detail": "limit must be an integer"}, status_code=400)
    if not 1 <= limit <= 50:
        return JSONResponse({"detail": "limit out of range"}, status_code=400)
    mine = [public(i) for i in sorted(ITEMS.values(), key=lambda x: x["id"]) if i["owner_id"] == uid or "bola" in BUGS]
    return mine[: limit + 1] if "offbyone" in BUGS else mine[:limit]


@app.post(
    "/items",
    operation_id="createItem",
    status_code=201,
    openapi_extra={"requestBody": {"required": True, "content": _json(NEW_ITEM_SCHEMA)}},
    responses=_responses({201: {"description": "created", "content": _json(ITEM_SCHEMA)}}, 400, 401),
)
async def create_item(request: Request):
    uid, err = caller(request)
    if err:
        return err
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"detail": "invalid json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "body must be an object"}, status_code=400)
    name, price = body.get("name"), body.get("price")
    if not isinstance(name, str) or isinstance(price, bool) or not isinstance(price, (int, float)):
        return JSONResponse({"detail": "name (string) and price (number) are required"}, status_code=400)
    if "noval" not in BUGS and (not 1 <= len(name) <= 40 or not 0 <= price <= 10000):
        return JSONResponse({"detail": "name must be 1..40 chars and price 0..10000"}, status_code=400)
    NEXT["id"] += 1
    item = {"id": NEXT["id"], "name": name, "price": price, "owner_id": uid}
    ITEMS[item["id"]] = item
    return JSONResponse(public(item), status_code=200 if "status" in BUGS else 201)


@app.get(
    "/items/{item_id}",
    operation_id="getItem",
    responses=_responses({200: {"description": "item", "content": _json(ITEM_SCHEMA)}}, 401, 404),
)
def get_item(item_id: int, request: Request):
    uid, err = caller(request)
    if err:
        return err
    item = ITEMS.get(item_id)
    if item is None or (item["owner_id"] != uid and "bola" not in BUGS):
        return JSONResponse({"detail": "not found"}, status_code=404)
    return public(item)


@app.delete(
    "/items/{item_id}",
    operation_id="deleteItem",
    status_code=204,
    response_class=Response,
    responses=_responses({204: {"description": "deleted"}}, 401, 404),
)
def delete_item(item_id: int, request: Request):
    uid, err = caller(request)
    if err:
        return err
    item = ITEMS.get(item_id)
    if item is None or (item["owner_id"] != uid and "bola" not in BUGS):
        if "status" in BUGS:
            return JSONResponse({"detail": "ok"}, status_code=200)
        return JSONResponse({"detail": "not found"}, status_code=404)
    del ITEMS[item_id]
    return Response(status_code=204)
