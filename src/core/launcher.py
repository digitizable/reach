"""Launch applications pointed at Spectre’s local SOCKS entry.

Contract:
  - Apps opened from the Apps page get SOCKS / proxychains env aimed at
    Spectre’s local proxy (live address when connected, else the usual
    bind:10808 so you can open in anticipation of Connect).
  - While the path is up, cooperative apps use Spectre.
  - While disconnected, that SOCKS is down — those apps keep running but
    network via the proxy fails (no process kill, no forced clearnet).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Mapping

from core.apps import RoutedApp
from core.client import CoreClient, CoreState

# Match spectred default when status has no local_proxy yet.
DEFAULT_SOCKS_PORT = 10808


@dataclass
class LaunchResult:
    ok: bool
    message: str
    pid: int | None = None


@dataclass
class _Tracked:
    pid: int
    name: str


@dataclass
class LaunchSession:
    """Processes started via Apps (for status counts only — not killed)."""

    _items: list[_Tracked] = field(default_factory=list)

    def track(self, pid: int, name: str) -> None:
        if pid <= 0:
            return
        self._items.append(_Tracked(pid=pid, name=name or f"pid {pid}"))

    def active_count(self) -> int:
        self._reap()
        return len(self._items)

    def names(self) -> list[str]:
        self._reap()
        return [t.name for t in self._items]

    def _reap(self) -> None:
        self._items = [t for t in self._items if _pid_alive(t.pid)]


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def resolve_socks_hostport(
    core: CoreClient,
    *,
    bind_address: str = "127.0.0.1",
) -> tuple[str, bool]:
    """Return (host:port, path_up).

    Prefer the live local_proxy from the core when connected; otherwise the
    configured bind address and default SOCKS port so apps can be pre-launched.
    """
    st = core.status()
    proxy = (st.local_proxy or "").strip()
    path_up = st.state == CoreState.CONNECTED and bool(proxy)
    if proxy:
        # Strip scheme if a URL leaked in
        if "://" in proxy:
            proxy = proxy.split("://", 1)[1]
        return proxy, path_up
    host = (bind_address or "127.0.0.1").strip() or "127.0.0.1"
    return f"{host}:{DEFAULT_SOCKS_PORT}", False


def socks_urls(local_proxy: str) -> dict[str, str]:
    """Environment variables that route cooperative apps via Spectre SOCKS."""
    hostport = local_proxy.strip()
    if "://" in hostport:
        url = hostport
    else:
        url = f"socks5h://{hostport}"
    no_proxy = "localhost,127.0.0.1,::1,.local"
    return {
        "ALL_PROXY": url,
        "all_proxy": url,
        "HTTP_PROXY": url,
        "HTTPS_PROXY": url,
        "http_proxy": url,
        "https_proxy": url,
        "FTP_PROXY": url,
        "ftp_proxy": url,
        "SOCKS_PROXY": url,
        "socks_proxy": url,
        "SOCKS5_PROXY": url,
        "socks5_proxy": url,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
        "SPECTRE_PROXY": url,
        "SPECTRE_SOCKS": hostport.split("://")[-1] if "://" in hostport else hostport,
    }


def build_launch_env(
    local_proxy: str,
    *,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = dict(base if base is not None else os.environ)
    env.update(socks_urls(local_proxy))
    return env


def launch_app(
    app: RoutedApp,
    core: CoreClient,
    *,
    session: LaunchSession | None = None,
    bind_address: str = "127.0.0.1",
) -> LaunchResult:
    """Start *app* with env aimed at Spectre SOCKS (connected or not)."""
    if not app.enabled:
        return LaunchResult(False, f"“{app.name}” is disabled")

    proxy, path_up = resolve_socks_hostport(core, bind_address=bind_address)

    try:
        argv = app.argv()
    except ValueError as exc:
        return LaunchResult(False, str(exc))

    mode = app.mode if app.mode in ("env", "proxychains") else "env"
    env = build_launch_env(proxy)

    if mode == "proxychains":
        pc = shutil.which("proxychains4") or shutil.which("proxychains")
        if not pc:
            return LaunchResult(
                False,
                "proxychains not found — install proxychains-ng or use env mode",
            )
        conf = _proxychains_conf(proxy)
        argv = [pc, "-q", "-f", conf, *argv]

    try:
        proc = subprocess.Popen(  # noqa: S603
            argv,
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return LaunchResult(False, f"Command not found: {argv[0]}")
    except OSError as exc:
        return LaunchResult(False, f"Launch failed: {exc}")

    if session is not None and proc.pid:
        session.track(proc.pid, app.name)

    if path_up:
        msg = f"Opened “{app.name}” via Spectre SOCKS {proxy}"
    else:
        msg = (
            f"Opened “{app.name}” via SOCKS {proxy} "
            f"(path down — network waits until you Connect)"
        )
    return LaunchResult(True, msg, pid=proc.pid)


def launch_command(
    argv: list[str],
    core: CoreClient,
    *,
    mode: str = "env",
    session: LaunchSession | None = None,
    bind_address: str = "127.0.0.1",
) -> LaunchResult:
    """Launch an arbitrary command through Spectre SOCKS (CLI helper)."""
    proxy, _path_up = resolve_socks_hostport(core, bind_address=bind_address)
    env = build_launch_env(proxy)
    cmd = list(argv)
    if mode == "proxychains":
        pc = shutil.which("proxychains4") or shutil.which("proxychains")
        if not pc:
            return LaunchResult(False, "proxychains not found")
        conf = _proxychains_conf(proxy)
        cmd = [pc, "-q", "-f", conf, *cmd]
    try:
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        return LaunchResult(False, str(exc))
    name = argv[0] if argv else "command"
    if session is not None and proc.pid:
        session.track(proc.pid, name)
    return LaunchResult(True, f"pid {proc.pid}", pid=proc.pid)


def _proxychains_conf(local_proxy: str) -> str:
    from app_config import user_data_dir

    hostport = local_proxy
    if "://" in hostport:
        hostport = hostport.split("://", 1)[1]
    if ":" in hostport:
        host, port = hostport.rsplit(":", 1)
    else:
        host, port = hostport, str(DEFAULT_SOCKS_PORT)
    path = user_data_dir() / "proxychains-spectre.conf"
    path.write_text(
        "strict_chain\n"
        "proxy_dns\n"
        "remote_dns_subnet 224\n"
        "tcp_read_time_out 15000\n"
        "tcp_connect_time_out 8000\n"
        "[ProxyList]\n"
        f"socks5 {host} {port}\n",
        encoding="utf-8",
    )
    return str(path)
