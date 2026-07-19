#!/usr/bin/env python3
"""Outside accept for Composition III — SOCKS map + agent dial-in.

Lab / product reverse that actually works without fragile Xray reverse.

  [china agent(s)] --TCP--> [this accept] --SOCKS--> Spectre (VPN → this SOCKS)

Supports multiple simultaneous agents (population reverse). Round-robin by default.
Same agent_id reconnect replaces that slot only (does not kick others).

Usage:
  python3 spectre-reverse-accept.py --token SECRET --listen 0.0.0.0:8443 --socks 127.0.0.1:10808
"""

from __future__ import annotations

import argparse
import select
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass, field


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


@dataclass
class AgentConn:
    agent_id: str
    conn: socket.socket
    addr: object
    connected_at: float = field(default_factory=time.time)
    last_pong: float = field(default_factory=time.time)


class AcceptServer:
    def __init__(
        self,
        token: str,
        listen: str,
        socks: str,
        data_port_min: int = 0,
        data_port_max: int = 0,
    ) -> None:
        self.token = token
        self.listen_host, self.listen_port = self._hp(listen)
        self.socks_host, self.socks_port = self._hp(socks)
        # When set, DATA listeners bind only in [min, max] so cloud firewalls
        # can open a fixed range (ephemeral ports are otherwise blocked).
        self.data_port_min = data_port_min
        self.data_port_max = data_port_max
        self._data_port_next = data_port_min if data_port_min else 0
        self._agents: dict[str, AgentConn] = {}
        self._rr_ids: list[str] = []
        self._rr_i = 0
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    @staticmethod
    def _hp(s: str) -> tuple[str, int]:
        if s.count(":") == 1:
            h, p = s.rsplit(":", 1)
            return h or "0.0.0.0", int(p)
        raise ValueError(f"expected host:port, got {s!r}")

    def run(self) -> None:
        agent_l = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        agent_l.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        agent_l.bind((self.listen_host, self.listen_port))
        agent_l.listen(32)
        socks_l = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        socks_l.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        socks_l.bind((self.socks_host, self.socks_port))
        socks_l.listen(64)
        data_note = (
            f" · data ports {self.data_port_min}-{self.data_port_max}"
            if self.data_port_min and self.data_port_max
            else " · data ports ephemeral"
        )
        print(
            f"accept: agent listen {self.listen_host}:{self.listen_port} · "
            f"socks {self.socks_host}:{self.socks_port} · multi-agent pool"
            f"{data_note}",
            flush=True,
        )
        threading.Thread(target=self._accept_agents, args=(agent_l,), daemon=True).start()
        while True:
            client, addr = socks_l.accept()
            threading.Thread(
                target=self._handle_socks, args=(client, addr), daemon=True
            ).start()

    def _accept_agents(self, listener: socket.socket) -> None:
        while True:
            conn, addr = listener.accept()
            threading.Thread(
                target=self._handshake_agent, args=(conn, addr), daemon=True
            ).start()

    def _parse_hello(self, text: str) -> tuple[str, str] | None:
        """Return (token, agent_id) or None.

        SPECTRE-REV1 <token>
        SPECTRE-REV2 <token> <agent_id>
        """
        parts = text.split()
        if len(parts) == 2 and parts[0] == "SPECTRE-REV1":
            return parts[1], "default"
        if len(parts) >= 3 and parts[0] == "SPECTRE-REV2":
            agent_id = parts[2].strip() or "default"
            # sanitize agent_id
            if not all(c.isalnum() or c in "-_." for c in agent_id) or len(agent_id) > 64:
                return None
            return parts[1], agent_id
        return None

    def _register_agent(self, agent_id: str, conn: socket.socket, addr: object) -> None:
        with self._cond:
            old = self._agents.get(agent_id)
            if old is not None and old.conn is not conn:
                try:
                    old.conn.close()
                except OSError:
                    pass
            self._agents[agent_id] = AgentConn(agent_id=agent_id, conn=conn, addr=addr)
            if agent_id not in self._rr_ids:
                self._rr_ids.append(agent_id)
            self._cond.notify_all()
            n = len(self._agents)
        print(f"agent connected id={agent_id} from {addr} (pool={n})", flush=True)

    def _unregister_agent(self, agent_id: str, conn: socket.socket) -> None:
        with self._cond:
            cur = self._agents.get(agent_id)
            if cur is not None and cur.conn is conn:
                del self._agents[agent_id]
                if agent_id in self._rr_ids:
                    self._rr_ids = [i for i in self._rr_ids if i != agent_id]
                    if self._rr_ids:
                        self._rr_i %= len(self._rr_ids)
                    else:
                        self._rr_i = 0
            n = len(self._agents)
        print(f"agent disconnected id={agent_id} (pool={n})", flush=True)

    def _pick_agent(self) -> AgentConn | None:
        with self._cond:
            if not self._rr_ids:
                return None
            # round-robin among currently connected
            for _ in range(len(self._rr_ids)):
                aid = self._rr_ids[self._rr_i % len(self._rr_ids)]
                self._rr_i = (self._rr_i + 1) % len(self._rr_ids)
                ac = self._agents.get(aid)
                if ac is not None:
                    return ac
            return None

    def _handshake_agent(self, conn: socket.socket, addr: object) -> None:
        agent_id = "default"
        try:
            conn.settimeout(30)
            line = b""
            while b"\n" not in line and len(line) < 512:
                chunk = conn.recv(256)
                if not chunk:
                    conn.close()
                    return
                line += chunk
            text = line.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
            parsed = self._parse_hello(text)
            if parsed is None:
                conn.sendall(b"ERR auth\n")
                conn.close()
                print(f"agent reject {addr}: bad handshake {text!r}", flush=True)
                return
            token, agent_id = parsed
            if token != self.token:
                conn.sendall(b"ERR auth\n")
                conn.close()
                print(f"agent reject {addr}: bad token", flush=True)
                return
            conn.sendall(b"OK\n")
            conn.settimeout(None)
            self._register_agent(agent_id, conn, addr)
            self._agent_loop(agent_id, conn)
        except OSError as exc:
            print(f"agent error id={agent_id} {addr}: {exc}", flush=True)
            try:
                conn.close()
            except OSError:
                pass
            self._unregister_agent(agent_id, conn)

    def _agent_loop(self, agent_id: str, conn: socket.socket) -> None:
        """Keep control channel open; data planes use dedicated sockets."""
        try:
            while True:
                r, _, _ = select.select([conn], [], [], 60)
                if not r:
                    try:
                        conn.sendall(b"PING\n")
                    except OSError:
                        break
                    continue
                data = conn.recv(64)
                if not data:
                    break
                # agent may send PONG
                if b"PONG" in data:
                    with self._lock:
                        ac = self._agents.get(agent_id)
                        if ac is not None and ac.conn is conn:
                            ac.last_pong = time.time()
        finally:
            self._unregister_agent(agent_id, conn)
            try:
                conn.close()
            except OSError:
                pass

    def _wait_agent(self, timeout: float = 5.0) -> AgentConn | None:
        deadline = time.time() + timeout
        with self._cond:
            while True:
                if self._rr_ids:
                    for aid in list(self._rr_ids):
                        ac = self._agents.get(aid)
                        if ac is not None:
                            return ac
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)

    def _handle_socks(self, client: socket.socket, addr: object) -> None:
        try:
            client.settimeout(30)
            gre = client.recv(32)
            if len(gre) < 2 or gre[0] != 5:
                client.close()
                return
            client.sendall(b"\x05\x00")
            req = client.recv(4)
            if len(req) < 4 or req[0] != 5 or req[1] != 1:
                client.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
                client.close()
                return
            atyp = req[3]
            if atyp == 1:
                raw = client.recv(4)
                host = socket.inet_ntoa(raw)
            elif atyp == 3:
                ln = client.recv(1)[0]
                host = client.recv(ln).decode("utf-8", "replace")
            elif atyp == 4:
                raw = client.recv(16)
                host = socket.inet_ntop(socket.AF_INET6, raw)
            else:
                client.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
                client.close()
                return
            port = struct.unpack("!H", client.recv(2))[0]
            self._socks_via_agent(client, host, port)
        except OSError:
            try:
                client.close()
            except OSError:
                pass

    def _bind_data_listener(self) -> tuple[socket.socket, int]:
        """Bind DATA accept socket; prefer fixed range for firewalled VPS."""
        data_bind = (
            "0.0.0.0"
            if self.listen_host in ("0.0.0.0", "", "::")
            else self.listen_host
        )
        if not (self.data_port_min and self.data_port_max):
            data_l = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            data_l.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            data_l.bind((data_bind, 0))
            data_l.listen(1)
            return data_l, data_l.getsockname()[1]

        lo, hi = self.data_port_min, self.data_port_max
        if hi < lo:
            lo, hi = hi, lo
        span = hi - lo + 1
        with self._lock:
            start = self._data_port_next if self._data_port_next else lo
            self._data_port_next = start
        for i in range(span):
            port = lo + ((start - lo + i) % span)
            data_l = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            data_l.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                data_l.bind((data_bind, port))
                data_l.listen(1)
                with self._lock:
                    self._data_port_next = port + 1 if port < hi else lo
                return data_l, port
            except OSError:
                data_l.close()
                continue
        raise OSError(f"no free data port in {lo}-{hi}")

    def _socks_via_agent(self, client: socket.socket, host: str, port: int) -> None:
        try:
            data_l, data_port = self._bind_data_listener()
        except OSError:
            client.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
            client.close()
            return
        data_l.settimeout(15)

        agent = self._pick_agent()
        if agent is None:
            agent = self._wait_agent(2.0)
        if agent is None:
            client.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
            client.close()
            data_l.close()
            return

        try:
            msg = f"DATA {data_port} {host} {port}\n".encode()
            agent.conn.sendall(msg)
        except OSError:
            client.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
            client.close()
            data_l.close()
            return

        try:
            agent_data, _ = data_l.accept()
        except OSError:
            client.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
            client.close()
            data_l.close()
            return
        finally:
            data_l.close()

        try:
            client.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            client.settimeout(None)
            agent_data.settimeout(None)
            relay(client, agent_data)
        except OSError:
            for s in (client, agent_data):
                try:
                    s.close()
                except OSError:
                    pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Spectre reverse accept (outside)")
    ap.add_argument("--token", required=True, help="shared pairing token")
    ap.add_argument("--listen", default="0.0.0.0:8443", help="agent dial-in host:port")
    ap.add_argument("--socks", default="127.0.0.1:10808", help="SOCKS map host:port")
    ap.add_argument(
        "--data-port-min",
        type=int,
        default=0,
        help="min DATA listener port (0 = OS ephemeral)",
    )
    ap.add_argument(
        "--data-port-max",
        type=int,
        default=0,
        help="max DATA listener port (0 = OS ephemeral)",
    )
    args = ap.parse_args()
    try:
        AcceptServer(
            args.token,
            args.listen,
            args.socks,
            data_port_min=args.data_port_min,
            data_port_max=args.data_port_max,
        ).run()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
