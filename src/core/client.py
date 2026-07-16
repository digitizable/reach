"""Thin client for the Spectre core over a Unix-socket HTTP API."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from http.client import HTTPConnection
from pathlib import Path
from typing import Any


class CoreState(str, Enum):
    UNAVAILABLE = "unavailable"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


@dataclass
class CoreStatus:
    state: CoreState = CoreState.UNAVAILABLE
    message: str = "Spectre core is not running"
    active_profile: str | None = None
    profile_id: str | None = None
    path_summary: str = "No path"
    hops: list[str] = field(default_factory=list)
    hop_details: list[dict[str, Any]] = field(default_factory=list)
    local_proxy: str = ""
    dns_ok: bool | None = None
    leak_guard: bool | None = None
    kill_switch: bool | None = None
    kill_switch_active: bool | None = None
    kill_switch_detail: str = ""
    routing_mode: str = ""
    routing_active: bool | None = None
    routing_detail: str = ""
    mullvad: dict[str, Any] | None = None
    last_payload: dict[str, Any] | None = None
    environment: dict[str, Any] | None = None


# Health probes must stay short so UI refresh never freezes the main thread.
_HEALTH_TIMEOUT = 0.4
_START_POLL_TIMEOUT = 0.35
# Status can include a cached Mullvad probe; 0.5s was too tight and caused
# intermittent UNAVAILABLE → home flicker (“Offline” blink).
_STATUS_TIMEOUT = 1.5
_STATUS_CACHE_TTL = 0.5  # seconds — coalesce rapid chrome/tray/page polls
# Keep showing last successful status briefly if a single poll fails (socket
# timeout / brief restart) so the dashboard does not blink Offline.
_STATUS_STICKY_SEC = 8.0


def default_socket_path() -> str:
    """Match spectred default: $XDG_RUNTIME_DIR/spectre/spectre.sock."""
    env = os.environ.get("SPECTRE_SOCKET", "").strip()
    if env:
        return env
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime:
        return str(Path(runtime) / "spectre" / "spectre.sock")
    return str(Path.home() / ".local" / "share" / "spectre" / "run" / "spectre.sock")


class _UnixHTTPConnection(HTTPConnection):
    def __init__(self, unix_path: str, timeout: float | None = None) -> None:
        super().__init__("localhost", timeout=timeout)
        self._unix_path = unix_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if self.timeout is not None:
            sock.settimeout(self.timeout)
        sock.connect(self._unix_path)
        self.sock = sock


def _request(
    unix_path: str,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    token: str = "",
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any]]:
    raw = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if raw is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    conn = _UnixHTTPConnection(unix_path, timeout=timeout)
    try:
        conn.request(method, path, body=raw, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        try:
            parsed: dict[str, Any] = json.loads(data.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            parsed = {"raw": data.decode("utf-8", errors="replace")}
        return resp.status, parsed
    finally:
        conn.close()


def _parse_state(value: str | None) -> CoreState:
    if not value:
        return CoreState.DISCONNECTED
    try:
        return CoreState(value)
    except ValueError:
        return CoreState.DISCONNECTED


def _status_from_json(
    data: dict[str, Any], *, last_payload: dict[str, Any] | None
) -> CoreStatus:
    env = data.get("environment")
    ks = data.get("kill_switch")
    ksa = data.get("kill_switch_active")
    ra = data.get("routing_active")
    mv = data.get("mullvad")
    raw_details = data.get("hop_details") or []
    hop_details: list[dict[str, Any]] = (
        [d for d in raw_details if isinstance(d, dict)]
        if isinstance(raw_details, list)
        else []
    )
    return CoreStatus(
        state=_parse_state(str(data.get("state") or "")),
        message=str(data.get("message") or ""),
        active_profile=data.get("active_profile") or None,
        profile_id=data.get("profile_id") or None,
        path_summary=str(data.get("path_summary") or "No path"),
        hops=list(data.get("hops") or []),
        hop_details=hop_details,
        local_proxy=str(data.get("local_proxy") or ""),
        dns_ok=data.get("dns_ok"),
        leak_guard=data.get("leak_guard"),
        kill_switch=ks if isinstance(ks, bool) else None,
        kill_switch_active=ksa if isinstance(ksa, bool) else None,
        kill_switch_detail=str(data.get("kill_switch_detail") or ""),
        routing_mode=str(data.get("routing_mode") or ""),
        routing_active=ra if isinstance(ra, bool) else None,
        routing_detail=str(data.get("routing_detail") or ""),
        mullvad=mv if isinstance(mv, dict) else None,
        last_payload=last_payload,
        environment=env if isinstance(env, dict) else None,
    )


class CoreClient:
    """Talks to spectred over the local Unix-socket HTTP API."""

    def __init__(
        self,
        socket_path: str = "",
        *,
        timeout_sec: int = 10,
        api_token: str = "",
    ) -> None:
        self.socket_path = (socket_path or default_socket_path()).strip()
        self.timeout_sec = timeout_sec
        self.api_token = api_token
        self._selected_profile: str | None = None
        self._status = CoreStatus()
        self._last_payload: dict[str, Any] | None = None
        self._status_cache: CoreStatus | None = None
        self._status_cache_at: float = 0.0
        self._last_good_status: CoreStatus | None = None
        self._last_good_at: float = 0.0

    def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> tuple[int, dict[str, Any]]:
        return _request(
            self.socket_path,
            method,
            path,
            body=body,
            token=self.api_token,
            timeout=float(self.timeout_sec if timeout is None else timeout),
        )

    def _core_reachable(self) -> bool:
        if not self.socket_path or not Path(self.socket_path).exists():
            return False
        try:
            code, data = self._call("GET", "/v1/health", timeout=_HEALTH_TIMEOUT)
            return code == 200 and data.get("status") == "ok"
        except (OSError, TimeoutError, ConnectionError, socket.timeout):
            return False

    def ensure_running(self, *, try_start: bool = True) -> bool:
        """Return True if core is healthy; optionally start spectred once."""
        if self._core_reachable():
            return True
        if not try_start:
            return False
        if not self._try_start_daemon():
            return False
        # Poll briefly with short timeouts (never 10s × N on the UI thread).
        for _ in range(20):
            if self._core_reachable():
                return True
            time.sleep(0.1)
        return self._core_reachable()

    def _try_start_daemon(self) -> bool:
        candidates: list[list[str]] = []

        # systemctl --user first
        if _which("systemctl"):
            candidates.append(
                ["systemctl", "--user", "start", "spectred.service"]
            )

        for name in ("spectre", "spectred"):
            path = _which(name)
            if path and name == "spectre":
                candidates.append([path, "start"])
            elif path and name == "spectred":
                candidates.append([path, "-socket", self.socket_path])

        home = Path.home()
        for base in (home / ".local" / "bin", Path("/usr/local/bin")):
            sp = base / "spectre"
            if sp.is_file():
                candidates.append([str(sp), "start"])
            sd = base / "spectred"
            if sd.is_file():
                candidates.append([str(sd), "-socket", self.socket_path])

        try:
            from app_config import project_root

            neighbour = project_root().parent / "spectre" / "bin" / "spectred"
            if neighbour.is_file():
                candidates.append([str(neighbour), "-socket", self.socket_path])
            neighbour_cli = project_root().parent / "spectre" / "bin" / "spectre"
            if neighbour_cli.is_file():
                candidates.append([str(neighbour_cli), "start"])
        except Exception:
            pass

        seen: set[str] = set()
        for cmd in candidates:
            key = " ".join(cmd)
            if key in seen:
                continue
            seen.add(key)
            try:
                if cmd[0] == "systemctl" or (
                    len(cmd) > 1 and cmd[1] == "start" and not cmd[0].endswith("spectred")
                ):
                    # systemctl start / spectre start — short timeout
                    subprocess.run(  # noqa: S603
                        cmd,
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                    return True
                if cmd[0].endswith("spectred") or (
                    len(cmd) > 1 and cmd[1] == "-socket"
                ):
                    subprocess.Popen(  # noqa: S603
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    return True
                subprocess.run(  # noqa: S603
                    cmd,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                return True
            except (OSError, subprocess.SubprocessError):
                continue
        return False

    def _remember_good(self, st: CoreStatus, *, now: float) -> CoreStatus:
        if st.state != CoreState.UNAVAILABLE:
            self._last_good_status = st
            self._last_good_at = now
        self._status = st
        self._status_cache = st
        self._status_cache_at = now
        return st

    def _sticky_or_offline(
        self,
        offline: CoreStatus,
        *,
        now: float,
        reason: str = "",
    ) -> CoreStatus:
        """Prefer last good status for a short window after a blip."""
        if (
            self._last_good_status is not None
            and (now - self._last_good_at) < _STATUS_STICKY_SEC
        ):
            # Reuse last good; keep cache warm so we do not thrash.
            self._status = self._last_good_status
            self._status_cache = self._last_good_status
            self._status_cache_at = now
            return self._last_good_status
        _ = reason
        return self._remember_good(offline, now=now)

    def refresh(self, *, force: bool = False) -> CoreStatus:
        """Fetch core status. Uses a short TTL cache so UI page switches stay snappy."""
        now = time.monotonic()
        if (
            not force
            and self._status_cache is not None
            and (now - self._status_cache_at) < _STATUS_CACHE_TTL
        ):
            return self._status_cache

        profile = self._selected_profile
        # Prefer a single /v1/status call (includes health-ish liveness). Avoid a
        # separate /health round-trip on every chrome update.
        if not self.socket_path or not Path(self.socket_path).exists():
            offline = CoreStatus(
                state=CoreState.UNAVAILABLE,
                message="Spectre core is not running",
                active_profile=profile,
                path_summary="No path — core offline",
                hops=[],
                local_proxy="",
                dns_ok=None,
                leak_guard=None,
                last_payload=self._last_payload,
            )
            # Missing socket can be a brief restart; sticky if we were just fine.
            return self._sticky_or_offline(offline, now=now, reason="no-socket")
        try:
            code, data = self._call(
                "GET", "/v1/status", timeout=_STATUS_TIMEOUT
            )
            if code != 200:
                msg = _error_message(data) or f"status HTTP {code}"
                offline = CoreStatus(
                    state=CoreState.UNAVAILABLE,
                    message=msg,
                    active_profile=profile,
                    path_summary="No path",
                    hops=[],
                    last_payload=self._last_payload,
                )
                return self._sticky_or_offline(offline, now=now, reason="http")
            st = _status_from_json(data, last_payload=self._last_payload)
            if st.active_profile:
                self._selected_profile = st.active_profile
            return self._remember_good(st, now=now)
        except (OSError, TimeoutError, ConnectionError, socket.timeout) as exc:
            offline = CoreStatus(
                state=CoreState.UNAVAILABLE,
                message=f"Core unreachable: {exc}",
                active_profile=profile,
                path_summary="No path — core offline",
                hops=[],
                last_payload=self._last_payload,
            )
            return self._sticky_or_offline(offline, now=now, reason="error")

    def status(self, *, force: bool = False) -> CoreStatus:
        return self.refresh(force=force)

    def invalidate_status_cache(self) -> None:
        self._status_cache = None
        self._status_cache_at = 0.0

    def set_selected_profile(self, name: str | None) -> None:
        self._selected_profile = name

    def selected_profile(self) -> str | None:
        return self._selected_profile

    def connect(
        self,
        profile: str | None = None,
        *,
        payload: dict[str, Any] | None = None,
    ) -> CoreStatus:
        if profile:
            self._selected_profile = profile
        self._last_payload = payload

        if not self.ensure_running(try_start=True):
            return self.refresh()

        if payload is None:
            payload = {
                "profile_id": "",
                "profile_name": profile or self._selected_profile or "",
                "hops": [],
                "policy": {},
            }
        self.invalidate_status_cache()
        try:
            code, data = self._call("POST", "/v1/connect", payload)
            if code >= 400:
                msg = _error_message(data) or f"connect failed ({code})"
                self._status = CoreStatus(
                    state=CoreState.DISCONNECTED,
                    message=msg,
                    active_profile=profile or self._selected_profile,
                    path_summary="Connect failed",
                    hops=[],
                    last_payload=self._last_payload,
                )
                return self._status
            self._status = _status_from_json(data, last_payload=self._last_payload)
            return self._status
        except (OSError, TimeoutError, ConnectionError, socket.timeout) as exc:
            self._status = CoreStatus(
                state=CoreState.UNAVAILABLE,
                message=f"Connect failed: {exc}",
                active_profile=profile or self._selected_profile,
                path_summary="No path — core offline",
                hops=[],
                last_payload=self._last_payload,
            )
            return self._status

    def disconnect(self) -> CoreStatus:
        self._last_payload = None
        self.invalidate_status_cache()
        if not self.socket_path or not Path(self.socket_path).exists():
            return self.refresh(force=True)
        try:
            # Allow time for nft unlock (sudo helper) so disconnect never
            # returns while system routing is still redirecting to dead ports.
            code, data = self._call(
                "POST",
                "/v1/disconnect",
                timeout=min(20.0, max(8.0, float(self.timeout_sec))),
            )
            if code >= 400:
                return self.refresh(force=True)
            self._status = _status_from_json(data, last_payload=None)
            self._status_cache = self._status
            self._status_cache_at = time.monotonic()
            return self._status
        except (OSError, TimeoutError, ConnectionError, socket.timeout):
            return self.refresh(force=True)


def _error_message(data: dict[str, Any]) -> str:
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or "")
    return str(data.get("message") or "")


def _which(name: str) -> str | None:
    paths = os.environ.get("PATH", "").split(os.pathsep)
    paths.extend(
        [
            str(Path.home() / ".local" / "bin"),
            "/usr/local/bin",
        ]
    )
    for directory in paths:
        if not directory:
            continue
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None
