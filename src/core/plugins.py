"""Reach built-in plugin packs + desktop posture (Settings → Plugins).

Core Reach: path console, territories, apps carve-out, basic tools.
Built-in packs: lab measurement + companion install surface.
**Operate** posture unlocks marketplace + C2 (Hogwarts) on the rail.
C2 itself lives in marketplace plugins — not as a Settings pack.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# Stable ids persisted in config.json → plugins_enabled
PLUGIN_FINGERPRINT = "fingerprint"
PLUGIN_LAB = "lab"

# Desktop posture names (config.operate_enabled + packs)
POSTURE_PRIVACY = "privacy"
POSTURE_LAB = "lab"
POSTURE_OPERATE = "operate"

# Legacy ids (stripped on load / normalize)
_LEGACY_IDS = frozenset({"reachback", "face_probe", "egress"})


@dataclass(frozen=True)
class Plugin:
    id: str
    title: str
    tagline: str
    description: str
    category: str  # "lab" (built-ins only; C2 is marketplace Hogwarts)
    icon: str
    # Tools page action targets gated by this plugin (see pages/tools.py)
    tool_targets: tuple[str, ...] = ()
    # Show lab companions row
    shows_lab_companions: bool = False


PLUGINS: tuple[Plugin, ...] = (
    Plugin(
        id=PLUGIN_FINGERPRINT,
        title="Path fingerprint",
        tagline="Composition / ΔRTT lab scores on the live path",
        description=(
            "Measure cross-layer RTT and path latency through Spectre SOCKS "
            "(Laminar F2). Research / purple-team — does not change the path."
        ),
        category="lab",
        icon="utilities-system-monitor-symbolic",
        tool_targets=("fingerprint",),
    ),
    Plugin(
        id=PLUGIN_LAB,
        title="Lab companions",
        tagline="Drift · Mirage · Sounding · Laminar install surface",
        description=(
            "Detect, install, and update lab engines next to Reach: reverse "
            "pathing, cover, measurement, and fingerprint tooling."
        ),
        category="lab",
        icon="applications-science-symbolic",
        shows_lab_companions=True,
    ),
)

_BY_ID: dict[str, Plugin] = {p.id: p for p in PLUGINS}


def plugin_by_id(plugin_id: str) -> Plugin | None:
    return _BY_ID.get(plugin_id)


def normalize_enabled(raw: Iterable[str] | None) -> list[str]:
    """Dedupe, drop unknowns/legacy, preserve catalog order."""
    if not raw:
        return []
    want = {str(x).strip() for x in raw if str(x).strip()}
    want -= _LEGACY_IDS
    return [p.id for p in PLUGINS if p.id in want]


def is_enabled(enabled: Iterable[str] | None, plugin_id: str) -> bool:
    return plugin_id in set(normalize_enabled(enabled))


def tool_allowed(enabled: Iterable[str] | None, tool_target: str) -> bool:
    """True if tool is core (no plugin) or its plugin is enabled."""
    for p in PLUGINS:
        if tool_target in p.tool_targets:
            return is_enabled(enabled, p.id)
    return True  # core tool


def lab_companions_visible(enabled: Iterable[str] | None) -> bool:
    return is_enabled(enabled, PLUGIN_LAB)


def preset_privacy() -> list[str]:
    """Core path console only — no lab packs, Operate off (caller sets flag)."""
    return []


def preset_lab() -> list[str]:
    """Lab packs on; Operate still off unless user enables it."""
    return normalize_enabled([PLUGIN_FINGERPRINT, PLUGIN_LAB])


def preset_operate() -> list[str]:
    """Lab packs on + Operate rail (marketplace / C2) — flag set by caller."""
    return normalize_enabled([PLUGIN_FINGERPRINT, PLUGIN_LAB])


def detect_posture(
    enabled: Iterable[str] | None, *, operate_enabled: bool
) -> str:
    """Return privacy | lab | operate for UI summary."""
    if operate_enabled:
        return POSTURE_OPERATE
    if normalize_enabled(enabled):
        return POSTURE_LAB
    return POSTURE_PRIVACY


def posture_label(posture: str) -> str:
    return {
        POSTURE_PRIVACY: "Privacy",
        POSTURE_LAB: "Lab",
        POSTURE_OPERATE: "Operate",
    }.get(posture, "Privacy")


def enabled_summary(
    enabled: Iterable[str] | None, *, operate_enabled: bool = False
) -> str:
    ids = normalize_enabled(enabled)
    posture = posture_label(detect_posture(ids, operate_enabled=operate_enabled))
    if not ids and not operate_enabled:
        return f"{posture} · core path console only"
    parts: list[str] = [posture]
    if ids:
        titles = [plugin_by_id(i).title for i in ids if plugin_by_id(i)]
        parts.append(f"{len(ids)} pack(s): " + ", ".join(titles))
    if operate_enabled:
        parts.append("Operate rail on")
    return " · ".join(parts)
