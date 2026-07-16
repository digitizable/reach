"""Local backend definitions — anticipates Spectre core adapters.

Fully functional on the desktop: create, edit, delete, persist, validate.
The core will later own secrets and live connections; this store is the
desktop draft of “which VPN / Tor / REALITY / Proxy instance” a hop uses.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from app_config import user_data_dir

# Mirrors Spectre hop / adapter kinds
BACKEND_KINDS: tuple[str, ...] = (
    "REALITY",
    "VPN",
    "Tor",
    "Proxy",
)

VPN_PROTOCOLS: tuple[str, ...] = (
    "WireGuard",
    "OpenVPN",
    "IKEv2",
    "Other",
)

PROXY_PROTOCOLS: tuple[str, ...] = (
    "SOCKS5",
    "HTTP",
    "HTTPS",
)


@dataclass
class Backend:
    """A concrete adapter instance the user can bind into a path hop."""

    id: str
    kind: str  # REALITY | VPN | Tor | Proxy
    name: str
    enabled: bool = True
    notes: str = ""

    # --- VPN ---
    vpn_protocol: str = "WireGuard"
    vpn_provider: str = ""  # e.g. Mullvad, Proton, custom
    vpn_endpoint: str = ""  # host:port or region label
    vpn_config: str = ""  # config blob or path note (desktop-side draft)

    # --- REALITY / Xray ---
    reality_server: str = ""
    reality_port: int = 443
    reality_uuid: str = ""
    reality_public_key: str = ""
    reality_short_id: str = ""
    reality_sni: str = ""
    reality_flow: str = "xtls-rprx-vision"

    # --- Tor ---
    tor_socks_host: str = "127.0.0.1"
    tor_socks_port: int = 9050
    tor_control_port: int = 9051
    tor_use_system: bool = True

    # --- Proxy ---
    proxy_protocol: str = "SOCKS5"
    proxy_host: str = ""
    proxy_port: int = 1080
    proxy_username: str = ""
    proxy_password: str = ""

    def label(self) -> str:
        return self.name.strip() or f"{self.kind} backend"

    def is_configured(self) -> bool:
        """Desktop-side completeness check (not a live core probe)."""
        if not self.name.strip() or self.kind not in BACKEND_KINDS:
            return False
        if self.kind == "VPN":
            # WireGuard needs a .conf path for the core; other protocols keep soft draft rules.
            if (self.vpn_protocol or "WireGuard") == "WireGuard":
                return bool(self.vpn_config.strip())
            return bool(
                self.vpn_provider.strip()
                or self.vpn_config.strip()
                or self.vpn_endpoint.strip()
            )
        if self.kind == "REALITY":
            return bool(
                self.reality_server.strip()
                and self.reality_public_key.strip()
                and self.reality_uuid.strip()
            )
        if self.kind == "Tor":
            return self.tor_use_system or bool(
                self.tor_socks_host.strip() and self.tor_socks_port > 0
            )
        if self.kind == "Proxy":
            return bool(self.proxy_host.strip() and self.proxy_port > 0)
        return False

    def status_line(self) -> str:
        if not self.is_configured():
            return "Incomplete"
        if self.kind == "VPN":
            bits = [self.vpn_protocol]
            if self.vpn_provider:
                bits.append(self.vpn_provider)
            elif self.vpn_endpoint:
                bits.append(self.vpn_endpoint)
            return " · ".join(bits)
        if self.kind == "REALITY":
            host = self.reality_server
            if self.reality_port:
                host = f"{host}:{self.reality_port}"
            return host or "REALITY"
        if self.kind == "Tor":
            if self.tor_use_system:
                return "System Tor"
            return f"{self.tor_socks_host}:{self.tor_socks_port}"
        if self.kind == "Proxy":
            return f"{self.proxy_protocol} {self.proxy_host}:{self.proxy_port}"
        return self.kind

    def to_dict(self) -> dict:
        return asdict(self)


def _tor_default_backend() -> Backend:
    """System / Whonix Gateway Tor seed."""
    from core.whonix import detect

    w = detect()
    if w.is_workstation:
        return Backend(
            id="tor-system",
            kind="Tor",
            name="Whonix Gateway Tor",
            notes=(
                f"Whonix-Workstation → Gateway SOCKS "
                f"{w.tor_socks_host}:{w.tor_socks_port}"
            ),
            tor_use_system=True,
            tor_socks_host=w.tor_socks_host,
            tor_socks_port=w.tor_socks_port,
            tor_control_port=9051,
        )
    return Backend(
        id="tor-system",
        kind="Tor",
        name="System Tor",
        notes="Uses the local Tor daemon (SOCKS 9050)",
        tor_use_system=True,
        tor_socks_host="127.0.0.1",
        tor_socks_port=9050,
        tor_control_port=9051,
    )


def default_backend_templates() -> tuple[Backend, ...]:
    """Default backends; Tor adapts to Whonix when detected."""
    return (
        Backend(
            id="vpn-primary",
            kind="VPN",
            name="My VPN",
            notes="Fill in provider or config under Backends",
            vpn_protocol="WireGuard",
            vpn_provider="",
        ),
        Backend(
            id="reality-primary",
            kind="REALITY",
            name="Entry REALITY",
            notes="Fill in server + public key + UUID under Backends",
            reality_port=443,
            reality_flow="xtls-rprx-vision",
        ),
        _tor_default_backend(),
    )


# Stable IDs so default profiles can bind out of the box.
DEFAULT_BACKENDS: tuple[Backend, ...] = default_backend_templates()


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "backend"


def _default_backends() -> list[Backend]:
    # Re-evaluate Tor defaults at seed time (Whonix may be present).
    return [
        Backend(**{f.name: getattr(b, f.name) for f in fields(Backend)})
        for b in default_backend_templates()
    ]


def _backend_from_dict(item: dict) -> Backend | None:
    known = {f.name for f in fields(Backend)}
    data = {k: v for k, v in item.items() if k in known}
    if "id" not in data or "kind" not in data or "name" not in data:
        return None
    try:
        return Backend(**data)
    except TypeError:
        return None


class BackendStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (user_data_dir() / "backends.json")
        self._backends: list[Backend] = []
        self.load()

    def load(self) -> None:
        """Load backends. Missing or empty store → seed defaults."""
        if not self._path.is_file():
            self._backends = _default_backends()
            self.save()
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            items = raw if isinstance(raw, list) else raw.get("backends", [])
            self._backends = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                backend = _backend_from_dict(item)
                if backend is not None:
                    self._backends.append(backend)
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            self._backends = _default_backends()
            self.save()
            return

        if not self._backends:
            self._backends = _default_backends()
            self.save()
        else:
            self._apply_whonix_tor()

    def _apply_whonix_tor(self) -> None:
        """Retarget seeded System Tor toward Whonix Gateway when on Workstation."""
        from core.whonix import detect

        w = detect()
        if not w.is_workstation:
            return
        tor = self.get("tor-system")
        if tor is None or tor.kind != "Tor":
            return
        changed = False
        if tor.tor_use_system or tor.tor_socks_host in ("", "127.0.0.1", "localhost"):
            if tor.tor_socks_host != w.tor_socks_host or tor.tor_socks_port != w.tor_socks_port:
                tor.tor_socks_host = w.tor_socks_host
                tor.tor_socks_port = w.tor_socks_port
                tor.tor_use_system = True
                changed = True
            if tor.name in ("System Tor", "Tor"):
                tor.name = "Whonix Gateway Tor"
                changed = True
            note = f"Whonix-Workstation → Gateway SOCKS {w.tor_socks_host}:{w.tor_socks_port}"
            if note not in (tor.notes or ""):
                tor.notes = note
                changed = True
        if changed:
            self.save()

    def save(self) -> None:
        payload = {"backends": [b.to_dict() for b in self._backends]}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def list(self, *, kind: str | None = None, enabled_only: bool = False) -> list[Backend]:
        items = list(self._backends)
        if kind is not None:
            items = [b for b in items if b.kind == kind]
        if enabled_only:
            items = [b for b in items if b.enabled]
        return items

    def get(self, backend_id: str) -> Backend | None:
        for b in self._backends:
            if b.id == backend_id:
                return b
        return None

    def create(self, *, kind: str, name: str, **extra: object) -> Backend:
        kind = kind.strip()
        name = name.strip()
        if kind not in BACKEND_KINDS:
            raise ValueError(f"Unknown backend kind: {kind}")
        if not name:
            raise ValueError("Backend name is required")
        pid = f"{_slug(kind)}-{_slug(name)}-{uuid.uuid4().hex[:6]}"
        known = {f.name for f in fields(Backend)} - {"id", "kind", "name"}
        data = {k: v for k, v in extra.items() if k in known}
        backend = Backend(id=pid, kind=kind, name=name, **data)  # type: ignore[arg-type]
        self._backends.append(backend)
        self.save()
        return backend

    def update(self, backend_id: str, **extra: object) -> Backend | None:
        backend = self.get(backend_id)
        if backend is None:
            return None
        known = {f.name for f in fields(Backend)} - {"id"}
        for key, value in extra.items():
            if key not in known:
                continue
            if key == "name":
                name = str(value).strip()
                if not name:
                    raise ValueError("Backend name is required")
                backend.name = name
            elif key == "kind":
                kind = str(value).strip()
                if kind not in BACKEND_KINDS:
                    raise ValueError(f"Unknown backend kind: {kind}")
                backend.kind = kind
            else:
                setattr(backend, key, value)
        self.save()
        return backend

    def delete(self, backend_id: str) -> bool:
        before = len(self._backends)
        self._backends = [b for b in self._backends if b.id != backend_id]
        if len(self._backends) == before:
            return False
        self.save()
        return True

    def count(self, *, kind: str | None = None) -> int:
        return len(self.list(kind=kind))
