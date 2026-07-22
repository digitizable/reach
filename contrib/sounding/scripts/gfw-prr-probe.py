#!/usr/bin/env python3
"""
Probe harness for GFW PRR faces.

Compares naked SPECTRE accept vs REALITY cover port:
  - TLS ClientHello (should not see SPECTRE banner on cover)
  - SPECTRE-REV / INVITE bytes (naked may answer; cover should not speak SPECTRE)

  python3 gfw-prr-probe.py --host 203.0.113.10 \\
    --naked-port 18443 --cover-port 18444
"""

from __future__ import annotations

import argparse
import socket
import ssl
import time


def recv_some(sock: socket.socket, n: int = 256, timeout: float = 3.0) -> bytes:
    sock.settimeout(timeout)
    try:
        return sock.recv(n)
    except Exception:
        return b""


def probe_raw(host: str, port: int, payload: bytes | None = None) -> dict:
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=5)
        if payload:
            s.sendall(payload)
        data = recv_some(s)
        s.close()
        return {
            "ok": True,
            "ms": int((time.time() - t0) * 1000),
            "hex": data[:48].hex(),
            "ascii": "".join(chr(b) if 32 <= b < 127 else "." for b in data[:64]),
            "len": len(data),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}:{e}", "ms": int((time.time() - t0) * 1000)}


def probe_tls(host: str, port: int, sni: str) -> dict:
    t0 = time.time()
    try:
        raw = socket.create_connection((host, port), timeout=5)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # REALITY will often fail standard TLS verify — we care about *not* getting SPECTRE
        try:
            ss = ctx.wrap_socket(raw, server_hostname=sni)
            # if handshake completes, note peer cert subject
            cert = ss.getpeercert(binary_form=False) or {}
            ss.close()
            return {
                "ok": True,
                "tls": "handshake_ok",
                "ms": int((time.time() - t0) * 1000),
                "cert_subject": str(cert.get("subject", ""))[:120],
            }
        except ssl.SSLError as e:
            return {
                "ok": True,
                "tls": f"ssl_error:{e}",
                "ms": int((time.time() - t0) * 1000),
                "note": "SSL error common for REALITY vs stock OpenSSL client",
            }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}:{e}", "ms": int((time.time() - t0) * 1000)}


def main() -> int:
    ap = argparse.ArgumentParser(description="GFW PRR probe harness")
    ap.add_argument("--host", required=True)
    ap.add_argument("--naked-port", type=int, default=18443)
    ap.add_argument("--cover-port", type=int, default=18444)
    ap.add_argument("--sni", default="www.cloudflare.com")
    args = ap.parse_args()

    spectre_hello = b"SPECTRE-REV1 probe-test\n"
    invite_look = b"GET / HTTP/1.1\r\nHost: x\r\n\r\n"

    print(f"host={args.host}")
    print("--- naked port", args.naked_port)
    n1 = probe_raw(args.host, args.naked_port, spectre_hello)
    print("  SPECTRE-REV send:", n1)
    n2 = probe_raw(args.host, args.naked_port, None)
    print("  connect only (may get INVITE if probe-invite wrong port):", n2)
    n3 = probe_tls(args.host, args.naked_port, args.sni)
    print("  TLS ClientHello:", n3)

    print("--- cover port", args.cover_port)
    c1 = probe_raw(args.host, args.cover_port, spectre_hello)
    print("  SPECTRE-REV send:", c1)
    c2 = probe_tls(args.host, args.cover_port, args.sni)
    print("  TLS ClientHello:", c2)

    # Verdict
    naked_speaks = "SPECTRE" in n1.get("ascii", "") or "OK" in n1.get("ascii", "")
    cover_speaks = "SPECTRE" in c1.get("ascii", "") or (
        c1.get("ascii", "").startswith("OK")
    )
    print("--- verdict")
    print(f"  naked speaks SPECTRE-ish: {naked_speaks}")
    print(f"  cover speaks SPECTRE-ish: {cover_speaks}  (want False)")
    print(f"  cover TLS interaction: {c2.get('tls') or c2.get('error')}")
    if cover_speaks:
        print("  FAIL: cover still exposes SPECTRE")
        return 1
    print("  PASS: cover does not return SPECTRE banner to raw probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
