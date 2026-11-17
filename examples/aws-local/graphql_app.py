"""A small local GraphQL API in the style of a managed GraphQL service with API-key auth (example target, not production code).

Run:  python graphql_app.py --port 8082            (needs: pip install fastapi uvicorn strawberry-graphql)
Every request needs an `x-api-key` header (a missing or wrong key is HTTP 401 with an `errors` entry). Introspection is off, alias counts are limited and "did you mean" hints are masked, as a hardened service would do.
Seeded flaws come from GRAPHQL_BUGS (comma separated): nokey, noconstraint, crash, introspection, noalias, suggest.
"""
from __future__ import annotations

import argparse
import os

import strawberry
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from strawberry.fastapi import GraphQLRouter

BUGS = set(filter(None, os.environ.get("GRAPHQL_BUGS", "").split(",")))
KEY = os.environ.get("GRAPHQL_API_KEY", "local-key-A")


@strawberry.type
class Pet:
    id: strawberry.ID
    name: str
    tag: str | None = None


@strawberry.input
class NewPet:
    name: str
    tag: str | None = None


STORE: dict[str, Pet] = {"1": Pet(id=strawberry.ID("1"), name="Rex", tag="dog"), "2": Pet(id=strawberry.ID("2"), name="Tom", tag="cat")}
NEXT = [100]


def _check_name(name: str) -> None:
    if "crash" in BUGS and "'" in name:
        raise RuntimeError("sql: syntax error near \"'\"")
    if "noconstraint" not in BUGS and (not name or len(name) > 100):
        raise ValueError("name must be 1 to 100 characters")


@strawberry.type
class Query:
    @strawberry.field
    def pet(self, id: strawberry.ID) -> Pet | None:
        if "crash" in BUGS and "'" in str(id):
            raise RuntimeError("sql: syntax error")
        return STORE.get(str(id))

    @strawberry.field
    def pets(self, first: int = 20) -> list[Pet]:
        if first < 0 or first > 100:
            raise ValueError("first must be between 0 and 100")
        return list(STORE.values())[:first]

    @strawberry.field
    def health(self) -> str:
        return "ok"


@strawberry.type
class Mutation:
    @strawberry.mutation
    def create_pet(self, input: NewPet) -> Pet:
        _check_name(input.name)
        NEXT[0] += 1
        pet = Pet(id=strawberry.ID(str(NEXT[0])), name=input.name, tag=input.tag)
        STORE[str(pet.id)] = pet
        return pet

    @strawberry.mutation
    def delete_pet(self, id: strawberry.ID) -> bool:
        return STORE.pop(str(id), None) is not None


def _extensions() -> list:
    from strawberry.extensions import DisableIntrospection, MaskErrors, MaxAliasesLimiter
    out: list = []
    if "introspection" not in BUGS:
        out.append(DisableIntrospection)
    if "noalias" not in BUGS:
        out.append(lambda: MaxAliasesLimiter(max_alias_count=15))
    if "suggest" not in BUGS:
        out.append(lambda: MaskErrors(should_mask_error=lambda e: "Did you mean" in str(e), error_message="Cannot query this field"))
    return out


schema = strawberry.Schema(Query, Mutation, extensions=_extensions())
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def api_key(request: Request, call_next):
    if "nokey" not in BUGS and request.headers.get("x-api-key") != KEY:
        return JSONResponse({"errors": [{"message": "Unauthorized"}]}, status_code=401)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


app.include_router(GraphQLRouter(schema, graphql_ide=None), prefix="/graphql")

if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8082)
    uvicorn.run(app, host="127.0.0.1", port=ap.parse_args().port, log_level="warning")
