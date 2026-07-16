"""Local path profiles until the Spectre core owns storage.

Desktop-side CRUD is fully functional. Hops bind to backends by id;
readiness (see core.readiness) decides whether Connect is allowed.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app_config import user_data_dir

if TYPE_CHECKING:
    from core.backends import BackendStore

# Backends Spectre is designed to compose (see spectre README).
HOP_KINDS: tuple[str, ...] = (
    "REALITY",
    "VPN",
    "Tor",
    "Proxy",
)


@dataclass
class Hop:
    """One step in a path — kind + optional bound backend."""

    kind: str
    backend_id: str = ""

    def display_kind(self) -> str:
        return self.kind


@dataclass
class Profile:
    id: str
    name: str
    summary: str = ""
    hops: list[Hop] = field(default_factory=list)
    notes: str = ""
    favorite: bool = False
    # User-editable dashboard “i” text. Empty → built-in default (if known id)
    # or “Custom configuration.”
    info: str = ""

    def hop_kinds(self) -> list[str]:
        return [h.kind for h in self.hops]

    def hops_line(self) -> str:
        return " → ".join(self.hop_kinds()) if self.hops else "No hops"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "notes": self.notes,
            "favorite": self.favorite,
            "info": self.info,
            "hops": [
                {"kind": h.kind, "backend_id": h.backend_id} for h in self.hops
            ],
        }


def _parse_hop(item: object) -> Hop | None:
    if isinstance(item, str):
        kind = item.strip()
        return Hop(kind=kind) if kind else None
    if isinstance(item, dict):
        kind = str(item.get("kind") or item.get("type") or "").strip()
        if not kind:
            return None
        return Hop(kind=kind, backend_id=str(item.get("backend_id") or ""))
    return None


def _parse_profile(item: dict) -> Profile | None:
    if "id" not in item or "name" not in item:
        return None
    hops: list[Hop] = []
    for raw in item.get("hops") or []:
        hop = _parse_hop(raw)
        if hop is not None:
            hops.append(hop)
    return Profile(
        id=str(item["id"]),
        name=str(item["name"]),
        summary=str(item.get("summary", "")),
        hops=hops,
        notes=str(item.get("notes", "")),
        favorite=bool(item.get("favorite", False)),
        info=str(item.get("info", "")),
    )


# Seeded when the store is missing or empty (first run).
# backend_id values match default_backend_templates() in core.backends.
# Only valid compositions (see core.path_compose) — invalid nestings are blocked.
DEFAULT_PROFILES: tuple[Profile, ...] = (
    Profile(
        id="stealth-entry",
        name="Stealth entry",
        summary="REALITY only — public exit is the REALITY server",
        hops=[Hop("REALITY", "reality-primary")],
        notes=(
            "Fill the REALITY backend first. For Mullvad as first-mile underlay, "
            "use “Mullvad into REALITY” (Mullvad SOCKS first, then REALITY)."
        ),
        favorite=True,
    ),
    Profile(
        id="mullvad-only",
        name="Mullvad only",
        summary="Mullvad app SOCKS exit",
        hops=[Hop("Proxy", "mullvad-socks")],
        notes="Requires the Mullvad app Connected (full tunnel + in-tunnel SOCKS).",
    ),
    Profile(
        id="mullvad-reality",
        name="Mullvad into REALITY",
        summary="Mullvad app underlay → REALITY exit",
        hops=[
            Hop("Proxy", "mullvad-socks"),
            Hop("REALITY", "reality-primary"),
        ],
        notes=(
            "Mullvad is OS underlay; Spectre dials REALITY only. "
            "Exit IP is the REALITY server — not a Mullvad exit. "
            "Check: https://anguish.sh/reality-check"
        ),
    ),
    Profile(
        id="mullvad-tor",
        name="Mullvad into Tor",
        summary="Mullvad app underlay → Tor exit",
        hops=[
            Hop("Proxy", "mullvad-socks"),
            Hop("Tor", "tor-system"),
        ],
        notes=(
            "Mullvad stays the OS tunnel; Spectre dials Tor only. "
            "Traffic is still Host → Mullvad → Tor when the app is Connected."
        ),
    ),
    Profile(
        id="tor-only",
        name="Tor only",
        summary="System Tor exit",
        hops=[Hop("Tor", "tor-system")],
    ),
    Profile(
        id="vpn-only",
        name="VPN only",
        summary="WireGuard hop (enable + .conf under Backends)",
        hops=[Hop("VPN", "vpn-primary")],
        notes="Disabled by default until a WireGuard .conf is set on vpn-primary.",
    ),
    Profile(
        id="vpn-tor",
        name="VPN into Tor",
        summary="WireGuard underlay → Tor exit",
        hops=[
            Hop("VPN", "vpn-primary"),
            Hop("Tor", "tor-system"),
        ],
        notes="Requires a working WireGuard backend, then system Tor.",
    ),
)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "profile"


def _default_profiles() -> list[Profile]:
    return [
        Profile(
            id=p.id,
            name=p.name,
            summary=p.summary,
            hops=[Hop(h.kind, h.backend_id) for h in p.hops],
            notes=p.notes,
            favorite=p.favorite,
            info=p.info,
        )
        for p in DEFAULT_PROFILES
    ]


def _normalize_hops(raw_hops: list) -> list[Hop]:
    parsed: list[Hop] = []
    for raw in raw_hops:
        if isinstance(raw, Hop):
            parsed.append(Hop(raw.kind, raw.backend_id))
        else:
            hop = _parse_hop(raw)
            if hop is not None:
                parsed.append(hop)
    return parsed


class ProfileStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (user_data_dir() / "profiles.json")
        self._profiles: list[Profile] = []
        self.load()

    def load(self) -> None:
        """Load profiles. Missing or empty store → seed defaults."""
        if not self._path.is_file():
            self._profiles = _default_profiles()
            self.save()
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            items = raw if isinstance(raw, list) else raw.get("profiles", [])
            self._profiles = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                profile = _parse_profile(item)
                if profile is not None:
                    self._profiles.append(profile)
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            self._profiles = _default_profiles()
            self.save()
            return

        if not self._profiles:
            self._profiles = _default_profiles()
            self.save()

    def save(self) -> None:
        payload = {"profiles": [p.to_dict() for p in self._profiles]}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )

    def list(self) -> list[Profile]:
        # Favorites first, then name.
        return sorted(
            self._profiles,
            key=lambda p: (not p.favorite, p.name.lower()),
        )

    def get(self, profile_id: str) -> Profile | None:
        for p in self._profiles:
            if p.id == profile_id:
                return p
        return None

    def by_name(self, name: str) -> Profile | None:
        for p in self._profiles:
            if p.name == name:
                return p
        return None

    def create(
        self,
        *,
        name: str,
        summary: str = "",
        hops: list[Hop] | list[dict] | list[str] | None = None,
        notes: str = "",
        favorite: bool = False,
        info: str = "",
    ) -> Profile:
        name = name.strip()
        if not name:
            raise ValueError("Profile name is required")
        parsed = _normalize_hops(list(hops or []))
        if not parsed:
            raise ValueError("Add at least one hop")
        for hop in parsed:
            if hop.kind not in HOP_KINDS:
                raise ValueError(f"Unknown hop kind: {hop.kind}")
        base = _slug(name)
        pid = f"{base}-{uuid.uuid4().hex[:8]}"
        profile = Profile(
            id=pid,
            name=name,
            summary=summary.strip(),
            hops=parsed,
            notes=notes.strip(),
            favorite=favorite,
            info=info.strip(),
        )
        self._profiles.append(profile)
        self.save()
        return profile

    def update(self, profile_id: str, **fields: object) -> Profile | None:
        profile = self.get(profile_id)
        if profile is None:
            return None
        if "name" in fields:
            name = str(fields["name"]).strip()
            if not name:
                raise ValueError("Profile name is required")
            profile.name = name
        if "summary" in fields:
            profile.summary = str(fields["summary"]).strip()
        if "hops" in fields:
            raw_hops = fields["hops"]
            if not isinstance(raw_hops, list):
                raise TypeError("hops must be a list")
            parsed = _normalize_hops(raw_hops)
            if not parsed:
                raise ValueError("Add at least one hop")
            for hop in parsed:
                if hop.kind not in HOP_KINDS:
                    raise ValueError(f"Unknown hop kind: {hop.kind}")
            profile.hops = parsed
        if "notes" in fields:
            profile.notes = str(fields["notes"]).strip()
        if "favorite" in fields:
            profile.favorite = bool(fields["favorite"])
        if "info" in fields:
            profile.info = str(fields["info"]).strip()
        self.save()
        return profile

    def delete(self, profile_id: str) -> bool:
        before = len(self._profiles)
        self._profiles = [p for p in self._profiles if p.id != profile_id]
        if len(self._profiles) == before:
            return False
        self.save()
        return True

    def unbind_backend(self, backend_id: str) -> int:
        """Clear hop bindings when a backend is deleted. Returns hops cleared."""
        cleared = 0
        for profile in self._profiles:
            for hop in profile.hops:
                if hop.backend_id == backend_id:
                    hop.backend_id = ""
                    cleared += 1
        if cleared:
            self.save()
        return cleared

    def reconcile_backends(self, backends: BackendStore) -> int:
        """Fix dangling bindings; auto-bind hops that have exactly one match.

        Returns the number of hops changed. Fully functional desktop hygiene —
        does not require Spectre core.
        """
        changed = 0
        for profile in self._profiles:
            for hop in profile.hops:
                if hop.backend_id:
                    b = backends.get(hop.backend_id)
                    if b is None:
                        hop.backend_id = ""
                        changed += 1
                    elif b.kind != hop.kind:
                        hop.backend_id = ""
                        changed += 1
                    else:
                        continue
                # Unbound: bind only when there is a single backend of this kind
                candidates = backends.list(kind=hop.kind)
                if len(candidates) == 1:
                    hop.backend_id = candidates[0].id
                    changed += 1
        if changed:
            self.save()
        return changed

    def hop_labels(self, profile: Profile, backends: BackendStore) -> list[str]:
        """Display labels for path diagram (backend name or kind)."""
        labels: list[str] = []
        for hop in profile.hops:
            b = backends.get(hop.backend_id) if hop.backend_id else None
            labels.append(b.name if b else hop.kind)
        return labels
