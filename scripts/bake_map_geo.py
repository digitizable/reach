#!/usr/bin/env python3
"""Bake Home-map geometry from Natural Earth GeoJSON.

Produces lat/lon JSON under data/assets/:
  - world-landmass.json  continuous continents + islands
  - world-lakes.json     major inland water (cutouts)
  - world-borders.json   admin-0 boundary linework
  - world-countries.json ISO2 closed rings + focus bbox/centroid

Source (Public Domain): Natural Earth
  https://www.naturalearthdata.com/
  https://github.com/nvkelso/natural-earth-vector

Usage (from repo root):
  python3 scripts/bake_map_geo.py [--cache-dir /tmp/ne_bake]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "data" / "assets"

NE_BASE = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"
)

# Files we bake from (50m land/lakes/borders for quality; 110m countries for flags)
SOURCES = {
    "land": "ne_50m_land.geojson",
    "lakes": "ne_50m_lakes.geojson",
    "borders": "ne_50m_admin_0_boundary_lines_land.geojson",
    "countries": "ne_110m_admin_0_countries.geojson",
}

# Douglas–Peucker tolerances in degrees (≈ km at equator: 0.05° ≈ 5.5 km)
TOL_LAND = 0.06
TOL_LAKES = 0.04
TOL_BORDERS = 0.05
TOL_COUNTRIES = 0.08

# Keep major inland water only (scalerank from Natural Earth)
LAKE_MAX_SCALERANK = 2
# Drop tiny lake rings after simplify (deg² shoelace; ~Lake Geneva scale)
LAKE_MIN_AREA = 0.15


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 1000:
        return
    print(f"  download {dest.name} …", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Reach-map-bake/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        dest.write_bytes(resp.read())


def _perp_dist(p, a, b) -> float:
    """Perpendicular distance from p to segment a→b (2D)."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def douglas_peucker(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    if len(pts) < 3:
        return pts
    # iterative stack DP
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        max_d = -1.0
        max_i = -1
        a, b = pts[i], pts[j]
        for k in range(i + 1, j):
            d = _perp_dist(pts[k], a, b)
            if d > max_d:
                max_d = d
                max_i = k
        if max_d > tol and max_i >= 0:
            keep[max_i] = True
            stack.append((i, max_i))
            stack.append((max_i, j))
    out = [pts[i] for i, k in enumerate(keep) if k]
    return out if len(out) >= 2 else pts[:2]


def _ring_area_deg2(ring: list[tuple[float, float]]) -> float:
    if len(ring) < 3:
        return 0.0
    a = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        a += x1 * y2 - x2 * y1
    # ring is (lat, lon) — treat as plane for relative size only
    return abs(a) * 0.5


def _close_ring(ring: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(ring) < 2:
        return ring
    if ring[0] != ring[-1]:
        return ring + [ring[0]]
    return ring


def _coords_to_latlon(ring) -> list[tuple[float, float]]:
    """GeoJSON ring [lon, lat] → (lat, lon) tuples."""
    return [(float(p[1]), float(p[0])) for p in ring if len(p) >= 2]


def geojson_exteriors_and_holes(
    geom: dict,
) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]]]:
    """Split Polygon/MultiPolygon into exterior rings and hole rings (lat, lon).

    Natural Earth land puts the Caspian Sea (etc.) as a *hole* inside Eurasia —
    holes must be cut out, never filled as land.
    """
    t = geom.get("type")
    coords = geom.get("coordinates") or []
    exteriors: list[list[tuple[float, float]]] = []
    holes: list[list[tuple[float, float]]] = []

    def add_poly(poly):
        if not poly:
            return
        ext = _coords_to_latlon(poly[0])
        if len(ext) >= 3:
            exteriors.append(ext)
        for hole in poly[1:]:
            h = _coords_to_latlon(hole)
            if len(h) >= 3:
                holes.append(h)

    if t == "Polygon":
        add_poly(coords)
    elif t == "MultiPolygon":
        for poly in coords:
            add_poly(poly)
    return exteriors, holes


def geojson_rings_latlon(geom: dict) -> list[list[tuple[float, float]]]:
    """All rings (exteriors + holes) — for lakes where every ring is water."""
    ext, holes = geojson_exteriors_and_holes(geom)
    return ext + holes


def geojson_lines_latlon(geom: dict) -> list[list[tuple[float, float]]]:
    t = geom.get("type")
    coords = geom.get("coordinates") or []
    out: list[list[tuple[float, float]]] = []
    if t == "LineString":
        pts = [(float(p[1]), float(p[0])) for p in coords if len(p) >= 2]
        if len(pts) >= 2:
            out.append(pts)
    elif t == "MultiLineString":
        for line in coords:
            pts = [(float(p[1]), float(p[0])) for p in line if len(p) >= 2]
            if len(pts) >= 2:
                out.append(pts)
    return out


def simplify_ring(ring: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    # Drop duplicate close point for DP, re-close after
    closed = len(ring) >= 2 and ring[0] == ring[-1]
    core = ring[:-1] if closed and len(ring) > 2 else ring
    if len(core) < 3:
        return _close_ring(list(core)) if closed else list(core)
    simp = douglas_peucker(core, tol)
    if closed:
        simp = _close_ring(simp)
    return simp


def simplify_line(line: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    if len(line) < 3:
        return line
    return douglas_peucker(line, tol)


def _serialize_rings(
    rings: list[list[tuple[float, float]]],
    *,
    tol: float,
    min_area: float = 0.0,
) -> list[list[list[float]]]:
    out: list[list[list[float]]] = []
    for ring in rings:
        simp = simplify_ring(ring, tol)
        if len(simp) < 4:
            continue
        if min_area > 0 and _ring_area_deg2(simp) < min_area:
            continue
        out.append([[round(lat, 4), round(lon, 4)] for lat, lon in simp])
    return out


def extract_landmass(
    path: Path,
    *,
    tol: float,
) -> tuple[list[list[list[float]]], list[list[list[float]]]]:
    """Return (exterior land rings, hole rings to treat as inland seas/lakes)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    exteriors: list[list[tuple[float, float]]] = []
    holes: list[list[tuple[float, float]]] = []
    for feat in data.get("features") or []:
        geom = feat.get("geometry") or {}
        ext, hol = geojson_exteriors_and_holes(geom)
        exteriors.extend(ext)
        holes.extend(hol)
    # Tiny islands/slivers: keep small exteriors (islands matter); drop noise
    land = _serialize_rings(exteriors, tol=tol, min_area=0.002)
    # Land holes are enclosed seas (Caspian) — always keep as water cutouts
    hole_water = _serialize_rings(holes, tol=tol, min_area=0.0)
    return land, hole_water


def extract_polygons(
    path: Path,
    *,
    tol: float,
    min_area: float = 0.0,
    lake_filter: bool = False,
) -> list[list[list[float]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rings_acc: list[list[tuple[float, float]]] = []
    for feat in data.get("features") or []:
        props = feat.get("properties") or {}
        if lake_filter:
            sr = props.get("scalerank")
            try:
                if sr is not None and int(sr) > LAKE_MAX_SCALERANK:
                    continue
            except (TypeError, ValueError):
                pass
            fcla = (props.get("featurecla") or "").lower()
            if fcla and "lake" not in fcla and "reservoir" not in fcla:
                if "river" in fcla:
                    continue
        geom = feat.get("geometry") or {}
        # Lakes: exteriors only (holes inside lakes are islands — skip for cutout)
        ext, _holes = geojson_exteriors_and_holes(geom)
        rings_acc.extend(ext)
    return _serialize_rings(rings_acc, tol=tol, min_area=min_area)


def extract_lines(path: Path, *, tol: float) -> list[list[list[float]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    lines_out: list[list[list[float]]] = []
    for feat in data.get("features") or []:
        geom = feat.get("geometry") or {}
        for line in geojson_lines_latlon(geom):
            simp = simplify_line(line, tol)
            if len(simp) < 2:
                continue
            lines_out.append([[round(lat, 4), round(lon, 4)] for lat, lon in simp])
    return lines_out


def _iso2(props: dict) -> str | None:
    for key in ("ISO_A2_EH", "ISO_A2", "iso_a2", "WB_A2"):
        v = props.get(key)
        if not v or not isinstance(v, str):
            continue
        v = v.strip().lower()
        if len(v) == 2 and v.isalpha() and v != "-1":
            return v
    return None


def extract_countries(path: Path, *, tol: float) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    countries: dict[str, dict] = {}
    for feat in data.get("features") or []:
        props = feat.get("properties") or {}
        code = _iso2(props)
        if not code:
            continue
        geom = feat.get("geometry") or {}
        rings: list[list[list[float]]] = []
        lats: list[float] = []
        lons: list[float] = []
        for ring in geojson_rings_latlon(geom):
            # Exterior rings only for flag fill (index 0 of each polygon)
            # MultiPolygon: first ring of each poly is exterior
            simp = simplify_ring(ring, tol)
            if len(simp) < 4:
                continue
            # Heuristic: large area rings are exteriors; skip tiny holes
            if _ring_area_deg2(simp) < 0.02:
                continue
            rings.append([[round(lat, 4), round(lon, 4)] for lat, lon in simp])
            for lat, lon in simp:
                lats.append(lat)
                lons.append(lon)
        if not rings:
            continue
        lat_min, lat_max = min(lats), max(lats)
        lon_min, lon_max = min(lons), max(lons)
        # Centroid ≈ bbox center (good enough for camera focus)
        entry = {
            "rings": rings,
            "bbox": [
                round(lat_min, 4),
                round(lon_min, 4),
                round(lat_max, 4),
                round(lon_max, 4),
            ],
            "centroid": [
                round((lat_min + lat_max) * 0.5, 4),
                round((lon_min + lon_max) * 0.5, 4),
            ],
        }
        # Prefer larger ring set if duplicate ISO (e.g. France)
        prev = countries.get(code)
        if prev is None or sum(len(r) for r in rings) > sum(
            len(r) for r in prev["rings"]
        ):
            countries[code] = entry
    return countries


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("/tmp/ne_bake"),
        help="Directory for downloaded Natural Earth GeoJSON",
    )
    args = ap.parse_args()
    cache: Path = args.cache_dir
    cache.mkdir(parents=True, exist_ok=True)

    print("Fetching Natural Earth GeoJSON…")
    paths = {}
    for key, fname in SOURCES.items():
        dest = cache / fname
        _download(f"{NE_BASE}/{fname}", dest)
        paths[key] = dest

    print("Baking landmass (exteriors only; holes → inland seas)…")
    land, land_holes = extract_landmass(paths["land"], tol=TOL_LAND)
    print(
        f"  {len(land)} land rings, {sum(len(r) for r in land)} pts; "
        f"{len(land_holes)} land-hole seas"
    )

    print("Baking lakes (major inland water + land holes e.g. Caspian)…")
    lakes = extract_polygons(
        paths["lakes"],
        tol=TOL_LAKES,
        min_area=LAKE_MIN_AREA,
        lake_filter=True,
    )
    # Enclosed seas stored as holes in the land layer (Caspian Sea, etc.)
    lakes = lakes + land_holes
    print(f"  {len(lakes)} water rings, {sum(len(r) for r in lakes)} pts")

    print("Baking borders…")
    borders = extract_lines(paths["borders"], tol=TOL_BORDERS)
    print(f"  {len(borders)} lines, {sum(len(r) for r in borders)} pts")

    print("Baking countries (flags + focus)…")
    countries = extract_countries(paths["countries"], tol=TOL_COUNTRIES)
    print(f"  {len(countries)} ISO2")

    ASSETS.mkdir(parents=True, exist_ok=True)

    land_doc = {
        "format": "latlon-rings",
        "source": "Natural Earth 50m land (simplified)",
        "license": "public domain",
        "rings": land,
    }
    lakes_doc = {
        "format": "latlon-rings",
        "source": (
            "Natural Earth 50m lakes (scalerank≤2) + land-polygon holes "
            "(enclosed seas e.g. Caspian); simplified"
        ),
        "license": "public domain",
        "rings": lakes,
    }
    borders_doc = {
        "format": "latlon-lines",
        "source": "Natural Earth 50m admin-0 boundary lines (simplified)",
        "license": "public domain",
        "lines": borders,
    }
    # Keep backward-compatible "countries" map of rings; also store meta
    countries_rings = {cc: ent["rings"] for cc, ent in countries.items()}
    countries_doc = {
        "format": "latlon-countries",
        "source": "Natural Earth 110m admin-0 countries (simplified)",
        "license": "public domain",
        "countries": countries_rings,
        "meta": {
            cc: {"bbox": ent["bbox"], "centroid": ent["centroid"]}
            for cc, ent in countries.items()
        },
    }

    def write(name: str, doc: dict) -> None:
        path = ASSETS / name
        path.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        print(f"  wrote {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")

    write("world-landmass.json", land_doc)
    write("world-lakes.json", lakes_doc)
    write("world-borders.json", borders_doc)
    write("world-countries.json", countries_doc)

    # Legacy alias: old code paths load world-land.json as pixel polys.
    # Keep a small pointer note by rewriting landmass-compatible? Better leave
    # world-land.json in place for one release but map prefers landmass.
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
