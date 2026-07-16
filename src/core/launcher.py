"""Launch applications excluded from Spectre / tunnel (clearnet).

Contract (exclude-list split tunnel):
  - Default product model: entire system uses Spectre; apps opened here run
    *outside* the path (clearnet).
  - Preferred isolation: clearnet network namespace via ``clearnet-run``
    (veth ``cn-host``; marks Spectre KS/sysroute already honor).
  - Fallback: ``mullvad-exclude`` (setuid mark-based exclusion; Spectre
    skips the same marks).
  - Never runs ``clearnet-netns teardown`` (would kill every PID in the
    netns — including agents launched there).
  - Does not auto-``setup`` the netns from the GUI if missing (avoids
    destructive recreate); tells the user to set up once as root.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from core.apps import RoutedApp
from core.client import CoreClient, CoreState

# Match spectred default when status has no local_proxy yet.
DEFAULT_SOCKS_PORT = 10808

# Default clearnet netns name used by clearnet-run / clearnet-netns.
DEFAULT_CLEARNET_NS = "clearnet"

# Session / display vars to pass into clearnet-run --env (env -i inside).
_SESSION_ENV_KEYS = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_SESSION_TYPE",
    "XDG_CURRENT_DESKTOP",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "PULSE_SERVER",
    "PIPEWIRE_RUNTIME_DIR",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "GTK_IM_MODULE",
    "QT_IM_MODULE",
    "XMODIFIERS",
)

_PROXY_ENV_KEYS = (
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "FTP_PROXY",
    "ftp_proxy",
    "SOCKS_PROXY",
    "socks_proxy",
    "SOCKS5_PROXY",
    "socks5_proxy",
    "SPECTRE_PROXY",
    "SPECTRE_SOCKS",
)


@dataclass
class LaunchResult:
    ok: bool
    message: str
    pid: int | None = None
    method: str = ""  # clearnet-run | mullvad-exclude | socks | ""


@dataclass
class ExcludeTooling:
    """Read-only snapshot of exclude helpers (no side effects)."""

    clearnet_run: str | None = None
    mullvad_exclude: str | None = None
    netns_ready: bool = False
    netns_name: str = DEFAULT_CLEARNET_NS
    sudo_nopasswd: bool | None = None  # None = not probed

    @property
    def can_clearnet_run(self) -> bool:
        return bool(self.clearnet_run and self.netns_ready and self.sudo_nopasswd)

    @property
    def can_mullvad_exclude(self) -> bool:
        return bool(self.mullvad_exclude)

    @property
    def any_ready(self) -> bool:
        return self.can_clearnet_run or self.can_mullvad_exclude

    def summary(self) -> str:
        bits: list[str] = []
        if self.can_clearnet_run:
            bits.append(f"clearnet-run ({self.netns_name} netns)")
        elif self.clearnet_run and not self.netns_ready:
            bits.append(f"clearnet-run present · netns “{self.netns_name}” not ready")
        elif self.clearnet_run and self.sudo_nopasswd is False:
            bits.append("clearnet-run present · needs passwordless sudo")
        if self.can_mullvad_exclude:
            bits.append("mullvad-exclude")
        if not bits:
            return "no exclude helper"
        return " · ".join(bits)


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


def _first_executable(*candidates: str | None) -> str | None:
    for c in candidates:
        if not c:
            continue
        p = Path(c)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def clearnet_ns_name() -> str:
    return (os.environ.get("CLEARNET_NS") or DEFAULT_CLEARNET_NS).strip() or DEFAULT_CLEARNET_NS


def netns_exists(name: str | None = None) -> bool:
    """Read-only: is the clearnet netns registered? Never creates or tears down."""
    ns = (name or clearnet_ns_name()).strip() or DEFAULT_CLEARNET_NS
    for base in (Path("/run/netns"), Path("/var/run/netns")):
        if (base / ns).exists():
            return True
    return False


def find_clearnet_run() -> str | None:
    env = (os.environ.get("SPECTRE_CLEARNET_RUN") or "").strip()
    which = shutil.which("clearnet-run")
    return _first_executable(
        env or None,
        "/usr/local/libexec/spectre/clearnet-run",
        "/usr/libexec/spectre/clearnet-run",
        "/usr/local/bin/clearnet-run",
        which,
    )


def find_mullvad_exclude() -> str | None:
    env = (os.environ.get("SPECTRE_MULLVAD_EXCLUDE") or "").strip()
    which = shutil.which("mullvad-exclude")
    return _first_executable(env or None, "/usr/bin/mullvad-exclude", which)


def probe_sudo_nopasswd(*, timeout_sec: float = 1.5) -> bool:
    """True if ``sudo -n true`` succeeds (no password prompt). Non-destructive."""
    sudo = shutil.which("sudo")
    if not sudo:
        return False
    try:
        r = subprocess.run(  # noqa: S603
            [sudo, "-n", "true"],
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def probe_exclude_tooling(*, check_sudo: bool = True) -> ExcludeTooling:
    """Discover exclude helpers. Never runs netns setup/teardown."""
    t = ExcludeTooling(
        clearnet_run=find_clearnet_run(),
        mullvad_exclude=find_mullvad_exclude(),
        netns_ready=netns_exists(),
        netns_name=clearnet_ns_name(),
    )
    if check_sudo and t.clearnet_run and t.netns_ready:
        t.sudo_nopasswd = probe_sudo_nopasswd()
    elif t.clearnet_run:
        t.sudo_nopasswd = False if check_sudo else None
    return t


def _session_env_pairs() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for key in _SESSION_ENV_KEYS:
        val = os.environ.get(key)
        if val:
            out.append((key, val))
    return out


def _strip_proxy_env(env: dict[str, str]) -> dict[str, str]:
    for k in _PROXY_ENV_KEYS:
        env.pop(k, None)
    return env


def _build_clearnet_run_argv(clearnet_run: str, app_argv: list[str]) -> list[str]:
    """sudo -n clearnet-run [--env K=V ...] -- cmd...

    Never passes teardown. Never invokes clearnet-netns directly.
    """
    sudo = shutil.which("sudo") or "sudo"
    cmd: list[str] = [sudo, "-n", "--", clearnet_run]
    user = (os.environ.get("CLEARNET_USER") or os.environ.get("USER") or "").strip()
    if user:
        # clearnet-run uses SUDO_USER / CLEARNET_USER; export via env for child.
        # SUDO_USER is set by sudo automatically when invoked from a user session.
        pass
    for key, val in _session_env_pairs():
        cmd.extend(["--env", f"{key}={val}"])
    cmd.append("--")
    cmd.extend(app_argv)
    return cmd


def _build_mullvad_exclude_argv(mullvad_exclude: str, app_argv: list[str]) -> list[str]:
    return [mullvad_exclude, *app_argv]


def launch_app(
    app: RoutedApp,
    core: CoreClient,
    *,
    session: LaunchSession | None = None,
    bind_address: str = "127.0.0.1",
    tooling: ExcludeTooling | None = None,
) -> LaunchResult:
    """Start *app* excluded from Spectre / tunnel (clearnet).

    ``bind_address`` is accepted for API compatibility; exclude launch does
    not use SOCKS. SOCKS include is available via :func:`launch_app_socks`.
    """
    _ = core, bind_address
    if not app.enabled:
        return LaunchResult(False, f"“{app.name}” is disabled")

    try:
        app_argv = app.argv()
    except ValueError as exc:
        return LaunchResult(False, str(exc))

    tools = tooling if tooling is not None else probe_exclude_tooling(check_sudo=True)

    # 1) Preferred: clearnet netns
    if tools.clearnet_run and tools.netns_ready and tools.sudo_nopasswd:
        cmd = _build_clearnet_run_argv(tools.clearnet_run, app_argv)
        return _popen_exclude(
            cmd,
            app_name=app.name,
            method="clearnet-run",
            session=session,
            env=_launch_env_for_exclude(),
            detail=f"netns {tools.netns_name}",
        )

    # 2) Fallback: mark-based mullvad-exclude (Spectre honors same marks)
    if tools.mullvad_exclude:
        cmd = _build_mullvad_exclude_argv(tools.mullvad_exclude, app_argv)
        return _popen_exclude(
            cmd,
            app_name=app.name,
            method="mullvad-exclude",
            session=session,
            env=_launch_env_for_exclude(),
            detail="mark-based clearnet",
        )

    # Explain why we failed (no silent SOCKS fallback — that would re-include).
    return LaunchResult(False, _exclude_unavailable_message(tools))


def _launch_env_for_exclude() -> dict[str, str]:
    env = _strip_proxy_env(dict(os.environ))
    # Hint for debugging / child tooling
    env["SPECTRE_EXCLUDED"] = "1"
    return env


def _exclude_unavailable_message(tools: ExcludeTooling) -> str:
    parts = [
        "Cannot exclude app: no working clearnet helper.",
    ]
    if not tools.clearnet_run and not tools.mullvad_exclude:
        parts.append(
            "Install clearnet-run (/usr/local/bin) or mullvad-exclude, "
            "then ensure the clearnet netns exists."
        )
    else:
        if tools.clearnet_run and not tools.netns_ready:
            parts.append(
                f"Netns “{tools.netns_name}” is missing — once: "
                f"spectre setup-clearnet  (do not teardown while agents use it)."
            )
        if tools.clearnet_run and tools.netns_ready and tools.sudo_nopasswd is False:
            parts.append(
                "Passwordless sudo missing for clearnet-run — run: spectre setup-clearnet"
            )
        if not tools.mullvad_exclude:
            parts.append("Optional fallback: install Mullvad CLI (mullvad-exclude).")
    return " ".join(parts)


def _popen_exclude(
    cmd: list[str],
    *,
    app_name: str,
    method: str,
    session: LaunchSession | None,
    env: dict[str, str],
    detail: str,
) -> LaunchResult:
    try:
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        return LaunchResult(False, f"Command not found: {cmd[0]}", method=method)
    except OSError as exc:
        return LaunchResult(False, f"Launch failed: {exc}", method=method)

    # Brief poll: catch immediate sudo / helper failures without blocking
    # the GUI or killing anything. Do not wait() for long-lived apps.
    ret = None
    for _ in range(8):
        ret = proc.poll()
        if ret is not None:
            break
        time.sleep(0.05)

    if ret is not None and ret != 0:
        err = ""
        try:
            if proc.stderr is not None:
                err = (proc.stderr.read() or b"").decode("utf-8", errors="replace").strip()
        except OSError:
            err = ""
        msg = f"Exclude launch failed ({method}, exit {ret})"
        if err:
            msg = f"{msg}: {err[:200]}"
        return LaunchResult(False, msg, method=method)

    # Drop stderr handle so a chatty child cannot fill a pipe
    try:
        if proc.stderr is not None:
            proc.stderr.close()
    except OSError:
        pass

    if session is not None and proc.pid:
        session.track(proc.pid, app_name)

    msg = f"Excluded “{app_name}” · {method}"
    if detail:
        msg += f" · {detail}"
    return LaunchResult(True, msg, pid=proc.pid, method=method)


# ── Optional SOCKS include (CLI / advanced; not the Apps page default) ─────


def resolve_socks_hostport(
    core: CoreClient,
    *,
    bind_address: str = "127.0.0.1",
) -> tuple[str, bool]:
    """Return (host:port, path_up)."""
    st = core.status()
    proxy = (st.local_proxy or "").strip()
    path_up = st.state == CoreState.CONNECTED and bool(proxy)
    if proxy:
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


def launch_app_socks(
    app: RoutedApp,
    core: CoreClient,
    *,
    session: LaunchSession | None = None,
    bind_address: str = "127.0.0.1",
) -> LaunchResult:
    """Start *app* via Spectre SOCKS (include). Not used by the Apps exclude page."""
    if not app.enabled:
        return LaunchResult(False, f"“{app.name}” is disabled", method="socks")

    proxy, path_up = resolve_socks_hostport(core, bind_address=bind_address)

    try:
        argv = app.argv()
    except ValueError as exc:
        return LaunchResult(False, str(exc), method="socks")

    mode = app.mode if app.mode in ("env", "proxychains") else "env"
    env = build_launch_env(proxy)

    if mode == "proxychains":
        pc = shutil.which("proxychains4") or shutil.which("proxychains")
        if not pc:
            return LaunchResult(
                False,
                "proxychains not found — install proxychains-ng or use env mode",
                method="socks",
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
        return LaunchResult(False, f"Command not found: {argv[0]}", method="socks")
    except OSError as exc:
        return LaunchResult(False, f"Launch failed: {exc}", method="socks")

    if session is not None and proc.pid:
        session.track(proc.pid, app.name)

    if path_up:
        msg = f"On path “{app.name}” · SOCKS {proxy}"
    else:
        msg = (
            f"On path “{app.name}” · SOCKS {proxy} "
            f"(path down — network waits until you Connect)"
        )
    return LaunchResult(True, msg, pid=proc.pid, method="socks")


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
            return LaunchResult(False, "proxychains not found", method="socks")
        conf = _proxychains_conf(proxy)
        cmd = [pc, "-q", "-f", conf, *cmd]
    try:
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        return LaunchResult(False, str(exc), method="socks")
    name = argv[0] if argv else "command"
    if session is not None and proc.pid:
        session.track(proc.pid, name)
    return LaunchResult(True, f"pid {proc.pid}", pid=proc.pid, method="socks")


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
