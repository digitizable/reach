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
from core.client import CoreClient, CoreState, CoreStatus, default_socket_path
from core.desktop_log import write_log
from core.launcher import LaunchSession
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
    # system = full-machine TCP/DNS via path (default); Exclude apps = clearnet carve-outs
    # apps = no system redirect; only SOCKS clients use the path
    routing_mode: str = "system"  # system | apps
    kill_switch: bool = True  # only enforced in system mode
    block_ipv6: bool = True
    dns_mode: str = "remote"  # system | remote | custom
    # Prefer Mullvad tunnel DNS (UDP 10.64.0.1). Public resolvers are TCP
    # fallback only — using them alone makes mullvad.net/check report a DNS "leak".
    dns_servers: str = "10.64.0.1"
    leak_guard: bool = True
    allow_lan: bool = False

    # Privacy extras (desktop policy; core enforces when present)
    block_webrtc: bool = True
    block_udp_non_tunnel: bool = False

    # Logging / diagnostics
    log_level: str = "info"  # error | warn | info | debug
    log_to_file: bool = True
    notify_on_disconnect: bool = True

    # Updates (GitHub Releases for digitizable/reach)
    check_for_updates: bool = True
    update_check_interval_hours: int = 24
    last_update_check: str = ""  # ISO-8601 UTC of last attempt
    dismissed_update_version: str = ""  # do not re-prompt for this latest

    # Mullvad (official CLI integration)
    mullvad_auto_connect: bool = True  # connect Mullvad if SOCKS hop needs it

    # Tray applet
    tray_enabled: bool = True
    close_to_tray: bool = True

    # Built-in packs (empty = privacy / core path console only)
    # Built-in packs only — see core.plugins.PLUGINS (fingerprint, lab).
    # Marketplace plugins (Hogwarts, …) use plugins_disabled when off.
    plugins_enabled: list[str] = field(default_factory=list)

    # Installed marketplace plugins (filesystem) that are turned OFF but kept.
    # Missing from this list ⇒ enabled. Built-in packs use plugins_enabled instead.
    plugins_disabled: list[str] = field(default_factory=list)

    # Operator suite: marketplace + C2 plugins on the rail (Privacy/Lab hide them).
    operate_enabled: bool = False

    # When False (default), Operate plugins / agent work require an active path
    # (Spectre CONNECTED — VPN or multi-hop). When True, that gate is off.
    allow_sensitive_without_path: bool = False

    # Left rail: expanded shows section labels (Run / Path / …)
    rail_expanded: bool = False

    # Window geometry (0 = use app default)
    window_width: int = 0
    window_height: int = 0

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
        if "plugins_enabled" in data:
            from core.plugins import normalize_enabled

            data["plugins_enabled"] = normalize_enabled(data.get("plugins_enabled"))
        if "plugins_disabled" in data:
            raw_dis = data.get("plugins_disabled") or []
            if isinstance(raw_dis, list):
                data["plugins_disabled"] = [
                    str(x).strip() for x in raw_dis if str(x).strip()
                ]
            else:
                data["plugins_disabled"] = []
        # Migrate: configs written before operate_enabled — if marketplace
        # plugins are already installed, keep Operate on so Hogwarts doesn't vanish.
        if "operate_enabled" not in raw:
            try:
                from core.plugin_store import list_installed

                data["operate_enabled"] = len(list_installed()) > 0
            except Exception:
                data["operate_enabled"] = False
        else:
            data["operate_enabled"] = bool(data.get("operate_enabled"))
        if "allow_sensitive_without_path" in data:
            data["allow_sensitive_without_path"] = bool(
                data.get("allow_sensitive_without_path")
            )
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
    # Apps opened via the Apps page for this Connect session
    launch_session: LaunchSession = field(default_factory=LaunchSession)
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
        from core.plugins import normalize_enabled

        self.config.plugins_enabled = normalize_enabled(self.config.plugins_enabled)
        self.config.save(self._config_path)
        self.core.socket_path = self.config.core_socket.strip() or default_socket_path()
        self.core.timeout_sec = self.config.core_timeout_sec
        self.core.api_token = self.config.api_token
        # Throttle log line — "Config saved" was the #2 most common desktop.log
        # entry (window geometry / profile sync) and added disk noise every few s.
        import time as _time

        now = _time.monotonic()
        last = float(getattr(self, "_last_config_log_at", 0.0) or 0.0)
        if now - last >= 30.0:
            self._last_config_log_at = now
            self.log("Config saved")

    def plugins_enabled(self) -> list[str]:
        from core.plugins import normalize_enabled

        return normalize_enabled(self.config.plugins_enabled)

    def plugin_enabled(self, plugin_id: str) -> bool:
        from core.plugins import is_enabled

        return is_enabled(self.config.plugins_enabled, plugin_id)

    def operate_enabled(self) -> bool:
        """True when the Operate rail (marketplace + C2 plugins) is unlocked."""
        return bool(getattr(self.config, "operate_enabled", False))

    def installed_plugin_active(self, plugin_id: str) -> bool:
        """True if a filesystem marketplace plugin is installed and not disabled.

        When Operate posture is off, all marketplace plugins are inactive on the rail
        (installs stay on disk).
        """
        pid = (plugin_id or "").strip()
        if not pid:
            return False
        if not self.operate_enabled():
            return False
        disabled = {
            str(x).strip()
            for x in (self.config.plugins_disabled or [])
            if str(x).strip()
        }
        return pid not in disabled

    def set_installed_plugin_active(self, plugin_id: str, active: bool) -> None:
        """Enable/disable an installed plugin without uninstalling it."""
        pid = (plugin_id or "").strip()
        if not pid:
            return
        cur = [
            str(x).strip()
            for x in (self.config.plugins_disabled or [])
            if str(x).strip()
        ]
        if active:
            cur = [x for x in cur if x != pid]
        else:
            if pid not in cur:
                cur.append(pid)
        self.config.plugins_disabled = cur
        self.save_config()

    def is_path_connected(self) -> bool:
        """True when spectred reports an active path (not merely core online)."""
        try:
            return self.core.status().state == CoreState.CONNECTED
        except Exception:
            return False

    def require_path_for_sensitive_ops(self) -> bool:
        """True when sensitive Operate work must run behind an active path."""
        return not bool(getattr(self.config, "allow_sensitive_without_path", False))

    def sensitive_ops_allowed(self) -> bool:
        """True if operator/plugin work is allowed under current policy + path.

        Default: require Spectre CONNECTED (active VPN/path). Settings can allow
        operating without a path after explicit confirmation.
        """
        if not self.require_path_for_sensitive_ops():
            return True
        return self.is_path_connected()

    def sensitive_ops_block_message(self) -> str:
        """Short reason when sensitive_ops_allowed() is False."""
        if self.sensitive_ops_allowed():
            return ""
        return (
            "Connect a path (VPN/privacy hop) before using Operate tools, "
            "agents, or marketplace plugins — or allow this in Settings → Privacy"
        )

    def ensure_sensitive_ops_allowed(self) -> bool:
        """Return True if sensitive ops are allowed; False when gated."""
        return self.sensitive_ops_allowed()

    def with_reconnect_hint(self, message: str) -> str:
        """Append a reconnect reminder when the path is live.

        Desktop saves apply to disk immediately; hops/policy only take effect
        on the next Connect.
        """
        msg = (message or "").strip() or "Saved"
        if not self.is_path_connected():
            return msg
        # Keep short for Adw.Toast; full policy is also noted in Settings.
        return f"{msg} · reconnect to apply to the live path"

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

    def readiness(self, *, live: bool = False) -> Readiness:
        """Structural readiness, or live=True for connect preflight probes."""
        return profile_readiness(
            self.active_profile(),
            self.backends,
            routing_mode=self.config.routing_mode or "system",
            kill_switch=bool(self.config.kill_switch),
            live=live,
        )

    def build_connect_payload(self, profile: Profile) -> dict[str, Any]:
        """Anticipated core payload — fully built on the desktop today."""
        intent = (getattr(profile, "path_intent", None) or "").strip()
        payload: dict[str, Any] = {
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
                "routing_mode": (
                    self.config.routing_mode
                    if self.config.routing_mode in ("system", "apps")
                    else "system"
                ),
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
        if intent:
            payload["path_intent"] = intent
        return payload

    def connect_active(self) -> tuple[CoreStatus | None, Readiness]:
        """Validate locally (live probes), then hand off to core."""
        from core.mullvad import ensure_connected
        from core.netns_guard import clearnet_netns_block_message, in_clearnet_netns
        from core.readiness import Readiness, profile_uses_mullvad_app_socks

        # Desktop must not run inside the exclude netns (Mullvad SOCKS dies).
        if in_clearnet_netns():
            msg = clearnet_netns_block_message()
            self.log(f"Connect blocked: {msg}", level="warn")
            return None, Readiness(False, [msg])

        profile = self.active_profile()
        # Official Mullvad support: bring tunnel up before SOCKS-hop preflight.
        if (
            self.config.mullvad_auto_connect
            and profile is not None
            and profile_uses_mullvad_app_socks(profile, self.backends)
        ):
            mv = ensure_connected(timeout_sec=45.0)
            self.log(f"Mullvad ensure: {mv.summary}")
            if not mv.ready_for_socks_hop and mv.available:
                # Fall through to readiness for a clear toast
                pass

        ready = self.readiness(live=True)
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

    def disconnect(self) -> tuple[CoreStatus, str]:
        """Tear down Spectre path and restore clearnet.

        Returns (status, toast_message). Path-opened apps are left running;
        their SOCKS points at Spectre, so network fails until Connect again
        (they are not killed and are not steered onto clearnet by us).
        System routing is flushed by the core; we also run ``spectre unlock``.
        When the active profile uses Mullvad tunnel SOCKS and auto-connect is
        on, we disconnect Mullvad too.
        """
        from core import mullvad as mv
        from core.readiness import profile_uses_mullvad_app_socks

        profile = self.active_profile()
        used_mullvad = profile_uses_mullvad_app_socks(profile, self.backends)

        status = self.core.disconnect()
        self.log("Disconnect requested")

        # Restore clearnet if nft REDIRECT/DROP tables were left behind.
        unlock_note = self._unlock_network()
        if unlock_note:
            self.log(unlock_note)

        parts = ["Spectre path stopped"]
        n_path = self.launch_session.active_count()
        if n_path:
            parts.append(
                f"{n_path} path app{'s' if n_path != 1 else ''} still open "
                "(no network until Connect)"
            )
        if used_mullvad and self.config.mullvad_auto_connect:
            ok, msg = mv.disconnect()
            if ok:
                self.log("Mullvad disconnect requested (paired with Spectre Disconnect)")
                parts.append("Mullvad disconnect requested")
            else:
                self.log(f"Mullvad disconnect: {msg}", level="warn")
                parts.append("Mullvad still connected — use Settings or Mullvad app")
        elif used_mullvad:
            parts.append("Mullvad left connected (auto-manage off)")

        return status, " · ".join(parts)

    def _unlock_network(self) -> str:
        """Best-effort clearnet restore via spectre CLI (nft unlock helper)."""
        import shutil
        import subprocess

        exe = shutil.which("spectre")
        if not exe:
            home = Path.home() / ".local" / "bin" / "spectre"
            if home.is_file():
                exe = str(home)
        if not exe:
            return ""
        try:
            proc = subprocess.run(  # noqa: S603
                [exe, "unlock"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"unlock failed: {exc}"
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if proc.returncode != 0:
            return f"unlock exit {proc.returncode}: {out or 'failed'}"
        return "network unlock ok"
