"""Whonix / Qubes-Whonix environment detection for Spectre Desktop."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


DEFAULT_GATEWAY_IP = "10.152.152.10"


@dataclass(frozen=True)
class WhonixInfo:
    present: bool = False
    role: str = ""  # workstation | gateway | ""
    qubes: bool = False
    gateway_ip: str = ""
    tor_socks_host: str = "127.0.0.1"
    tor_socks_port: int = 9050
    notes: str = ""

    @property
    def is_workstation(self) -> bool:
        return self.present and self.role == "workstation"


def detect() -> WhonixInfo:
    role = _detect_role()
    if not role:
        host = (
            os.environ.get("SPECTRE_TOR_SOCKS_HOST")
            or os.environ.get("TOR_SOCKS_HOST")
            or ""
        ).strip()
        port = _port_from_env(9050)
        if host:
            return WhonixInfo(
                tor_socks_host=host,
                tor_socks_port=port,
                notes="Tor SOCKS host from environment",
            )
        return WhonixInfo()

    qubes = _is_qubes()
    gateway = _resolve_gateway(qubes)
    socks_host, socks_port = _resolve_tor_socks(role, gateway)
    notes = ""
    if role == "workstation":
        notes = (
            "Whonix-Workstation: Tor is the Gateway SOCKS. "
            "VPN hops that bypass Tor are blocked by the core by default."
        )
    elif role == "gateway":
        notes = "Whonix-Gateway detected — run Spectre on the Workstation."

    return WhonixInfo(
        present=True,
        role=role,
        qubes=qubes,
        gateway_ip=gateway,
        tor_socks_host=socks_host,
        tor_socks_port=socks_port,
        notes=notes,
    )


def system_tor_socks() -> tuple[str, int]:
    info = detect()
    return info.tor_socks_host, info.tor_socks_port


def _detect_role() -> str:
    env = os.environ.get("WHONIX", "").strip().lower()
    if env in ("1", "true", "yes", "workstation", "ws"):
        return "workstation"
    if env in ("gateway", "gw"):
        return "gateway"

    if Path("/usr/share/anon-gw-base-files/gateway").is_file() or Path(
        "/usr/share/whonix-gw-base-files/gateway"
    ).is_file():
        return "gateway"
    if Path("/usr/share/anon-ws-base-files/workstation").is_file() or Path(
        "/usr/share/whonix-ws-base-files/workstation"
    ).is_file():
        return "workstation"

    if Path("/etc/whonix_version").is_file() or Path("/usr/share/whonix").is_dir():
        host = os.uname().nodename.lower()
        if "gateway" in host or host.endswith("-gw") or "sys-whonix" in host:
            return "gateway"
        return "workstation"

    if _is_qubes():
        host = os.uname().nodename.lower()
        if "sys-whonix" in host or host.endswith("-gw"):
            return "gateway"
        if "anon-whonix" in host or "whonix" in host:
            return "workstation"
    return ""


def _is_qubes() -> bool:
    if os.environ.get("QUBES_VOLUME_GROUP") or os.environ.get("QUBES_BASEDIR"):
        return True
    return Path("/usr/share/qubes/marker-vm").is_file() or Path(
        "/usr/lib/qubes"
    ).is_dir()


def _resolve_gateway(qubes: bool) -> str:
    for key in ("SPECTRE_WHONIX_GATEWAY", "WHONIX_GATEWAY", "GATEWAY_IP"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    if qubes:
        qubesdb = shutil.which("qubesdb-read")
        if qubesdb:
            try:
                out = subprocess.check_output(
                    [qubesdb, "/qubes-gateway"],
                    text=True,
                    timeout=2,
                    stderr=subprocess.DEVNULL,
                )
                ip = out.strip()
                if ip:
                    return ip
            except (OSError, subprocess.SubprocessError):
                pass
    return DEFAULT_GATEWAY_IP


def _resolve_tor_socks(role: str, gateway: str) -> tuple[str, int]:
    port = _port_from_env(9050)
    for key in ("SPECTRE_TOR_SOCKS_HOST", "TOR_SOCKS_HOST"):
        v = os.environ.get(key, "").strip()
        if v:
            return v, port
    if role == "gateway":
        return "127.0.0.1", port
    if gateway:
        return gateway, port
    return "127.0.0.1", port


def _port_from_env(default: int) -> int:
    for key in ("SPECTRE_TOR_SOCKS_PORT", "TOR_SOCKS_PORT"):
        v = os.environ.get(key, "").strip()
        if v.isdigit():
            n = int(v)
            if 0 < n < 65536:
                return n
    return default
