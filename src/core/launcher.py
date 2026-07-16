"""Launch applications through the active Spectre path."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Mapping

from core.apps import RoutedApp
from core.client import CoreClient, CoreState


@dataclass
class LaunchResult:
    ok: bool
    message: str
    pid: int | None = None


def socks_urls(local_proxy: str) -> dict[str, str]:
    """Environment variables that route cooperative apps via Spectre SOCKS."""
    hostport = local_proxy.strip()
    if "://" in hostport:
        # already a URL
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
        # Hint for tools that look for this
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


def launch_app(app: RoutedApp, core: CoreClient) -> LaunchResult:
    """Start *app* through the current Spectre path if connected."""
    if not app.enabled:
        return LaunchResult(False, f"“{app.name}” is disabled")

    st = core.status()
    if st.state != CoreState.CONNECTED:
        return LaunchResult(
            False,
            "Connect a path first — apps route through the active Spectre SOCKS",
        )
    proxy = (st.local_proxy or "").strip()
    if not proxy:
        return LaunchResult(False, "Path is up but no local SOCKS address is available")

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
        # Write a tiny conf that points at Spectre SOCKS
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

    return LaunchResult(True, f"Launched “{app.name}” via {proxy}", pid=proc.pid)


def launch_command(argv: list[str], core: CoreClient, *, mode: str = "env") -> LaunchResult:
    """Launch an arbitrary command through the path (CLI helper)."""
    app = RoutedApp(
        id="adhoc",
        name=argv[0] if argv else "command",
        command=" ".join(argv),  # for display only; we use argv
        mode=mode,
    )
    # Bypass argv re-parse issues by temporary override
    st = core.status()
    if st.state != CoreState.CONNECTED or not st.local_proxy:
        return LaunchResult(False, "Connect a path first")
    env = build_launch_env(st.local_proxy)
    cmd = list(argv)
    if mode == "proxychains":
        pc = shutil.which("proxychains4") or shutil.which("proxychains")
        if not pc:
            return LaunchResult(False, "proxychains not found")
        conf = _proxychains_conf(st.local_proxy)
        cmd = [pc, "-q", "-f", conf, *cmd]
    try:
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        return LaunchResult(False, str(exc))
    return LaunchResult(True, f"pid {proc.pid}", pid=proc.pid)


def _proxychains_conf(local_proxy: str) -> str:
    from app_config import user_data_dir

    hostport = local_proxy
    if "://" in hostport:
        hostport = hostport.split("://", 1)[1]
    if ":" in hostport:
        host, port = hostport.rsplit(":", 1)
    else:
        host, port = hostport, "10808"
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
