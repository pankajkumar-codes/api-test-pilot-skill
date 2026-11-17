"""A real gRPC server for the shop proto (tests only). Needs grpcio and grpcio-tools; the tests that use it skip when they are missing.

Seeded flaws (a set of names): noauth, crash (internal error on a quote in a string), leak (stack path in an error), acceptsbad
(unknown id and empty name are accepted), reflection (server reflection on), badshape is covered by the stand-in only.
"""
from __future__ import annotations

import contextlib
import importlib
import sys
import tempfile
from concurrent import futures
from pathlib import Path

PROTO_DIR = Path(__file__).parent / "fixtures"
TOKEN = "tokA"


def available() -> bool:
    try:
        import grpc  # noqa: F401
        import grpc_tools  # noqa: F401
        return True
    except ImportError:
        return False


def _compile(out: Path):
    import grpc_tools
    from grpc_tools import protoc
    wkt = Path(grpc_tools.__file__).parent / "_proto"
    code = protoc.main(["protoc", f"-I{PROTO_DIR}", f"-I{wkt}", f"--python_out={out}", f"--grpc_python_out={out}", "shop.proto", "common.proto"])
    if code:
        raise RuntimeError("protoc failed")
    sys.path.insert(0, str(out))
    return importlib.import_module("shop_pb2"), importlib.import_module("shop_pb2_grpc"), importlib.import_module("common_pb2")


@contextlib.contextmanager
def running(bugs: set[str] | None = None):
    import grpc
    bugs = bugs or set()
    with tempfile.TemporaryDirectory() as tmp:
        pb, pbg, common = _compile(Path(tmp))
        pets = {"1": pb.Pet(id="1", name="Rex", tag="dog", species=pb.DOG, vaccinated=True, owner_id=10),
                "2": pb.Pet(id="2", name="Tom", tag="cat", species=pb.CAT, owner_id=11)}

        class Service(pbg.PetServiceServicer):
            def GetPet(self, request, context):
                if "crash" in bugs and "'" in request.id:
                    context.abort(grpc.StatusCode.INTERNAL, "sql: syntax error near \"'\"")
                pet = pets.get(request.id)
                if pet is None:
                    if "acceptsbad" in bugs:
                        return pb.Pet()
                    msg = "lookup failed in /app/server/pets.go:88 goroutine 7" if "leak" in bugs else "pet not found"
                    context.abort(grpc.StatusCode.NOT_FOUND, msg)
                return pet

            def ListPets(self, request, context):
                if request.page_size < 0:
                    context.abort(grpc.StatusCode.INVALID_ARGUMENT, "page_size must be >= 0")
                return pb.ListPetsResponse(pets=list(pets.values())[: request.page_size or 10])

            def CreatePet(self, request, context):
                if "crash" in bugs and "'" in request.name:
                    context.abort(grpc.StatusCode.INTERNAL, "sql: syntax error")
                if (not request.name or len(request.name) > 100) and "acceptsbad" not in bugs:
                    context.abort(grpc.StatusCode.INVALID_ARGUMENT, "name is required and at most 100 characters")
                return pb.Pet(id="99", name=request.name, tag=request.tag)

            def DeletePet(self, request, context):
                if request.id not in pets:
                    context.abort(grpc.StatusCode.NOT_FOUND, "pet not found")
                return common.Ack(ok=True, message="deleted")

        class Auth(grpc.ServerInterceptor):
            def intercept_service(self, continuation, details):
                handler = continuation(details)
                if "noauth" in bugs or handler is None or details.method.startswith("/grpc.reflection"):
                    return handler
                meta = dict(details.invocation_metadata)

                def deny(request, context):
                    context.abort(grpc.StatusCode.UNAUTHENTICATED, "missing or invalid token")
                if meta.get("authorization") not in (f"Bearer {TOKEN}", TOKEN):
                    return grpc.unary_unary_rpc_method_handler(deny)
                return handler

        server = grpc.server(futures.ThreadPoolExecutor(max_workers=4), interceptors=[Auth()])
        pbg.add_PetServiceServicer_to_server(Service(), server)
        if "reflection" in bugs:
            from grpc_reflection.v1alpha import reflection
            reflection.enable_server_reflection(("shop.v1.PetService", reflection.SERVICE_NAME), server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.stop(0).wait()
