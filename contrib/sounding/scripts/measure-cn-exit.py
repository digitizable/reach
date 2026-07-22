#!/usr/bin/env python3
"""
Measure a client-from-CN (or any) HTTP/SOCKS proxy under Mullvad.

  # HTTP proxy
  python3 measure-cn-exit.py --proxy http://user:pass@host:port

  # SOCKS5
  python3 measure-cn-exit.py --proxy socks5h://user:pass@host:port

Prints exit geo + HTTPS matrix. Exit 0 if countryCode=CN and cnki looks open.
Requires: Mullvad connected (warns if not). Uses stdlib only for HTTP proxy;
SOCKS5 needs PySocks if available, else falls back to curl subprocess.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse


TARGETS = [
    "www.cnki.net",
    "www.baidu.com",
    "www.pku.edu.cn",
    "www.gov.cn",
]


def mullvad_ok() -> tuple[bool, dict]:
    try:
        with urllib.request.urlopen("https://am.i.mullvad.net/json", timeout=10) as r:
            d = json.loads(r.read().decode())
        return bool(d.get("mullvad_exit_ip")), d
    except Exception as e:
        return False, {"error": str(e)}


def curl_matrix(proxy: str) -> dict:
    out: dict = {"proxy": proxy, "targets": {}, "exit": None}
    # Exit identity over HTTPS (CONNECT-friendly). Prefer full geo, then IP only.
    for url in (
        "https://ipapi.co/json/",
        "https://ifconfig.co/json",
        "https://api.ipify.org?format=json",
    ):
        try:
            r = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "--connect-timeout",
                    "12",
                    "--max-time",
                    "20",
                    "-x",
                    proxy,
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=25,
            )
            if r.returncode == 0 and r.stdout.strip().startswith("{"):
                try:
                    out["exit"] = json.loads(r.stdout)
                    ex = out["exit"]
                    if "countryCode" not in ex:
                        for k in ("country_code", "country_iso"):
                            if k in ex and ex[k]:
                                ex["countryCode"] = ex[k]
                                break
                    if "countryCode" not in ex and ex.get("country") == "China":
                        ex["countryCode"] = "CN"
                    if "query" not in ex and "ip" in ex:
                        ex["query"] = ex["ip"]
                    break
                except json.JSONDecodeError:
                    out["exit"] = {"raw": r.stdout.strip()[:200]}
                    break
            elif r.returncode == 0 and r.stdout.strip():
                out["exit"] = {"raw": r.stdout.strip()[:200]}
        except Exception as e:
            out["exit_error"] = str(e)

    for host in TARGETS:
        t0 = time.time()
        r = subprocess.run(
            [
                "curl",
                "-sS",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "--connect-timeout",
                "15",
                "--max-time",
                "30",
                "-x",
                proxy,
                f"https://{host}/",
            ],
            capture_output=True,
            text=True,
            timeout=35,
        )
        code = r.stdout.strip() if r.returncode == 0 else f"err:{r.returncode}"
        out["targets"][host] = {
            "http_code": code,
            "ms": int((time.time() - t0) * 1000),
            "stderr": (r.stderr or "")[:120],
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", required=True, help="curl -x URL: http://… or socks5h://…")
    ap.add_argument("--json-out", default="", help="write full result JSON")
    ap.add_argument("--require-mullvad", action="store_true", default=True)
    ap.add_argument("--allow-clearnet", action="store_true")
    args = ap.parse_args()

    ok, m = mullvad_ok()
    print("mullvad:", json.dumps(m, ensure_ascii=False)[:200])
    if args.require_mullvad and not args.allow_clearnet and not ok:
        print("ABORT: Mullvad not connected (use --allow-clearnet to override)", file=sys.stderr)
        return 2

    result = curl_matrix(args.proxy)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
            f.write("\n")

    exit_info = result.get("exit") or {}
    cc = exit_info.get("countryCode") or exit_info.get("country")
    cnki = (result.get("targets") or {}).get("www.cnki.net", {}).get("http_code")
    good = (cc == "CN" or cc == "China") and str(cnki).startswith("2")
    print(
        f"summary: country={cc!r} cnki={cnki!r} pass={good}",
        flush=True,
    )
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
