"""Clearnet netns health + lightweight speed probe for Exclude apps.

Never runs teardown. May call ``sudo -n clearnet-netns check|setup`` when
passwordless sudo is available; otherwise uses read-only host/ns checks.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ClearnetHealth:
    ok: bool
    summary: str
    detail_lines: list[str] = field(default_factory=list)
    uplink: str = ""
    sample_mbps: float | None = None
    inet_ping_ms: float | None = None
    dns_ms: float | None = None
    can_repair: bool = False


def _first_executable(*candidates: str | None) -> str | None:
    for c in candidates:
        if not c:
            continue
        p = Path(c)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def find_clearnet_netns() -> str | None:
    env = (os.environ.get("SPECTRE_CLEARNET_NETNS") or "").strip()
    which = shutil.which("clearnet-netns")
    return _first_executable(
        env or None,
        "/usr/local/libexec/spectre/clearnet-netns",
        "/usr/libexec/spectre/clearnet-netns",
        "/usr/local/sbin/clearnet-netns",
        which,
    )


def _sudo_n(*args: str, timeout: float = 12.0) -> subprocess.CompletedProcess[str]:
    sudo = shutil.which("sudo") or "sudo"
    return subprocess.run(  # noqa: S603
        [sudo, "-n", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _parse_check_output(text: str) -> ClearnetHealth:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    data: dict[str, str] = {}
    for ln in lines:
        if ":" in ln:
            k, _, v = ln.partition(":")
            data[k.strip().lower().replace(" ", "_")] = v.strip()

    health = (data.get("health") or "").upper()
    ok = health == "OK"
    uplink = data.get("uplink") or ""
    sample = None
    if data.get("sample_mbps") not in (None, "", "n/a", "FAIL"):
        try:
            sample = float(data["sample_mbps"])
        except ValueError:
            sample = None
    ping = None
    if data.get("inet_ping_ms") not in (None, "", "FAIL", "n/a", "ok"):
        try:
            ping = float(re.sub(r"[^0-9.]", "", data["inet_ping_ms"]) or "nan")
            if ping != ping:  # NaN
                ping = None
        except ValueError:
            ping = None
    dns = None
    if data.get("dns_ms") not in (None, "", "FAIL", "n/a", "ok"):
        try:
            dns = float(data["dns_ms"])
        except ValueError:
            dns = None

    bits = [f"health {health or 'unknown'}"]
    if uplink and uplink != "none":
        bits.append(f"uplink {uplink}")
    if ping is not None:
        bits.append(f"ping {ping:.0f} ms")
    if dns is not None:
        bits.append(f"dns {dns:.0f} ms")
    if sample is not None:
        bits.append(f"~{sample:.0f} Mbit/s sample")
    if not ok:
        bits.append("run Repair or: sudo clearnet-netns setup")

    return ClearnetHealth(
        ok=ok,
        summary=" · ".join(bits),
        detail_lines=lines,
        uplink=uplink,
        sample_mbps=sample,
        inet_ping_ms=ping,
        dns_ms=dns,
        can_repair=bool(find_clearnet_netns()),
    )


def _fallback_probe() -> ClearnetHealth:
    """Read-only probe without clearnet-netns helper."""
    ns = (os.environ.get("CLEARNET_NS") or "clearnet").strip()
    lines: list[str] = []
    ok = True
    netns_ok = any(
        (Path(b) / ns).exists() for b in ("/run/netns", "/var/run/netns")
    )
    lines.append(f"netns: {'present' if netns_ok else 'missing'}")
    if not netns_ok:
        ok = False

    resolv = Path(f"/etc/netns/{ns}/resolv.conf")
    if resolv.is_file():
        try:
            text = resolv.read_text(encoding="utf-8", errors="replace")
            lines.append("resolv: " + " ".join(
                ln.split()[-1] for ln in text.splitlines() if ln.startswith("nameserver")
            ))
            # Prefer public DNS first
            nss = [
                ln.split()[-1]
                for ln in text.splitlines()
                if ln.startswith("nameserver")
            ]
            if nss and nss[0] not in ("1.1.1.1", "9.9.9.9", "8.8.8.8"):
                lines.append("note: first DNS is not public — may feel slow")
        except OSError:
            lines.append("resolv: unreadable")
    else:
        lines.append("resolv: missing")
        ok = False

    # If this process is inside clearnet, can ping/curl directly
    in_ns = False
    try:
        r = subprocess.run(  # noqa: S603
            ["ip", "netns", "identify", str(os.getpid())],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        in_ns = (r.stdout or "").strip() == ns
    except (OSError, subprocess.TimeoutExpired):
        pass

    ping_ms = None
    sample = None
    if in_ns:
        try:
            r = subprocess.run(  # noqa: S603
                ["ping", "-c", "1", "-W", "2", "1.1.1.1"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            m = re.search(r"time=([\d.]+)", r.stdout or "")
            if m:
                ping_ms = float(m.group(1))
                lines.append(f"inet_ping_ms: {ping_ms}")
            else:
                lines.append("inet_ping_ms: FAIL")
                ok = False
        except (OSError, subprocess.TimeoutExpired):
            lines.append("inet_ping_ms: FAIL")
            ok = False
        try:
            r = subprocess.run(  # noqa: S603
                [
                    "curl",
                    "-4",
                    "-sS",
                    "-o",
                    "/dev/null",
                    "-w",
                    "%{speed_download}",
                    "--max-time",
                    "8",
                    "https://speed.cloudflare.com/__down?bytes=2000000",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if r.returncode == 0 and r.stdout.strip().replace(".", "", 1).isdigit():
                bps = float(r.stdout.strip())
                sample = (bps * 8) / 1_000_000
                lines.append(f"sample_mbps: {sample:.0f}")
        except (OSError, subprocess.TimeoutExpired, ValueError):
            lines.append("sample_mbps: n/a")
    else:
        lines.append("note: run Check as user with passwordless clearnet-netns for full probe")

    bits = ["health " + ("OK" if ok else "DEGRADED")]
    if ping_ms is not None:
        bits.append(f"ping {ping_ms:.0f} ms")
    if sample is not None:
        bits.append(f"~{sample:.0f} Mbit/s sample")
    return ClearnetHealth(
        ok=ok,
        summary=" · ".join(bits),
        detail_lines=lines,
        sample_mbps=sample,
        inet_ping_ms=ping_ms,
        can_repair=bool(find_clearnet_netns()),
    )


def check_clearnet(*, try_helper: bool = True) -> ClearnetHealth:
    """Probe clearnet netns health + short speed sample."""
    helper = find_clearnet_netns() if try_helper else None
    if helper:
        try:
            r = _sudo_n(helper, "check", timeout=20.0)
            if r.returncode in (0, 1) and (r.stdout or "").strip():
                h = _parse_check_output(r.stdout)
                h.can_repair = True
                if r.returncode != 0:
                    h.ok = False
                return h
            # sudo failed or empty — fall back
            if r.stderr and "password" in (r.stderr or "").lower():
                fb = _fallback_probe()
                fb.detail_lines.append("sudo: password required for full check")
                fb.can_repair = False
                return fb
        except (OSError, subprocess.TimeoutExpired) as exc:
            fb = _fallback_probe()
            fb.detail_lines.append(f"helper error: {exc}")
            return fb
    return _fallback_probe()


def repair_clearnet() -> tuple[bool, str]:
    """Refresh nft + DNS via clearnet-netns setup (no teardown when healthy)."""
    helper = find_clearnet_netns()
    if not helper:
        return False, "clearnet-netns not found — run: spectre setup-clearnet"
    try:
        r = _sudo_n(helper, "setup", timeout=45.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"setup failed: {exc}"
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "setup failed").strip()
        if "password" in err.lower():
            return False, "Need passwordless sudo for clearnet-netns (spectre setup-clearnet)"
        return False, err[:240]
    # Re-check
    h = check_clearnet(try_helper=True)
    if h.ok:
        return True, f"Clearnet repaired · {h.summary}"
    return True, f"Setup ran · {h.summary}"
