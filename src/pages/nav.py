"""Navigation destinations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    id: str
    title: str
    icon_name: str
    tooltip: str
    """Optional asset under data/assets/ (e.g. flag SVG) instead of icon_name."""
    icon_asset: str | None = None


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
        title="Exclude apps",
        icon_name="view-app-grid-symbolic",
        tooltip="Run apps on clearnet (exclude from Spectre / tunnel)",
    ),
    NavItem(
        id="china",
        title="Reach",
        icon_name="network-workgroup-symbolic",
        tooltip="Territory ingress — inbound host or Inverse Snowflake (China default)",
        icon_asset="globe.svg",
    ),
    NavItem(
        id="settings",
        title="Settings",
        icon_name="preferences-system-symbolic",
        tooltip="Settings",
    ),
)

DEFAULT_PAGE = "home"
