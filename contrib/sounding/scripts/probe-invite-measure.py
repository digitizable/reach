#!/usr/bin/env python3
"""
Measurement: listen for TCP, send SPECTRE-INVITE1, log who replies YES.

Writes:
  --log        JSONL event log (append)
  --web-dir    if set, publishes status.json + events.jsonl for the lab UI

  python3 probe-invite-measure.py --listen 0.0.0.0:19443 --web-dir /var/www/lab/api

Self vs external:
  Operator self-tests (localhost, hairpin, labeled tokens, known egress IPs)
  are counted separately so a third-party YES stands out overnight.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import select
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


INVITE = (
    b"SPECTRE-INVITE1\n"
    b"role=reverse-agent\n"
    b"ask=will_you_map_socks\n"
    b"reply=YES <token> or any line starting YES\n"
    b"\n"
)

# Tokens used in our own end-to-end checks (prefix match on first word after YES)
SELF_TOKEN_PREFIXES = (
    "local-dashboard",
    "via-public-ip",
    "after-firewall",
    "self-test",
    "selftest",
    "operator",
    "mullvad-self",
    "lab-check",
)

_state_lock = threading.Lock()
_state = {
    "listening": False,
    "listen": "",
    "sessions": 0,
    "yes_count": 0,
    "yes_self_count": 0,
    "yes_external_count": 0,
    "last_event_ts": None,
    "last_external_yes_ts": None,
    "started": None,
}

# Filled in main()
_self_nets: list[ipaddress._BaseNetwork] = []
_self_ips: set[str] = set()


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_line(path: str, obj: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _detect_local_ips() -> set[str]:
    """Best-effort host addresses (no outbound if env already set)."""
    found: set[str] = {"127.0.0.1", "::1"}
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            addr = info[4][0]
            if addr:
                found.add(addr.split("%")[0])
    except OSError:
        pass
    # Primary outbound interface address (often the public IP on a VPS)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        found.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 53))
        found.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return found


def configure_self(
    extra_ips: list[str] | None = None,
    extra_cidrs: list[str] | None = None,
) -> None:
    global _self_nets, _self_ips
    ips = _detect_local_ips()
    env_ips = os.environ.get("SPECTRE_SELF_IPS", "")
    for part in env_ips.replace(";", ",").split(","):
        part = part.strip()
        if part:
            ips.add(part)
    for part in extra_ips or []:
        part = part.strip()
        if part:
            ips.add(part)

    nets: list[ipaddress._BaseNetwork] = []
    env_cidrs = os.environ.get("SPECTRE_SELF_CIDRS", "")
    for part in list(extra_cidrs or []) + [
        c.strip() for c in env_cidrs.replace(";", ",").split(",") if c.strip()
    ]:
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            print(f"warn: bad self cidr {part!r}", flush=True)

    # Always treat loopback as self
    nets.append(ipaddress.ip_network("127.0.0.0/8"))
    nets.append(ipaddress.ip_network("::1/128"))

    _self_ips = ips
    _self_nets = nets
    print(
        f"self classification: ips={sorted(ips)} cidrs={[str(n) for n in nets]}",
        flush=True,
    )


def classify_yes(ip: str | None, first_line: str | None) -> tuple[str, str | None]:
    """Return (yes_kind, self_reason). yes_kind is 'self' or 'external'."""
    reasons: list[str] = []

    if ip:
        if ip in _self_ips:
            reasons.append(f"known_self_ip:{ip}")
        try:
            addr = ipaddress.ip_address(ip)
            for net in _self_nets:
                if addr in net:
                    reasons.append(f"self_net:{net}")
                    break
        except ValueError:
            pass

    if first_line:
        rest = first_line.strip()
        if rest.upper().startswith("YES"):
            token = rest[3:].strip().split()[0] if rest[3:].strip() else ""
            token_l = token.lower()
            for pref in SELF_TOKEN_PREFIXES:
                if token_l.startswith(pref):
                    reasons.append(f"self_token:{token}")
                    break

    if reasons:
        return "self", ";".join(reasons)
    return "external", None


def reclassify_event(ev: dict) -> dict:
    """Annotate an event dict for UI publish (idempotent)."""
    if not ev.get("said_yes"):
        if "yes_kind" not in ev:
            ev = dict(ev)
            ev["yes_kind"] = None
        return ev
    if ev.get("yes_kind") in ("self", "external"):
        return ev
    kind, reason = classify_yes(ev.get("ip"), ev.get("first_line"))
    out = dict(ev)
    out["yes_kind"] = kind
    if reason:
        out["self_reason"] = reason
    return out


def publish_web(web_dir: str | None, log_path: str) -> None:
    if not web_dir:
        return
    root = Path(web_dir)
    root.mkdir(parents=True, exist_ok=True)
    with _state_lock:
        st = dict(_state)
    st["updated"] = utc()
    st["pid"] = os.getpid()
    st["self_ips"] = sorted(_self_ips)
    (root / "status.json").write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")
    # Tail last 200 events for the UI (full history stays in --log)
    try:
        lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
        tail = lines[-200:] if len(lines) > 200 else lines
        out_lines: list[str] = []
        for line in tail:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                out_lines.append(line)
                continue
            if obj.get("event") == "session_end":
                obj = reclassify_event(obj)
            out_lines.append(json.dumps(obj, ensure_ascii=False))
        (root / "events.jsonl").write_text(
            "\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8"
        )
    except OSError:
        (root / "events.jsonl").write_text("", encoding="utf-8")


def handle(
    conn: socket.socket,
    addr: object,
    log_path: str,
    read_s: float,
    web_dir: str | None,
) -> None:
    peer = f"{addr[0]}:{addr[1]}" if isinstance(addr, tuple) else str(addr)
    t0 = time.time()
    rec: dict = {
        "ts": utc(),
        "peer": peer,
        "ip": addr[0] if isinstance(addr, tuple) else None,
        "event": "session_end",
        "bytes_in": 0,
        "first_line": None,
        "said_yes": False,
        "yes_kind": None,
        "self_reason": None,
        "duration_ms": 0,
        "error": None,
        "raw_hex_prefix": "",
    }
    try:
        conn.settimeout(read_s)
        conn.sendall(INVITE)
        buf = b""
        deadline = time.time() + read_s
        while time.time() < deadline:
            remain = max(0.05, deadline - time.time())
            r, _, _ = select.select([conn], [], [], remain)
            if not r:
                break
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            rec["bytes_in"] = len(buf)
            if b"\n" in buf or len(buf) > 2048:
                break
        text = buf.decode("utf-8", "replace")
        first = text.splitlines()[0].strip() if text.strip() else ""
        rec["first_line"] = first[:200] if first else None
        rec["raw_hex_prefix"] = buf[:64].hex() if buf else ""
        for line in text.splitlines():
            if line.strip().upper().startswith("YES"):
                rec["said_yes"] = True
                break
        if rec["said_yes"]:
            kind, reason = classify_yes(rec.get("ip"), rec.get("first_line"))
            rec["yes_kind"] = kind
            rec["self_reason"] = reason
    except Exception as e:
        rec["error"] = f"{type(e).__name__}:{e}"
    finally:
        rec["duration_ms"] = int((time.time() - t0) * 1000)
        try:
            conn.close()
        except OSError:
            pass
        with _state_lock:
            _state["sessions"] = int(_state["sessions"]) + 1
            if rec["said_yes"]:
                _state["yes_count"] = int(_state["yes_count"]) + 1
                if rec.get("yes_kind") == "self":
                    _state["yes_self_count"] = int(_state["yes_self_count"]) + 1
                else:
                    _state["yes_external_count"] = int(_state["yes_external_count"]) + 1
                    _state["last_external_yes_ts"] = rec["ts"]
            _state["last_event_ts"] = rec["ts"]
        log_line(log_path, rec)
        publish_web(web_dir, log_path)
        if rec["said_yes"]:
            flag = f"YES/{rec.get('yes_kind') or '?'}"
        else:
            flag = "no"
        print(
            f"[{utc()}] {peer} bytes_in={rec['bytes_in']} yes={flag} "
            f"first={rec['first_line']!r} reason={rec.get('self_reason')!r}",
            flush=True,
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Invite handshake measurement listener")
    ap.add_argument("--listen", default="0.0.0.0:19443")
    ap.add_argument("--seconds", type=int, default=0, help="0 = run until signal")
    ap.add_argument("--read-seconds", type=float, default=12.0)
    ap.add_argument("--log", default="/var/log/spectre-probe-invite.jsonl")
    ap.add_argument(
        "--web-dir",
        default="",
        help="Publish status.json + events.jsonl here (lab UI)",
    )
    ap.add_argument(
        "--self-ip",
        action="append",
        default=[],
        help="Extra operator egress IP (repeatable). Also SPECTRE_SELF_IPS=a,b",
    )
    ap.add_argument(
        "--self-cidr",
        action="append",
        default=[],
        help="Extra operator CIDR (repeatable). Also SPECTRE_SELF_CIDRS=",
    )
    args = ap.parse_args()
    web_dir = args.web_dir.strip() or None

    configure_self(extra_ips=args.self_ip, extra_cidrs=args.self_cidr)

    host, port_s = args.listen.rsplit(":", 1)
    port = int(port_s)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(128)
    srv.settimeout(1.0)

    with _state_lock:
        _state["listening"] = True
        _state["listen"] = args.listen
        _state["started"] = utc()
        _state["sessions"] = 0
        _state["yes_count"] = 0
        _state["yes_self_count"] = 0
        _state["yes_external_count"] = 0
        _state["last_external_yes_ts"] = None

    start_ev = {
        "ts": utc(),
        "event": "listen_start",
        "listen": args.listen,
        "seconds": args.seconds,
        "self_ips": sorted(_self_ips),
    }
    log_line(args.log, start_ev)
    publish_web(web_dir, args.log)
    print(f"listening {args.listen} log={args.log} web_dir={web_dir}", flush=True)

    end = time.time() + args.seconds if args.seconds > 0 else None
    try:
        while end is None or time.time() < end:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                # heartbeat publish so UI shows fresh updated
                if int(time.time()) % 15 == 0:
                    publish_web(web_dir, args.log)
                continue
            threading.Thread(
                target=handle,
                args=(conn, addr, args.log, args.read_seconds, web_dir),
                daemon=True,
            ).start()
    except KeyboardInterrupt:
        pass
    finally:
        with _state_lock:
            _state["listening"] = False
        log_line(args.log, {"ts": utc(), "event": "listen_stop"})
        publish_web(web_dir, args.log)
        srv.close()
        print("stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
