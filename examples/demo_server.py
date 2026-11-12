#!/usr/bin/env python3
"""A local, throw-away pets API to try the skill on (stdlib only, binds to 127.0.0.1, no real data).

    python examples/demo_server.py                  # a well-behaved server on port 8080
    python examples/demo_server.py --bugs bola,leak # seed flaws the generated tests should catch
    python examples/demo_server.py --list-bugs

It is the same mock the project's own tests use (tests/mock_rest.py). Valid tokens: tokA (user 1), tokB (user 2).
"""
from __future__ import annotations

import argparse
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
import mock_rest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--bugs", default="", help="comma-separated flaws to seed (see --list-bugs)")
    ap.add_argument("--list-bugs", action="store_true")
    a = ap.parse_args()
    if a.list_bugs:
        print(mock_rest.__doc__)
        return 0
    bugs = {b for b in a.bugs.split(",") if b}
    srv = mock_rest.make_server(bugs, None)
    # make_server picks a free port; rebind on the requested one
    srv.server_close()
    handler = srv.RequestHandlerClass
    srv2 = ThreadingHTTPServer(("127.0.0.1", a.port), handler)
    srv2.state = srv.state  # type: ignore[attr-defined]
    print(f"demo API on http://127.0.0.1:{a.port}  (seeded flaws: {', '.join(sorted(bugs)) or 'none'}; Ctrl-C to stop)", flush=True)
    try:
        srv2.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv2.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
