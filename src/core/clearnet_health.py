"""Clearnet netns health + lightweight speed probe for Exclude apps.

Never runs teardown. May call ``sudo -n clearnet-netns check|setup`` when
passwordless sudo is available; otherwise uses read-only host/ns checks.

``on_progress(fraction, label)`` is optional and may be called from a worker
thread — the UI must marshal to the main loop.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ProgressCb = Callable[[float, str], None]


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


def _report(on_progress: ProgressCb | None, fraction: float, label: str) -> None:
    if on_progress is None:
        return
    try:
        on_progress(max(0.0, min(1.0, fraction)), label)
    except Exception:
        pass


# Cloudflare down endpoint; 5 MiB is enough to get past TLS + TCP slow-start
# without making Apps → Check feel multi-minute. 1 MiB samples read ~3–5× low.
_SPEED_SAMPLE_BYTES = 5_000_000
_SPEED_SAMPLE_URL = (
    f"https://speed.cloudflare.com/__down?bytes={_SPEED_SAMPLE_BYTES}"
)


def _speed_sample_mbps(*, timeout_sec: float = 12.0) -> float | None:
    """Return a short HTTPS download sample in Mbit/s, or None."""
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
                str(max(4, int(timeout_sec))),
                _SPEED_SAMPLE_URL,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec + 2.0,
            check=False,
            start_new_session=True,
        )
        out = (r.stdout or "").strip()
        if r.returncode == 0 and out.replace(".", "", 1).isdigit():
            bps = float(out)
            return (bps * 8) / 1_000_000
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return None


def _sudo_n(*args: str, timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
    """Run with sudo -n; never hang the UI (caller should be off the main thread)."""
    sudo = shutil.which("sudo") or "sudo"
    try:
        return subprocess.run(  # noqa: S603
            [sudo, "-n", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args=[sudo, "-n", *args],
            returncode=124,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            stderr="timeout",
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
            if ping != ping:
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


def _fallback_probe(on_progress: ProgressCb | None = None) -> ClearnetHealth:
    """Read-only / in-ns probe without relying on a full helper check."""
    ns = (os.environ.get("CLEARNET_NS") or "clearnet").strip()
    lines: list[str] = []
    ok = True
    _report(on_progress, 0.1, "Looking for clearnet netns…")

    netns_ok = any((Path(b) / ns).exists() for b in ("/run/netns", "/var/run/netns"))
    lines.append(f"netns: {'present' if netns_ok else 'missing'}")
    if not netns_ok:
        ok = False

    _report(on_progress, 0.25, "Reading DNS config…")
    resolv = Path(f"/etc/netns/{ns}/resolv.conf")
    if resolv.is_file():
        try:
            text = resolv.read_text(encoding="utf-8", errors="replace")
            nss = [
                ln.split()[-1]
                for ln in text.splitlines()
                if ln.startswith("nameserver")
            ]
            lines.append("resolv: " + " ".join(nss))
            if nss and nss[0] not in ("1.1.1.1", "9.9.9.9", "8.8.8.8"):
                lines.append("note: first DNS is not public — may feel slow")
        except OSError:
            lines.append("resolv: unreadable")
    else:
        lines.append("resolv: missing")
        ok = False

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
        _report(on_progress, 0.45, "Pinging 1.1.1.1…")
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

        _report(on_progress, 0.7, "Speed sample…")
        # ~5 MiB — a 1 MiB sample under-reports badly (TLS + slow-start).
        sample = _speed_sample_mbps(timeout_sec=12.0)
        if sample is not None:
            lines.append(f"sample_mbps: {sample:.0f}")
        else:
            lines.append("sample_mbps: n/a")
    else:
        lines.append(
            "note: full probe needs passwordless clearnet-netns (spectre setup-clearnet)"
        )

    _report(on_progress, 0.95, "Finishing…")
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


def check_clearnet(
    *,
    try_helper: bool = True,
    on_progress: ProgressCb | None = None,
) -> ClearnetHealth:
    """Probe clearnet netns health + short speed sample (worker-thread safe)."""
    _report(on_progress, 0.05, "Starting clearnet check…")
    helper = find_clearnet_netns() if try_helper else None
    if helper:
        _report(on_progress, 0.2, "Running clearnet-netns check…")
        try:
            # Newer helpers do a real probe; older ones only have setup/status.
            r = _sudo_n(helper, "check", timeout=18.0)
            out = (r.stdout or "").strip()
            err = (r.stderr or "").strip()
            if r.returncode in (0, 1) and out and "health:" in out.lower():
                _report(on_progress, 0.9, "Parsing results…")
                h = _parse_check_output(out)
                h.can_repair = True
                if r.returncode != 0:
                    h.ok = False
                # Tiny helper samples (1 MiB) under-report; optionally refine.
                if h.sample_mbps is not None and h.sample_mbps < 50:
                    local = _speed_sample_mbps(timeout_sec=12.0)
                    if local is not None and local > h.sample_mbps * 1.3:
                        h.detail_lines.append(
                            f"note: helper sample ~{h.sample_mbps:.0f} Mbit/s; "
                            f"longer sample ~{local:.0f} Mbit/s"
                        )
                        h.sample_mbps = local
                        # Refresh the human summary's sample clause.
                        bits = [
                            p
                            for p in h.summary.split(" · ")
                            if not p.startswith("~") or "Mbit/s" not in p
                        ]
                        bits.append(f"~{local:.0f} Mbit/s sample")
                        h.summary = " · ".join(bits)
                _report(on_progress, 1.0, "Done")
                return h
            if r.returncode == 124 or err == "timeout":
                fb = _fallback_probe(on_progress)
                fb.detail_lines.append("helper check timed out — used local probe")
                fb.can_repair = True
                _report(on_progress, 1.0, "Done")
                return fb
            if "password" in err.lower() or "a password is required" in err.lower():
                fb = _fallback_probe(on_progress)
                fb.detail_lines.append("sudo: password required for full check")
                fb.can_repair = False
                _report(on_progress, 1.0, "Done")
                return fb
            # Old clearnet-netns without `check` prints usage and exits 2.
            if "usage:" in (err + out).lower() or r.returncode == 2:
                fb = _fallback_probe(on_progress)
                fb.detail_lines.append(
                    "helper is outdated (no check) — re-run: spectre setup-clearnet"
                )
                fb.can_repair = True
                _report(on_progress, 1.0, "Done")
                return fb
            if out or err:
                fb = _fallback_probe(on_progress)
                fb.detail_lines.append(f"helper: {(err or out)[:120]}")
                fb.can_repair = True
                _report(on_progress, 1.0, "Done")
                return fb
        except (OSError, subprocess.TimeoutExpired) as exc:
            fb = _fallback_probe(on_progress)
            fb.detail_lines.append(f"helper error: {exc}")
            _report(on_progress, 1.0, "Done")
            return fb
    h = _fallback_probe(on_progress)
    _report(on_progress, 1.0, "Done")
    return h


def repair_clearnet(on_progress: ProgressCb | None = None) -> tuple[bool, str]:
    """Refresh nft + DNS via clearnet-netns setup (no teardown when healthy)."""
    _report(on_progress, 0.1, "Looking for clearnet-netns…")
    helper = find_clearnet_netns()
    if not helper:
        _report(on_progress, 1.0, "Failed")
        return False, "clearnet-netns not found — run: spectre setup-clearnet"
    _report(on_progress, 0.35, "Running clearnet-netns setup…")
    try:
        r = _sudo_n(helper, "setup", timeout=30.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _report(on_progress, 1.0, "Failed")
        return False, f"setup failed: {exc}"
    if r.returncode == 124:
        _report(on_progress, 1.0, "Failed")
        return False, "setup timed out"
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "setup failed").strip()
        if "password" in err.lower():
            _report(on_progress, 1.0, "Failed")
            return False, "Need passwordless sudo for clearnet-netns (spectre setup-clearnet)"
        _report(on_progress, 1.0, "Failed")
        return False, err[:240]
    _report(on_progress, 0.7, "Re-checking clearnet…")
    h = check_clearnet(try_helper=True, on_progress=on_progress)
    _report(on_progress, 1.0, "Done")
    if h.ok:
        return True, f"Clearnet repaired · {h.summary}"
    return True, f"Setup ran · {h.summary}"
