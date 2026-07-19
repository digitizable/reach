"""Territory / region model for Reach (ingress) page.

China is first-class (deep research + defaults). Other territories share the same
topology: inbound host you control, or Inverse Snowflake dial-out. Underlay
(VPN) still required — never dial a censored-region endpoint from clearnet.

Map silhouettes live under data/assets/map-XX.svg (djaiss/mapsicon).
Nav uses globe.svg; each territory map is shown on the Reach page.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Territory:
    """A place you may be reaching *into* from outside."""

    code: str  # ISO-ish, e.g. CN
    name: str
    short_name: str
    """Adjective / label for copy: "China-side", "Iran-side"."""
    side_label: str
    """One-line product blurb."""
    blurb: str
    """Optional map silhouette under data/assets/ (e.g. map-cn.svg)."""
    map_asset: str | None = None
    """Deprecated alias — same as map_asset for older callers."""
    flag_asset: str | None = None
    """Optional research study URL."""
    study_url: str | None = None
    """Default accept port suggestion for reverse."""
    default_accept_port: int = 18443
    """Research pack depth: china | general."""
    research_depth: str = "general"

    def silhouette_asset(self) -> str | None:
        return self.map_asset or self.flag_asset


# Order = UI combo order. CN first.
TERRITORIES: tuple[Territory, ...] = (
    Territory(
        code="CN",
        name="China (mainland)",
        short_name="China",
        side_label="China-side",
        blurb="Outside → mainland-reachable host or Inverse Snowflake dial-out. "
        "GFW-aware research path; never clearnet to CN endpoints.",
        map_asset="map-cn.svg",
        flag_asset="mainland-china.svg",  # same silhouette family; page uses map-cn
        study_url="https://anguish.sh/studies/reaching-into-china-from-outside",
        research_depth="china",
    ),
    Territory(
        code="IR",
        name="Iran",
        short_name="Iran",
        side_label="Iran-side",
        blurb="Outside → host or dial-out agent under Iranian routing. "
        "Same doors (inbound / Inverse Snowflake); local DPI differs.",
        map_asset="map-ir.svg",
        research_depth="general",
    ),
    Territory(
        code="RU",
        name="Russia",
        short_name="Russia",
        side_label="Russia-side",
        blurb="Outside → host or dial-out agent. TSPU / local policy; same topology model.",
        map_asset="map-ru.svg",
        research_depth="general",
    ),
    Territory(
        code="TR",
        name="Turkey",
        short_name="Turkey",
        side_label="Turkey-side",
        blurb="Outside → host or dial-out. Court-ordered blocks / DPI episodes; same doors.",
        map_asset="map-tr.svg",
        research_depth="general",
    ),
    Territory(
        code="CU",
        name="Cuba",
        short_name="Cuba",
        side_label="Cuba-side",
        blurb="Outside → host or dial-out where connectivity allows. Same reverse model.",
        map_asset="map-cu.svg",
        research_depth="general",
    ),
    Territory(
        code="AE",
        name="UAE",
        short_name="UAE",
        side_label="UAE-side",
        blurb="Outside → host or dial-out. Filtering + logging norms; VPN underlay still required.",
        map_asset="map-ae.svg",
        research_depth="general",
    ),
    Territory(
        code="XX",
        name="Other / custom",
        short_name="Custom",
        side_label="target-side",
        blurb="Generic territory: you bring the host or Inverse Snowflake peer. "
        "No region-specific research pack — same engineering model.",
        map_asset="globe.svg",
        research_depth="general",
    ),
)

_BY_CODE = {t.code: t for t in TERRITORIES}
DEFAULT_TERRITORY_CODE = "CN"


def get_territory(code: str | None) -> Territory:
    if not code:
        return _BY_CODE[DEFAULT_TERRITORY_CODE]
    t = _BY_CODE.get(code.strip().upper())
    return t or _BY_CODE[DEFAULT_TERRITORY_CODE]


def territory_codes() -> list[str]:
    return [t.code for t in TERRITORIES]


def territory_labels() -> list[str]:
    return [f"{t.name}" for t in TERRITORIES]


def parse_territory_from_notes(notes: str | None) -> str | None:
    """Extract territory=XX from profile notes."""
    if not notes:
        return None
    for part in notes.replace(";", "·").split("·"):
        p = part.strip()
        if p.lower().startswith("territory="):
            return p.split("=", 1)[1].strip().upper() or None
    return None
