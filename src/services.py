"""App-level services: settings + core client + profiles + backends.

Desktop pieces are fully functional. Core connect remains a stub until a
Spectre core process is available; the desktop still validates paths and
persists a connect payload shape the core can consume later.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from app_config import user_config_dir
from core.apps import AppStore
from core.backends import BackendStore
from core.client import CoreClient, CoreStatus, default_socket_path
from core.desktop_log import write_log
from core.profiles import Profile, ProfileStore
from core.readiness import Readiness, profile_readiness


@dataclass
class AppConfig:
    """Desktop preferences that will map to Spectre core when wired."""

    # Core connection (empty socket → platform default at runtime)
    core_socket: str = ""
    core_timeout_sec: int = 10
    reconnect_auto: bool = True
    reconnect_delay_sec: int = 3

    # Session / path
    last_profile_id: str = ""
    auto_connect: bool = False
    start_minimized: bool = False

    # Network hygiene
    kill_switch: bool = True
    block_ipv6: bool = True
    dns_mode: str = "remote"  # system | remote | custom
    dns_servers: str = "1.1.1.1, 9.9.9.9"
    leak_guard: bool = True
    allow_lan: bool = False

    # Privacy extras (desktop policy; core enforces when present)
    block_webrtc: bool = True
    block_udp_non_tunnel: bool = False

    # Logging / diagnostics
    log_level: str = "info"  # error | warn | info | debug
    log_to_file: bool = True
    notify_on_disconnect: bool = True

    # Advanced
    mtu: int = 1280
    bind_address: str = "127.0.0.1"
    api_token: str = ""

    @classmethod
    def load(cls, path: Path) -> AppConfig:
        if not path.is_file():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in raw.items() if k in known}
        try:
            return cls(**data)
        except TypeError:
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), indent=2) + "\n",
            encoding="utf-8",
        )


@dataclass
class Services:
    config: AppConfig = field(default_factory=AppConfig)
    core: CoreClient = field(default_factory=CoreClient)
    profiles: ProfileStore = field(default_factory=ProfileStore)
    backends: BackendStore = field(default_factory=BackendStore)
    apps: AppStore = field(default_factory=AppStore)
    _config_path: Path = field(default_factory=lambda: user_config_dir() / "config.json")

    @classmethod
    def create(cls) -> Services:
        path = user_config_dir() / "config.json"
        config = AppConfig.load(path)
        socket = config.core_socket.strip() or default_socket_path()
        core = CoreClient(
            socket_path=socket,
            timeout_sec=config.core_timeout_sec,
            api_token=config.api_token,
        )
        profiles = ProfileStore()
        backends = BackendStore()
        apps = AppStore()
        # Keep hop bindings honest against the backend catalog.
        profiles.reconcile_backends(backends)
        if config.last_profile_id:
            p = profiles.get(config.last_profile_id)
            if p is not None:
                core.set_selected_profile(p.name)
            else:
                config.last_profile_id = ""
        svc = cls(
            config=config,
            core=core,
            profiles=profiles,
            backends=backends,
            apps=apps,
            _config_path=path,
        )
        svc.log("Services ready")
        return svc

    def log(self, message: str, *, level: str = "info") -> None:
        write_log(
            message,
            enabled=self.config.log_to_file,
            level=level,
        )

    def save_config(self) -> None:
        self.config.save(self._config_path)
        self.core.socket_path = self.config.core_socket.strip() or default_socket_path()
        self.core.timeout_sec = self.config.core_timeout_sec
        self.core.api_token = self.config.api_token
        self.log("Config saved")

    def active_profile(self) -> Profile | None:
        cfg_id = self.config.last_profile_id
        if cfg_id:
            p = self.profiles.get(cfg_id)
            if p is not None:
                return p
        name = self.core.selected_profile()
        if name:
            return self.profiles.by_name(name)
        profiles = self.profiles.list()
        return profiles[0] if profiles else None

    def set_active_profile(self, profile_id: str) -> Profile | None:
        profile = self.profiles.get(profile_id)
        if profile is None:
            return None
        self.config.last_profile_id = profile.id
        self.core.set_selected_profile(profile.name)
        self.save_config()
        self.log(f"Active profile → {profile.name}")
        return profile

    def readiness(self) -> Readiness:
        return profile_readiness(self.active_profile(), self.backends)

    def build_connect_payload(self, profile: Profile) -> dict[str, Any]:
        """Anticipated core payload — fully built on the desktop today."""
        return {
            "profile_id": profile.id,
            "profile_name": profile.name,
            "hops": [
                {
                    "kind": h.kind,
                    "backend_id": h.backend_id,
                    "backend": (
                        asdict(b) if (b := self.backends.get(h.backend_id)) else None
                    ),
                }
                for h in profile.hops
            ],
            "policy": {
                "kill_switch": self.config.kill_switch,
                "block_ipv6": self.config.block_ipv6,
                "dns_mode": self.config.dns_mode,
                "dns_servers": self.config.dns_servers,
                "leak_guard": self.config.leak_guard,
                "allow_lan": self.config.allow_lan,
                "block_webrtc": self.config.block_webrtc,
                "block_udp_non_tunnel": self.config.block_udp_non_tunnel,
                "mtu": self.config.mtu,
                "bind_address": self.config.bind_address,
                "reconnect_auto": self.config.reconnect_auto,
                "reconnect_delay_sec": self.config.reconnect_delay_sec,
            },
        }

    def connect_active(self) -> tuple[CoreStatus | None, Readiness]:
        """Validate locally, then hand off to core (stub until core exists)."""
        profile = self.active_profile()
        ready = profile_readiness(profile, self.backends)
        if not ready.ok:
            self.log(f"Connect blocked: {ready.summary}", level="warn")
            return None, ready
        assert profile is not None
        self.config.last_profile_id = profile.id
        self.core.set_selected_profile(profile.name)
        self.save_config()
        payload = self.build_connect_payload(profile)
        status = self.core.connect(profile.name, payload=payload)
        self.log(
            f"Connect handoff profile={profile.name} hops={len(profile.hops)} "
            f"core={status.state.value}"
        )
        return status, ready

    def disconnect(self) -> CoreStatus:
        status = self.core.disconnect()
        self.log("Disconnect requested")
        return status
