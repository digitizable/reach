#!/usr/bin/env python3
"""China-side agent for Composition III — dials out to outside accept.

  python3 spectre-reverse-agent.py --token SECRET --accept HOST:8443

Pairs with spectre-reverse-accept.py. Traffic that enters the accept SOCKS
map exits from this machine (mainland / peer network).
"""

from __future__ import annotations

import argparse
import select
import socket
import sys
import threading
import time


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
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


def handle_data(accept_host: str, data_port: int, target_host: str, target_port: int) -> None:
    try:
        target = socket.create_connection((target_host, target_port), timeout=20)
    except OSError as exc:
        print(f"target fail {target_host}:{target_port}: {exc}", flush=True)
        return
    try:
        back = socket.create_connection((accept_host, data_port), timeout=15)
    except OSError as exc:
        print(f"data channel fail {accept_host}:{data_port}: {exc}", flush=True)
        target.close()
        return
    print(f"relay {target_host}:{target_port}", flush=True)
    relay(back, target)


def session(
    accept: str,
    token: str,
    agent_id: str = "default",
    data_host: str | None = None,
) -> None:
    """Dial accept; optional data_host for DATA channels (REALITY wrap uses loopback accept)."""
    host, port_s = accept.rsplit(":", 1)
    port = int(port_s)
    data_h = (data_host or host).strip() or host
    # REV2 when non-default id; REV1 remains compatible with old accepts
    if agent_id and agent_id != "default":
        hello = f"SPECTRE-REV2 {token} {agent_id}\n"
    else:
        hello = f"SPECTRE-REV1 {token}\n"
    while True:
        try:
            conn = socket.create_connection((host, port), timeout=20)
            conn.sendall(hello.encode())
            resp = b""
            while b"\n" not in resp and len(resp) < 64:
                chunk = conn.recv(64)
                if not chunk:
                    raise OSError("closed during handshake")
                resp += chunk
            if not resp.startswith(b"OK"):
                raise OSError(f"auth failed: {resp!r}")
            print(
                f"connected to accept {accept} as id={agent_id} data_host={data_h}",
                flush=True,
            )
            conn.settimeout(None)
            buf = b""
            while True:
                r, _, _ = select.select([conn], [], [], 50)
                if not r:
                    # keepalive
                    try:
                        conn.sendall(b"PONG\n")
                    except OSError:
                        break
                    continue
                data = conn.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("utf-8", "replace").strip()
                    if text.startswith("PING"):
                        conn.sendall(b"PONG\n")
                        continue
                    if text.startswith("DATA "):
                        # DATA <data_port> <host> <dport>
                        parts = text.split()
                        if len(parts) != 4:
                            continue
                        _, dport_s, thost, tport_s = parts
                        threading.Thread(
                            target=handle_data,
                            args=(data_h, int(dport_s), thost, int(tport_s)),
                            daemon=True,
                        ).start()
        except OSError as exc:
            print(f"session error: {exc}; retry in 3s", flush=True)
            time.sleep(3)


def main() -> int:
    ap = argparse.ArgumentParser(description="Spectre reverse agent (China side)")
    ap.add_argument("--token", required=True)
    ap.add_argument("--accept", required=True, help="outside host:port")
    ap.add_argument(
        "--agent-id",
        default="default",
        help="unique id for multi-agent pool (SPECTRE-REV2)",
    )
    ap.add_argument(
        "--data-host",
        default="",
        help="host for DATA dial-back (default: accept host). "
        "Set to public origin when control uses 127.0.0.1 REALITY wrap.",
    )
    args = ap.parse_args()
    try:
        session(
            args.accept,
            args.token,
            args.agent_id,
            data_host=args.data_host.strip() or None,
        )
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
