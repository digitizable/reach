"""Official Mullvad VPN Linux CLI integration for Spectre Desktop.

Spectre does not replace the Mullvad app. This module probes status and can
request connect/disconnect via the ``mullvad`` CLI, matching the core package
github.com/digitizable/spectre/internal/mullvad.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from dataclasses import dataclass

DEFAULT_SOCKS_HOST = "10.64.0.1"
DEFAULT_SOCKS_PORT = 1080


@dataclass
class MullvadStatus:
    available: bool = False
    connected: bool = False
    relay: str = ""
    location: str = ""
    version: str = ""
    socks_host: str = DEFAULT_SOCKS_HOST
    socks_port: int = DEFAULT_SOCKS_PORT
    socks_reachable: bool = False
    summary: str = "Mullvad unknown"
    error: str = ""

    @property
    def ready_for_socks_hop(self) -> bool:
        return self.connected and self.socks_reachable


def cli_path() -> str | None:
    return shutil.which("mullvad")


def _tcp_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _run(*args: str, timeout: float = 8.0) -> tuple[int, str]:
    exe = cli_path()
    if not exe:
        return 127, "mullvad not found"
    try:
        proc = subprocess.run(  # noqa: S603
            [exe, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode, out


def probe() -> MullvadStatus:
    st = MullvadStatus()
    if not cli_path():
        st.summary = "Mullvad CLI not installed"
        st.error = "mullvad not found in PATH"
        return st
    st.available = True
    code, out = _run("status", timeout=3.0)
    if code != 0 and not out:
        st.summary = "Mullvad status failed"
        st.error = "status failed"
        st.socks_reachable = _tcp_open(st.socks_host, st.socks_port)
        return st
    lines = out.splitlines()
    head = (lines[0] if lines else "").strip().lower()
    if head.startswith("connected"):
        st.connected = True
    elif head.startswith("disconnected"):
        st.connected = False
    for line in lines:
        line = line.strip()
        if line.startswith("Relay:"):
            st.relay = line.split(":", 1)[1].strip()
        if line.startswith("Visible location:"):
            st.location = line.split(":", 1)[1].strip()
    st.socks_reachable = _tcp_open(st.socks_host, st.socks_port)
    if st.connected and st.socks_reachable:
        st.summary = "Mullvad Connected"
        if st.relay:
            st.summary += f" · {st.relay}"
    elif st.connected:
        st.summary = "Mullvad Connected (SOCKS not ready)"
    else:
        st.summary = "Mullvad Disconnected"
    code_v, ver = _run("version", timeout=2.0)
    if code_v == 0 and ver:
        line = ver.splitlines()[0].strip()
        if ":" in line:
            st.version = line.rsplit(":", 1)[-1].strip()
        else:
            st.version = line
    return st


def connect() -> tuple[bool, str]:
    """Request Mullvad connect. Returns (ok, message)."""
    if not cli_path():
        return False, "Mullvad CLI not installed"
    code, out = _run("connect", timeout=15.0)
    if code != 0:
        return False, out or "mullvad connect failed"
    return True, "Mullvad connect requested"


def disconnect() -> tuple[bool, str]:
    if not cli_path():
        return False, "Mullvad CLI not installed"
    code, out = _run("disconnect", timeout=15.0)
    if code != 0:
        return False, out or "mullvad disconnect failed"
    return True, "Mullvad disconnect requested"


def ensure_connected(*, timeout_sec: float = 45.0) -> MullvadStatus:
    """Connect if needed and wait until SOCKS is ready."""
    st = probe()
    if st.ready_for_socks_hop:
        return st
    if not st.available:
        return st
    ok, msg = connect()
    if not ok:
        st = probe()
        st.error = msg
        st.summary = msg
        return st
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        st = probe()
        if st.ready_for_socks_hop:
            return st
        time.sleep(0.4)
    st = probe()
    if not st.error:
        st.error = "Mullvad did not become ready in time"
        st.summary = st.error
    return st
