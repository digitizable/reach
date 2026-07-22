#!/usr/bin/env python3
"""
Harvest free HTTP proxies, keep those that:
  - complete CONNECT to www.cnki.net:443
  - report exit countryCode CN (best-effort geo)
  - optionally complete HTTPS GET with real response

Writes cn-connect-live.json next to this script (or --out).

  python3 harvest-cn-connect.py
  # Mullvad recommended
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import socket
import ssl
import time
import urllib.request
from pathlib import Path


LIST_URLS = [
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=CN&ssl=all&anonymity=all",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
]

# Prefer historically good ports/hosts from our lab
PRIORITY_SUBSTR = (
    "21000",
    "18255",
    "15010",
    "8800",
    "114.236",
    "123.138",
    "103.254",
    "113.249",
    "219.151",
    "120.92",
)


def fetch_lists() -> list[str]:
    found: set[str] = set()
    for u in LIST_URLS:
        try:
            with urllib.request.urlopen(u, timeout=20) as r:
                text = r.read().decode("utf-8", "replace")
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" not in line or not line[0].isdigit():
                    continue
                host, port = line.split(":")[:2]
                port = port.split()[0]
                if port.isdigit():
                    found.add(f"{host}:{port}")
        except Exception as e:
            print(f"list fail {u}: {type(e).__name__}", flush=True)
    return list(found)


def connect_ok(proxy: str, host: str = "www.cnki.net", port: int = 443, timeout: float = 8.0) -> bool:
    ph, pp = proxy.split(":")
    try:
        s = socket.create_connection((ph, int(pp)), timeout=timeout)
        s.settimeout(timeout)
        s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 4096:
            chunk = s.recv(256)
            if not chunk:
                break
            buf += chunk
        s.close()
        head = buf.split(b"\r\n", 1)[0]
        return b" 200" in head[:48] or head.startswith(b"HTTP/1.0 200") or head.startswith(b"HTTP/1.1 200")
    except Exception:
        return False


def https_status(proxy: str, host: str, timeout: float = 12.0) -> str | None:
    ph, pp = proxy.split(":")
    try:
        s = socket.create_connection((ph, int(pp)), timeout=timeout)
        s.settimeout(timeout)
        s.sendall(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 4096:
            chunk = s.recv(256)
            if not chunk:
                break
            buf += chunk
        if b" 200" not in buf[:80]:
            s.close()
            return None
        ctx = ssl.create_default_context()
        ss = ctx.wrap_socket(s, server_hostname=host)
        ss.sendall(
            f"GET / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: research-probe\r\nConnection: close\r\n\r\n".encode()
        )
        data = b""
        while len(data) < 8192:
            try:
                c = ss.recv(4096)
            except socket.timeout:
                break
            if not c:
                break
            data += c
        ss.close()
        line = data.split(b"\r\n", 1)[0].decode("latin1", "replace")
        if line.startswith("HTTP/"):
            parts = line.split()
            return parts[1] if len(parts) > 1 else line
        return line[:40]
    except Exception as e:
        return f"err:{type(e).__name__}"


def geo_via_proxy(proxy: str, timeout: float = 10.0) -> dict:
    """Best-effort: absolute-form HTTP to ip-api (many open proxies allow it)."""
    ph, pp = proxy.split(":")
    try:
        s = socket.create_connection((ph, int(pp)), timeout=timeout)
        s.settimeout(timeout)
        s.sendall(
            b"GET http://ip-api.com/json/?fields=status,countryCode,regionName,isp,as,query HTTP/1.1\r\n"
            b"Host: ip-api.com\r\nConnection: close\r\n\r\n"
        )
        data = b""
        while True:
            c = s.recv(4096)
            if not c:
                break
            data += c
        s.close()
        body = data.split(b"\r\n\r\n", 1)[-1].decode("utf-8", "replace")
        i, j = body.find("{"), body.rfind("}")
        if i >= 0 and j > i:
            return json.loads(body[i : j + 1])
    except Exception as e:
        return {"error": type(e).__name__}
    return {"error": "nojson"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="", help="output JSON path")
    ap.add_argument("--max-probe", type=int, default=150)
    ap.add_argument("--workers", type=int, default=40)
    ap.add_argument("--require-cnki-https", action="store_true", default=True)
    args = ap.parse_args()

    script_dir = Path(__file__).resolve().parent
    out_path = Path(args.out) if args.out else script_dir / "cn-connect-live.json"

    peers = fetch_lists()
    pri, rest = [], []
    for p in peers:
        (pri if any(s in p for s in PRIORITY_SUBSTR) else rest).append(p)
    ordered = (pri + rest)[: args.max_probe]
    print(f"candidates={len(peers)} probing={len(ordered)}", flush=True)

    def phase1(p: str) -> str | None:
        return p if connect_ok(p) else None

    hits: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(phase1, ordered):
            if r:
                hits.append(r)
    print(f"CONNECT200={len(hits)}", flush=True)

    winners: list[dict] = []
    for p in hits[:50]:
        geo = geo_via_proxy(p)
        cc = geo.get("countryCode")
        cnki = https_status(p, "www.cnki.net") if args.require_cnki_https else "skip"
        row = {
            "proxy": p,
            "exit": geo.get("query"),
            "region": geo.get("regionName"),
            "isp": geo.get("isp"),
            "as": geo.get("as"),
            "countryCode": cc,
            "cnki_https": cnki,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        print(
            f"  {p:28} geo={cc} cnki={cnki} exit={geo.get('query')}",
            flush=True,
        )
        if cc == "CN" and (not args.require_cnki_https or (cnki and str(cnki).startswith("2"))):
            winners.append(row)

    out_path.write_text(json.dumps(winners, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"winners={len(winners)} wrote {out_path}", flush=True)
    return 0 if winners else 1


if __name__ == "__main__":
    raise SystemExit(main())
