"""Local fault-injection proxies for chaos experiments. Stdlib only, offline.

Two proxies, both bound to 127.0.0.1 only (nothing else on the network can reach them):
  * HttpFaultProxy: sits in front of the API under test. Forwards requests and, on command, adds latency, answers
    with an injected error status, drops the connection, resets it, truncates the response or holds it open.
  * TcpFaultProxy: sits between the API and one of ITS dependencies (a database, a cache, a downstream service). The user points
    the dependency's address at the proxy; the proxy then adds latency, blackholes or resets the traffic.

They are libraries first (used by chaos_run.py); `python chaos_proxy.py --help` runs one by hand.
They do not check the safety gate themselves: chaos_run.py does, before it creates one. Standalone use checks it too.

Blast radius is bounded in code: every fault has a hard maximum duration, latency is capped, a blackhole is released after a
bounded time, and the proxy refuses upstreams that fail the gate. Faults apply only to traffic that goes through the proxy.
Nothing is logged except counters; request and response bodies are never recorded.
"""
from __future__ import annotations

import argparse
import http.client
import http.server
import random
import select
import socket
import socketserver
import struct
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import safety_gate  # noqa: E402

HARD_MAX_LATENCY_MS = 10_000
HARD_MAX_FAULT_S = 300
HARD_MAX_HOLD_S = 30
MAX_BODY = 2 * 1024 * 1024
MAX_CONNECTIONS = 64
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "host"}
HTTP_KINDS = {"none", "latency", "error", "abort", "reset", "truncate", "blackhole"}
TCP_KINDS = {"none", "latency", "reset", "blackhole"}


@dataclass(frozen=True)
class Fault:
    kind: str = "none"
    latency_ms: int = 0       # latency: added delay
    status: int = 503         # error: injected status
    pct: int = 100            # share of requests/connections affected (0-100)
    hold_s: float = 10.0      # blackhole: how long a connection is held before it is closed

    def checked(self, allowed: set[str]) -> "Fault":
        if self.kind not in allowed:
            raise ValueError(f"fault kind {self.kind!r} is not supported here; choose from {sorted(allowed)}")
        if not 0 <= self.latency_ms <= HARD_MAX_LATENCY_MS:
            raise ValueError(f"latency_ms must be 0-{HARD_MAX_LATENCY_MS} (hard cap)")
        if not 0 <= self.pct <= 100:
            raise ValueError("pct must be 0-100")
        if not 400 <= self.status <= 599:
            raise ValueError("status must be 4xx or 5xx")
        if not 0 < self.hold_s <= HARD_MAX_HOLD_S:
            raise ValueError(f"hold_s must be 0-{HARD_MAX_HOLD_S} (hard cap)")
        return self


NO_FAULT = Fault()


class _Base:
    kinds: set[str] = set()

    def __init__(self, upstream: str, seed: int = 1) -> None:
        self._fault = NO_FAULT
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._deadline: float | None = None
        self.stats: dict[str, int] = {}
        self._thread: threading.Thread | None = None

    # -- control
    def set_fault(self, fault: Fault, duration_s: float = HARD_MAX_FAULT_S) -> None:
        """Activate a fault; it reverts to pass-through by itself after duration_s (never more than HARD_MAX_FAULT_S)."""
        fault.checked(self.kinds)
        with self._lock:
            self._fault = fault
            self._deadline = time.monotonic() + min(max(duration_s, 0.0), HARD_MAX_FAULT_S) if fault.kind != "none" else None
        self._on_fault_change(fault)

    def clear_fault(self) -> None:
        self.set_fault(NO_FAULT)

    def _on_fault_change(self, fault: Fault) -> None:
        pass

    def _current(self) -> Fault:
        with self._lock:
            if self._deadline is not None and time.monotonic() > self._deadline:
                self._fault, self._deadline = NO_FAULT, None   # the bound expired: back to pass-through
            return self._fault

    def _hit(self, fault: Fault) -> bool:
        if fault.kind == "none":
            return False
        with self._lock:
            return fault.pct >= 100 or self._rng.random() * 100 < fault.pct

    def _count(self, key: str, n: int = 1) -> None:
        with self._lock:
            self.stats[key] = self.stats.get(key, 0) + n

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self.stats)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> "_Base":
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def _check_upstream(url: str, what: str) -> None:
    reason = safety_gate.gate_reason(url)
    if reason:
        raise PermissionError(f"{what}: {reason}")


def _reset(sock: socket.socket) -> None:
    """Close with RST instead of FIN."""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


# --------------------------------------------------------------------------- HTTP
class HttpFaultProxy(_Base):
    kinds = HTTP_KINDS

    def __init__(self, upstream: str, seed: int = 1, timeout: float = 15.0) -> None:
        super().__init__(upstream, seed)
        _check_upstream(upstream, "upstream")
        parts = urllib.parse.urlsplit(upstream)
        self._scheme, self._host = parts.scheme, parts.hostname or ""
        self._uport = parts.port or (443 if parts.scheme == "https" else 80)
        self._prefix = parts.path.rstrip("/")
        self._timeout = timeout
        proxy = self
        slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):   # no request log: paths and headers can carry secrets
                pass

            def handle_one_request(self):
                with slots:
                    super().handle_one_request()

            def _serve(self):
                fault = proxy._current()
                hit = proxy._hit(fault)
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY or self.headers.get("Transfer-Encoding"):
                    self.close_connection = True
                    self.send_error(413 if length > MAX_BODY else 411)
                    return
                body = self.rfile.read(length) if length else None
                proxy._count("requests")
                if hit:
                    proxy._count(f"fault_{fault.kind}")
                    if fault.kind == "latency":
                        time.sleep(fault.latency_ms / 1000)
                    elif fault.kind == "error":
                        payload = b'{"chaos":"injected fault"}'
                        self.send_response(fault.status)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(payload)))
                        self.send_header("X-Chaos-Injected", "1")
                        self.end_headers()
                        self.wfile.write(payload)
                        return
                    elif fault.kind == "abort":
                        self.close_connection = True
                        try:
                            self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        return
                    elif fault.kind == "reset":
                        self.close_connection = True
                        _reset(self.connection)
                        return
                    elif fault.kind == "blackhole":
                        self.close_connection = True
                        time.sleep(fault.hold_s)
                        return
                headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
                conn_cls = http.client.HTTPSConnection if proxy._scheme == "https" else http.client.HTTPConnection
                try:
                    up = conn_cls(proxy._host, proxy._uport, timeout=proxy._timeout)
                    up.request(self.command, proxy._prefix + self.path, body=body, headers=headers)
                    resp = up.getresponse()
                    data = resp.read()
                except (OSError, http.client.HTTPException):
                    proxy._count("upstream_errors")
                    self.send_error(502)
                    return
                finally:
                    try:
                        up.close()
                    except Exception:  # noqa: BLE001
                        pass
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in HOP and k.lower() != "content-length":
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if hit and fault.kind == "truncate":
                    self.wfile.write(data[: len(data) // 2])
                    self.wfile.flush()
                    self.close_connection = True
                    _reset(self.connection)
                    return
                if self.command != "HEAD":
                    self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _serve

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            request_queue_size = MAX_CONNECTIONS

        self._server = Server(("127.0.0.1", 0), Handler)


# --------------------------------------------------------------------------- TCP
class TcpFaultProxy(_Base):
    kinds = TCP_KINDS

    def __init__(self, upstream: str, seed: int = 1, listen_port: int = 0) -> None:
        """upstream is host:port of the dependency, e.g. 127.0.0.1:5432."""
        super().__init__(upstream, seed)
        host, _, port = upstream.rpartition(":")
        if not host or not port.isdigit():
            raise ValueError("upstream must look like host:port")
        _check_upstream(f"http://{host}:{port}", "dependency")
        self._up = (host, int(port))
        self._open: set[socket.socket] = set()
        proxy = self
        slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                client = self.request
                fault = proxy._current()
                if fault.kind == "reset" and proxy._hit(fault):
                    proxy._count("fault_reset")
                    _reset(client)
                    return
                if not slots.acquire(blocking=False):
                    proxy._count("refused_over_cap")
                    client.close()
                    return
                try:
                    try:
                        up = socket.create_connection(proxy._up, timeout=5)
                    except OSError:
                        proxy._count("upstream_errors")
                        client.close()
                        return
                    proxy._count("connections")
                    with proxy._lock:
                        proxy._open.update((client, up))
                    stop = threading.Event()
                    t = threading.Thread(target=proxy._pipe, args=(up, client, stop), daemon=True)
                    t.start()
                    proxy._pipe(client, up, stop)
                    t.join(timeout=1)
                finally:
                    for s in (client, locals().get("up")):
                        if s is not None:
                            with proxy._lock:
                                proxy._open.discard(s)
                            try:
                                s.close()
                            except OSError:
                                pass
                    slots.release()

        class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = True
            allow_reuse_address = True
            request_queue_size = MAX_CONNECTIONS

        self._server = Server(("127.0.0.1", listen_port), Handler)

    def _on_fault_change(self, fault: Fault) -> None:
        if fault.kind == "reset":   # break connections that are already open (pools keep them alive)
            with self._lock:
                socks = list(self._open)
            for s in socks:
                _reset(s)

    def _pipe(self, src: socket.socket, dst: socket.socket, stop: threading.Event) -> None:
        try:
            while not stop.is_set():
                ready, _, _ = select.select([src], [], [], 0.2)
                if not ready:
                    continue
                data = src.recv(65536)
                if not data:
                    break
                fault = self._current()
                hit = self._hit(fault)
                if hit and fault.kind == "blackhole":
                    self._count("fault_blackhole")
                    continue                       # swallow the bytes; the connection stays open
                if hit and fault.kind == "latency":
                    self._count("fault_latency")
                    time.sleep(fault.latency_ms / 1000)
                self._count("bytes", len(data))
                dst.sendall(data)
        except OSError:
            pass
        finally:
            stop.set()
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="chaos_proxy.py", description="Run one fault proxy by hand (127.0.0.1 only).")
    ap.add_argument("mode", choices=["http", "tcp"])
    ap.add_argument("upstream", help="http mode: base URL; tcp mode: host:port")
    ap.add_argument("--listen-port", type=int, default=0)
    ap.add_argument("--fault", default="none", help="one of: " + ", ".join(sorted(HTTP_KINDS)))
    ap.add_argument("--latency-ms", type=int, default=0)
    ap.add_argument("--status", type=int, default=503)
    ap.add_argument("--pct", type=int, default=100)
    ap.add_argument("--duration-s", type=float, default=60, help=f"fault length, capped at {HARD_MAX_FAULT_S}")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    try:
        px = HttpFaultProxy(a.upstream, a.seed) if a.mode == "http" else TcpFaultProxy(a.upstream, a.seed, a.listen_port)
        px.set_fault(Fault(a.fault, a.latency_ms, a.status, a.pct), a.duration_s)
    except (PermissionError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    px.start()
    print(f"{a.mode} fault proxy on 127.0.0.1:{px.port} -> {a.upstream}; fault={a.fault} for at most {min(a.duration_s, HARD_MAX_FAULT_S):.0f}s")
    try:
        time.sleep(min(a.duration_s, HARD_MAX_FAULT_S) + 5)
    except KeyboardInterrupt:
        pass
    px.stop()
    print("stats:", px.snapshot())
    return 0


if __name__ == "__main__":
    sys.exit(_main())
