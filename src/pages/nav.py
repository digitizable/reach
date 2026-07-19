"""Navigation destinations — primary operator IA."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    id: str
    title: str
    icon_name: str
    tooltip: str
    """Optional asset under data/assets/ (e.g. globe SVG) instead of icon_name."""
    icon_asset: str | None = None
    """When True, draw a quiet section divider above this item."""
    section_start: bool = False


# Order is the mental model:
#   Status → compose path → open doors → carve-outs → lab tools → settings
NAV_ITEMS: tuple[NavItem, ...] = (
    NavItem(
        id="home",
        title="Home",
        icon_name="go-home-symbolic",
        tooltip="Home — status and Connect",
    ),
    NavItem(
        id="profiles",
        title="Paths",
        icon_name="view-list-symbolic",
        tooltip="Paths — ordered hop recipes (profiles)",
        section_start=True,
    ),
    NavItem(
        id="backends",
        title="Adapters",
        icon_name="network-server-symbolic",
        tooltip="Adapters — VPN, Tor, REALITY, proxy backends",
    ),
    NavItem(
        id="china",
        title="Doors",
        icon_name="network-workgroup-symbolic",
        tooltip="Doors — territory ingress (inbound or dial-out)",
        icon_asset="globe.svg",
        section_start=True,
    ),
    NavItem(
        id="apps",
        title="Apps",
        icon_name="view-app-grid-symbolic",
        tooltip="Apps — run selected apps on clearnet (exclude from path)",
    ),
    NavItem(
        id="tools",
        title="Tools",
        icon_name="applications-engineering-symbolic",
        tooltip="Tools — Drift, Mirage, Sounding (lab)",
        section_start=True,
    ),
    NavItem(
        id="settings",
        title="Settings",
        icon_name="preferences-system-symbolic",
        tooltip="Settings",
        section_start=True,
    ),
)

DEFAULT_PAGE = "home"

# Map nav id → short chrome subtitle when not overridden by core state
PAGE_SUBTITLES: dict[str, str] = {
    "home": "Status",
    "profiles": "Paths",
    "backends": "Adapters",
    "china": "Doors",
    "apps": "Exclude apps",
    "tools": "Lab tools",
    "settings": "Settings",
}
