"""Navigation destinations — primary Reach IA.

Parent pages on the rail; detail lives in sub-pages (hubs):
  Paths → recipes · adapters
  Settings → plugins · core · network · …

Rail sections (expanded labels):
  Run · Path · Workspace · Operate · System

**Operate** (marketplace + installed plugins) is gated by
``config.operate_enabled`` — Privacy/Lab stay path-first.
"""

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
    """Absolute path to a plugin SVG/PNG (takes priority over icon_name)."""
    icon_path: str | None = None
    """When True, draw a quiet section divider above this item (legacy)."""
    section_start: bool = False
    """official = core Reach; plugin = marketplace / installed plugin page."""
    kind: str = "official"  # official | plugin | marketplace
    """Rail group: run | path | workspace | operate | system."""
    section: str = "run"


# Display order + expanded label for each rail section
RAIL_SECTIONS: tuple[tuple[str, str], ...] = (
    ("run", "Run"),
    ("path", "Path"),
    ("workspace", "Workspace"),
    ("operate", "Operate"),
    ("system", "System"),
)

# Order: run → path → workspace → operate → system
NAV_ITEMS: tuple[NavItem, ...] = (
    NavItem(
        id="home",
        title="Home",
        icon_name="go-home-symbolic",
        tooltip="Status and Connect",
        section="run",
    ),
    NavItem(
        id="paths",
        title="Paths",
        icon_name="view-list-symbolic",
        tooltip="Recipes and adapters",
        icon_asset="paths.svg",
        section_start=True,
        section="path",
    ),
    NavItem(
        id="china",
        title="Territories",
        icon_name="network-workgroup-symbolic",
        tooltip="Reach into a region (special setups)",
        icon_asset="globe.svg",
        section="path",
    ),
    NavItem(
        id="apps",
        title="Apps",
        icon_name="view-app-grid-symbolic",
        tooltip="Open apps on clearnet",
        section="workspace",
    ),
    NavItem(
        id="tools",
        title="Tools",
        icon_name="applications-engineering-symbolic",
        tooltip="Diagnostics and lab tools",
        section="workspace",
    ),
    NavItem(
        id="marketplace",
        title="Plugins",
        icon_name="application-x-addon-symbolic",
        tooltip="Install operator tools (Hogwarts C2, …)",
        kind="marketplace",
        section_start=True,
        section="operate",
    ),
    NavItem(
        id="settings",
        title="Settings",
        icon_name="preferences-system-symbolic",
        tooltip="Core, network, privacy, posture…",
        section_start=True,
        section="system",
    ),
)

DEFAULT_PAGE = "home"

PAGE_SUBTITLES: dict[str, str] = {
    "home": "Status",
    "paths": "Paths",
    "profiles": "Paths",
    "backends": "Adapters",
    "china": "Territories",
    "apps": "Apps",
    "tools": "Tools",
    "marketplace": "Plugins",
    "settings": "Settings",
}


def official_nav_items() -> tuple[NavItem, ...]:
    return tuple(i for i in NAV_ITEMS if i.kind in ("official", "marketplace"))


def items_for_section(section_id: str) -> tuple[NavItem, ...]:
    return tuple(i for i in NAV_ITEMS if i.section == section_id)


def is_operate_page(page_id: str) -> bool:
    """True if this stack page belongs to the Operate suite."""
    if not page_id:
        return False
    if page_id == "marketplace" or page_id.startswith("plugin:"):
        return True
    return False
