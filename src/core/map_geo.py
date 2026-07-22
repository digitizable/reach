"""Home Mullvad map geometry: landmasses, lakes, borders, country shapes.

Assets (lat/lon, Natural Earth derived — see scripts/bake_map_geo.py):
  - world-landmass.json  continuous continents + islands
  - world-lakes.json     major inland water (cut out of land)
  - world-borders.json   admin-0 political boundary lines
  - world-countries.json ISO2 rings + focus meta (bbox/centroid)
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app_config import project_root


def _assets() -> Path:
    return project_root() / "data" / "assets"


def _load_json(name: str) -> dict:
    path = _assets() / name
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _parse_rings(raw: list) -> list[list[tuple[float, float]]]:
    out: list[list[tuple[float, float]]] = []
    for ring in raw or []:
        if not isinstance(ring, list) or len(ring) < 3:
            continue
        pts: list[tuple[float, float]] = []
        for p in ring:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                pts.append((float(p[0]), float(p[1])))  # lat, lon
        if len(pts) >= 3:
            out.append(pts)
    return out


@lru_cache(maxsize=1)
def load_landmass_rings() -> list[list[tuple[float, float]]]:
    """Connected continents + islands as (lat, lon) rings."""
    data = _load_json("world-landmass.json")
    if data.get("rings"):
        return _parse_rings(data["rings"])
    # Legacy fallback: world-land.json was equirectangular 720×360
    legacy = _load_json("world-land.json")
    if not legacy.get("polys"):
        return []
    w = float(legacy.get("w") or 720.0)
    h = float(legacy.get("h") or 360.0)
    out: list[list[tuple[float, float]]] = []
    for ring in legacy["polys"]:
        pts: list[tuple[float, float]] = []
        for p in ring:
            if not isinstance(p, (list, tuple)) or len(p) < 2:
                continue
            wx, wy = float(p[0]), float(p[1])
            lon = wx / w * 360.0 - 180.0
            lat = 90.0 - wy / h * 180.0
            pts.append((lat, lon))
        if len(pts) >= 3:
            out.append(pts)
    return out


@lru_cache(maxsize=1)
def load_lake_rings() -> list[list[tuple[float, float]]]:
    """Major inland water bodies as (lat, lon) rings — cut out of land fill."""
    data = _load_json("world-lakes.json")
    return _parse_rings(data.get("rings") or [])


@lru_cache(maxsize=1)
def load_border_lines() -> list[list[tuple[float, float]]]:
    """Admin-0 political boundaries as (lat, lon) polylines."""
    data = _load_json("world-borders.json")
    raw = data.get("lines") or []
    out: list[list[tuple[float, float]]] = []
    for line in raw:
        if not isinstance(line, list) or len(line) < 2:
            continue
        pts: list[tuple[float, float]] = []
        for p in line:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                pts.append((float(p[0]), float(p[1])))
        if len(pts) >= 2:
            out.append(pts)
    return out


@lru_cache(maxsize=1)
def load_country_rings() -> dict[str, list[list[tuple[float, float]]]]:
    """ISO2 → closed rings (lat, lon) for flag fills + focus shapes."""
    data = _load_json("world-countries.json")
    raw = data.get("countries") or {}
    out: dict[str, list[list[tuple[float, float]]]] = {}
    for cc, rings in raw.items():
        code = str(cc).lower()
        if len(code) != 2:
            continue
        parsed = _parse_rings(rings if isinstance(rings, list) else [])
        # Drop degenerate
        parsed = [r for r in parsed if len(r) >= 4]
        if parsed:
            out[code] = parsed
    return out


@lru_cache(maxsize=1)
def load_country_meta() -> dict[str, dict]:
    """ISO2 → {bbox: [lat0,lon0,lat1,lon1], centroid: [lat, lon]}."""
    data = _load_json("world-countries.json")
    raw = data.get("meta") or {}
    out: dict[str, dict] = {}
    for cc, ent in raw.items():
        code = str(cc).lower()
        if len(code) != 2 or not isinstance(ent, dict):
            continue
        bbox = ent.get("bbox")
        centroid = ent.get("centroid")
        entry: dict = {}
        if isinstance(bbox, list) and len(bbox) >= 4:
            entry["bbox"] = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
        if isinstance(centroid, list) and len(centroid) >= 2:
            entry["centroid"] = [float(centroid[0]), float(centroid[1])]
        if entry:
            out[code] = entry
    # Derive from rings if meta missing
    if not out:
        for code, rings in load_country_rings().items():
            lats = [p[0] for r in rings for p in r]
            lons = [p[1] for r in rings for p in r]
            if not lats:
                continue
            lat0, lat1 = min(lats), max(lats)
            lon0, lon1 = min(lons), max(lons)
            out[code] = {
                "bbox": [lat0, lon0, lat1, lon1],
                "centroid": [(lat0 + lat1) * 0.5, (lon0 + lon1) * 0.5],
            }
    return out


def country_focus(code: str) -> tuple[float, float, float] | None:
    """Return (lat, lon, angular_half_span_rad) for camera focus, or None."""
    import math

    code = (code or "").lower()
    meta = load_country_meta().get(code)
    if not meta:
        rings = load_country_rings().get(code)
        if not rings:
            return None
        lats = [p[0] for r in rings for p in r]
        lons = [p[1] for r in rings for p in r]
        lat_c = (min(lats) + max(lats)) * 0.5
        lon_c = (min(lons) + max(lons)) * 0.5
        # rough span
        dlat = math.radians(max(lats) - min(lats))
        dlon = math.radians(max(lons) - min(lons)) * max(
            0.2, math.cos(math.radians(lat_c))
        )
        half = max(math.hypot(dlat, dlon) * 0.55, math.radians(6.0))
        return lat_c, lon_c, half

    c = meta.get("centroid") or [0.0, 0.0]
    lat_c, lon_c = float(c[0]), float(c[1])
    bbox = meta.get("bbox")
    if bbox and len(bbox) >= 4:
        lat0, lon0, lat1, lon1 = bbox
        dlat = math.radians(abs(lat1 - lat0))
        dlon = math.radians(abs(lon1 - lon0)) * max(
            0.2, math.cos(math.radians(lat_c))
        )
        half = max(math.hypot(dlat, dlon) * 0.55, math.radians(6.0))
    else:
        half = math.radians(12.0)
    return lat_c, lon_c, half


def warm_map_geo() -> None:
    """Touch all loaders (call off UI thread during bootstrap)."""
    load_landmass_rings()
    load_lake_rings()
    load_border_lines()
    load_country_rings()
    load_country_meta()
