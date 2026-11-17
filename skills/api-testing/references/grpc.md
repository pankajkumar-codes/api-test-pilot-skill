# gRPC testing (unary calls)

Pipeline: `parse_proto.py` (or `intake.py`, which detects `.proto` files) -> intake JSON -> `gen_pytest.py` -> pytest.
```
python scripts/intake.py shop.proto -o intake.json --summary
python scripts/gen_pytest.py intake.json -o grpc_tests --types all
APITEST_BASE_URL=http://localhost:50051 APITEST_BEARERAUTH_TOKEN=... APITEST_GRPC_ID=1 python -m pytest grpc_tests -v -ra
```
Needs the `grpcurl` command line tool (https://github.com/fullstorydev/grpcurl) at run time, nothing else. Without it every test skips and says so.
Set `APITEST_GRPCURL` to its path when it is not on `PATH`. The parser is offline and stdlib only; it reads proto2 and proto3.

## Target and auth
- `APITEST_BASE_URL=http://host:port` is plaintext gRPC, `https://host:port` is TLS (`APITEST_GRPC_CACERT` for a private CA,
  `APITEST_GRPC_INSECURE=yes` for a self-signed local certificate). The same non-production gate as every other type applies.
- A token in `APITEST_BEARERAUTH_TOKEN` is sent as an `authorization` header. `grpcurl` takes headers as command line arguments, so run
  the suite on a machine you trust. Reports and repro lines show the header redacted.
- `APITEST_GRPC_REQUIRES_AUTH=yes` confirms the service must reject calls without a token (otherwise those tests skip).

## What the parser reads
Services, unary methods, messages (nested, `oneof`, `map`, `repeated`), enums and imports. Imports are followed only below the
folder of the proto you pass (at most 20 files and 2 MB); others are reported, not read. Streaming methods are listed and skipped.
Well-known types (`google.protobuf.*`) are known without their files.

## Tests
- **smoke / positive**: each method with a baseline message built from the proto. Ids of existing objects come from
  `APITEST_GRPC_<METHOD>_<FIELD>` or `APITEST_GRPC_<FIELD>` (e.g. `APITEST_GRPC_ID`), otherwise the test skips and names the variable.
  Methods named `delete*` and friends need `APITEST_ALLOW_DESTRUCTIVE=yes`; writes are skipped on production and read-only runs.
- **negative**: no token and a bad token (`UNAUTHENTICATED` or `PERMISSION_DENIED`), unknown ids rejected with a client-error status.
- **edge**: boundary and unusual values per field; any handled outcome is fine, a server fault (`UNKNOWN`, `INTERNAL`, `DATA_LOSS`, `UNAVAILABLE`) or a timeout is not.
- **contract**: the answer matches the response message (field names, types, enum names, 64-bit integers as strings).
- **security**: reflection left open, status messages that leak stack traces, paths or database errors, injection strings in every string field.
All status codes are shown as in the gRPC spec (`NOT_FOUND`); reports show the HTTP-shaped equivalent next to them.

## Not covered
Streaming methods, `grpcio` based calls, mutual TLS client certificates, and load tests (planned next, through `ghz`).
