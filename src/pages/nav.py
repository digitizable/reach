"""Navigation destinations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    id: str
    title: str
    icon_name: str
    tooltip: str


NAV_ITEMS: tuple[NavItem, ...] = (
    NavItem(
        id="home",
        title="Home",
        icon_name="go-home-symbolic",
        tooltip="Home",
    ),
    NavItem(
        id="profiles",
        title="Profiles",
        icon_name="avatar-default-symbolic",
        tooltip="Profiles",
    ),
    NavItem(
        id="backends",
        title="Backends",
        icon_name="network-server-symbolic",
        tooltip="Backends",
    ),
    NavItem(
        id="apps",
        title="Apps",
        icon_name="view-app-grid-symbolic",
        tooltip="Launch apps through Spectre",
    ),
    NavItem(
        id="settings",
        title="Settings",
        icon_name="preferences-system-symbolic",
        tooltip="Settings",
    ),
)

DEFAULT_PAGE = "home"
