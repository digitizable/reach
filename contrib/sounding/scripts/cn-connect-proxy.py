#!/usr/bin/env python3
"""
Local HTTP proxy that chains through an upstream HTTP CONNECT peer.

Research residual (G3-class): Mullvad → this → open/paid CN CONNECT → target.

  python3 cn-connect-proxy.py --upstream 114.236.137.41:21000 --listen 127.0.0.1:18080
  curl -x http://127.0.0.1:18080 https://www.cnki.net/ -o /dev/null -w '%{http_code}\\n'

Optional: --upstream-file cn-connect-live.json (rotates on failure among list).
"""

from __future__ import annotations

import argparse
import json
import select
import socket
import threading
from pathlib import Path


def relay(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], 120)
            if not r:
                break
            for s in r:
                other = b if s is a else a
                data = s.recv(65536)
                if not data:
                    return
                other.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


class UpstreamPool:
    def __init__(self, peers: list[str]) -> None:
        if not peers:
            raise SystemExit("no upstream peers")
        self.peers = peers
        self._i = 0
        self._lock = threading.Lock()

    def next(self) -> str:
        with self._lock:
            p = self.peers[self._i % len(self.peers)]
            self._i += 1
            return p

    def prefer(self, peer: str) -> None:
        with self._lock:
            if peer in self.peers:
                self.peers.remove(peer)
                self.peers.insert(0, peer)


def load_peers(upstream: str | None, path: str | None) -> list[str]:
    peers: list[str] = []
    if upstream:
        peers.append(upstream.strip())
    if path:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, list):
            for row in data:
                if isinstance(row, str):
                    peers.append(row)
                elif isinstance(row, dict) and row.get("proxy"):
                    peers.append(str(row["proxy"]))
        elif isinstance(data, dict) and "winners" in data:
            for row in data["winners"]:
                if row.get("proxy"):
                    peers.append(str(row["proxy"]))
    # unique preserve order
    seen: set[str] = set()
    out: list[str] = []
    for p in peers:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def connect_via(upstream: str, host: str, port: int, timeout: float) -> socket.socket:
    uh, up = upstream.rsplit(":", 1)
    s = socket.create_connection((uh, int(up)), timeout=timeout)
    s.settimeout(timeout)
    req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n"
    s.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf and len(buf) < 8192:
        chunk = s.recv(256)
        if not chunk:
            raise OSError("upstream closed during CONNECT")
        buf += chunk
    head = buf.split(b"\r\n", 1)[0]
    if b" 200 " not in head and not head.startswith(b"HTTP/1.0 200") and not head.startswith(
        b"HTTP/1.1 200"
    ):
        s.close()
        raise OSError(f"CONNECT failed: {head!r}")
    s.settimeout(None)
    return s


def handle_client(
    client: socket.socket,
    pool: UpstreamPool,
    timeout: float,
    tries: int,
) -> None:
    try:
        client.settimeout(timeout)
        req = b""
        while b"\r\n\r\n" not in req and len(req) < 65536:
            chunk = client.recv(4096)
            if not chunk:
                client.close()
                return
            req += chunk
        first = req.split(b"\r\n", 1)[0].decode("latin1", "replace")
        parts = first.split()
        if len(parts) < 2:
            client.close()
            return
        method, target = parts[0].upper(), parts[1]

        if method == "CONNECT":
            # CONNECT host:port HTTP/1.1
            hostport = target
            if ":" in hostport:
                host, port_s = hostport.rsplit(":", 1)
                port = int(port_s)
            else:
                host, port = hostport, 443
            last_err: Exception | None = None
            for _ in range(max(1, tries)):
                up = pool.next()
                try:
                    remote = connect_via(up, host, port, timeout)
                    pool.prefer(up)
                    client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    client.settimeout(None)
                    relay(client, remote)
                    return
                except Exception as e:
                    last_err = e
                    continue
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            client.close()
            if last_err:
                print(f"CONNECT {host}:{port} fail: {last_err}", flush=True)
            return

        # Absolute-form GET http://host/path for plain HTTP via upstream absolute URI
        # Fallback: open CONNECT to host:80 then send relative request — simpler use CONNECT path.
        client.sendall(
            b"HTTP/1.1 501 Not Implemented\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: 44\r\n\r\n"
            b"Use HTTPS CONNECT or curl -x for HTTPS only\n"
        )
        client.close()
    except Exception as e:
        try:
            client.close()
        except OSError:
            pass
        print(f"client error: {e}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Local HTTP proxy → upstream CONNECT")
    ap.add_argument("--listen", default="127.0.0.1:18080")
    ap.add_argument("--upstream", default="", help="host:port of HTTP CONNECT peer")
    ap.add_argument(
        "--upstream-file",
        default="",
        help="JSON list of {proxy: host:port} or plain strings",
    )
    ap.add_argument("--timeout", type=float, default=12.0)
    ap.add_argument("--tries", type=int, default=3, help="upstreams to try per request")
    args = ap.parse_args()

    peers = load_peers(args.upstream or None, args.upstream_file or None)
    pool = UpstreamPool(peers)
    host, port_s = args.listen.rsplit(":", 1)
    port = int(port_s)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(64)
    print(f"listening http://{args.listen} upstreams={len(peers)} first={peers[0]}", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(
            target=handle_client,
            args=(conn, pool, args.timeout, args.tries),
            daemon=True,
        ).start()


if __name__ == "__main__":
    raise SystemExit(main())
