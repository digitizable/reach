"""Custom animated Mullvad relay map viewport (globe + city dots).

- Orthographic land (curved continents) from public-domain country outlines
- Square full-bleed viewport — no circular “ball” clip; map fills the square
- Cached base layer (land/graticule) so idle frames stay cheap
- Mullvad city markers (public API) with throttled pulse
- Fly-to active location; click city to set Mullvad relay (GPL-3 CLI)
"""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable
from functools import lru_cache

from gi.repository import GLib, Gtk

from app_config import project_root
from core.map_country_flags import flag_surface
from core.map_geo import (
    country_focus,
    load_border_lines,
    load_country_meta,
    load_country_rings,
    load_lake_rings,
    load_landmass_rings,
    warm_map_geo,
)
from core.mullvad import (
    RelayCity,
    cli_path,
    get_map_cities,
    load_map_cities_disk,
    set_location,
)

try:
    import cairo  # type: ignore
except Exception:  # pragma: no cover
    cairo = None  # type: ignore

_WORLD_W = 720.0
_WORLD_H = 360.0
# Throttle idle pulse redraws (~12 fps) once the camera settles.
_PULSE_INTERVAL = 1.0 / 12.0
# Snap camera for base-layer cache key while still animating smoothly.
_CACHE_XY_Q = 0.5
_CACHE_SCALE_Q = 0.05
# Cap land-layer rebuild rate during fly/zoom; cities still track live.
_BASE_REBUILD_MIN_DT = 0.10
_BASE_REBUILD_MOVING_DT = 0.18  # coarser while camera is in motion
# Hover hit-test throttle (~30 Hz) — nearest-city over all markers is not free.
_HOVER_MIN_DT = 1.0 / 30.0
# Progressive city fade-in when catalog arrives / refreshes.
_CITY_FADE_SEC = 0.45
# Idle: drop vsync tick; low-rate pulse timer only when markers need animation.
_IDLE_PULSE_MS = 90


def lonlat_to_world(lat: float, lon: float) -> tuple[float, float]:
    x = (lon + 180.0) / 360.0 * _WORLD_W
    y = (90.0 - lat) / 180.0 * _WORLD_H
    return x, y


def world_to_lonlat(wx: float, wy: float) -> tuple[float, float]:
    lon = wx / _WORLD_W * 360.0 - 180.0
    lat = 90.0 - wy / _WORLD_H * 180.0
    return lat, lon


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def angular_distance_rad(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle angular distance in radians."""
    φ1 = math.radians(lat1)
    φ2 = math.radians(lat2)
    dλ = math.radians(lon2 - lon1)
    cos_c = math.sin(φ1) * math.sin(φ2) + math.cos(φ1) * math.cos(φ2) * math.cos(dλ)
    return math.acos(_clamp(cos_c, -1.0, 1.0))


@lru_cache(maxsize=1)
def load_land_polygons() -> tuple[float, float, list[list[tuple[float, float]]]]:
    """Compatibility shim — land is lat/lon landmass rings now."""
    rings = load_landmass_rings()
    return _WORLD_W, _WORLD_H, rings


def land_latlon() -> list[list[tuple[float, float]]]:
    """Connected landmass rings (lat, lon)."""
    return load_landmass_rings()


def land_latlon_lod() -> list[list[tuple[float, float]]]:
    return load_landmass_rings()


def _stride_rings(
    rings: list[list[tuple[float, float]]], step: int = 2
) -> list[list[tuple[float, float]]]:
    """Decimate ring vertices for drag-time paints (keeps endpoints)."""
    if step <= 1:
        return rings
    out: list[list[tuple[float, float]]] = []
    for ring in rings:
        n = len(ring)
        if n < 8:
            out.append(ring)
            continue
        pts = ring[::step]
        if pts[-1] != ring[-1]:
            pts = pts + [ring[-1]]
        if len(pts) >= 3:
            out.append(pts)
    return out


@lru_cache(maxsize=1)
def land_latlon_drag() -> list[list[tuple[float, float]]]:
    """Strided landmass for interactive pan/fly (~8–12ms solid fill)."""
    return _stride_rings(load_landmass_rings(), step=3)


def _ring_core(ring: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop duplicate closing vertex if present."""
    if len(ring) >= 2 and ring[0] == ring[-1]:
        return ring[:-1]
    return ring


def _runs_from_projected(
    pts: list[tuple[float, float] | None], *, closed: bool = False
) -> list[list[tuple[float, float]]]:
    """Split already-projected points into contiguous visible runs."""
    n = len(pts)
    if n < 2:
        return []
    spans: list[tuple[int, int]] = []
    i = 0
    while i < n:
        while i < n and pts[i] is None:
            i += 1
        if i >= n:
            break
        j = i
        while j < n and pts[j] is not None:
            j += 1
        if j - i >= 2:
            spans.append((i, j))
        i = j

    if closed and len(spans) >= 2 and spans[0][0] == 0 and spans[-1][1] == n:
        a0, a1 = spans[-1]
        b0, b1 = spans[0]
        merged = [pts[k] for k in range(a0, a1)] + [pts[k] for k in range(b0, b1)]
        runs: list[list[tuple[float, float]]] = [merged]  # type: ignore[list-item]
        for s, e in spans[1:-1]:
            runs.append([pts[k] for k in range(s, e)])  # type: ignore[misc]
        return [r for r in runs if len(r) >= 2]

    return [[pts[k] for k in range(s, e)] for s, e in spans]  # type: ignore[misc]


def _iter_visible_runs(
    ring: list[tuple[float, float]], proj, *, closed: bool = False
) -> list[list[tuple[float, float]]]:
    """Project a ring and split into contiguous limb-visible polylines.

    For *closed* rings, merges runs that wrap across index 0 so a single front
    coastline is not split into two open polylines.
    """
    if len(ring) < 2:
        return []
    core = _ring_core(ring) if closed else ring
    pts: list[tuple[float, float] | None] = [proj(lat, lon) for lat, lon in core]
    return _runs_from_projected(pts, closed=closed)


def _paint_interactive_land(
    cr,
    land: list,
    proj,
    *,
    cx: float,
    cy: float,
    R: float,
) -> None:
    """Solid land for live camera poses (drag / fly).

    Fully front-facing rings fill solid. The big multipolygon continents often
    sit half on the limb during pan — those are filled via visible runs closed
    safely (limb arc or modest gap), so main land never vanishes mid-drag.
    Shares the live camera with city markers and flags.
    """
    if cairo is None or not land:
        return
    land_r, land_g, land_b = 0.16, 0.20, 0.24
    try:
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
    except Exception:
        pass

    cr.set_source_rgb(land_r, land_g, land_b)

    for ring in land:
        core = _ring_core(ring)
        if len(core) < 3:
            continue
        # One projection pass per ring
        projected: list[tuple[float, float] | None] = [
            proj(lat, lon) for lat, lon in core
        ]
        n = len(projected)
        n_vis = sum(1 for p in projected if p is not None)
        if n_vis < 3:
            continue

        if n_vis == n:
            # Fully front-facing
            p0 = projected[0]
            assert p0 is not None
            cr.move_to(p0[0], p0[1])
            for p in projected[1:]:
                assert p is not None
                cr.line_to(p[0], p[1])
            cr.close_path()
            cr.fill()
            continue

        # Partial: fill each visible run without ocean-chord wedges
        for run in _runs_from_projected(projected, closed=True):
            if len(run) < 4:
                continue
            ch = _chord(run)
            plen = _path_length(run)
            if plen < 6.0:
                continue
            # Skip tiny crumbs; keep substantial land (main continents)
            if plen < 0.08 * R and ch < 0.06 * R:
                continue
            xs = [p[0] for p in run]
            ys = [p[1] for p in run]
            if (max(xs) - min(xs)) > 2.05 * R or (max(ys) - min(ys)) > 2.05 * R:
                continue

            mode = "loop" if ch < 0.20 * plen else "limb"
            cr.new_path()
            if _emit_fill_path(cr, run, cx=cx, cy=cy, R=R, mode=mode):
                cr.fill()


def _ring_fully_visible(ring: list[tuple[float, float]], proj) -> bool:
    core = _ring_core(ring)
    if len(core) < 3:
        return False
    for lat, lon in core:
        if proj(lat, lon) is None:
            return False
    return True


def _path_length(run: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(1, len(run)):
        total += math.hypot(run[i][0] - run[i - 1][0], run[i][1] - run[i - 1][1])
    return total


def _chord(run: list[tuple[float, float]]) -> float:
    if len(run) < 2:
        return 0.0
    return math.hypot(run[0][0] - run[-1][0], run[0][1] - run[-1][1])


def _near_limb(
    x: float, y: float, cx: float, cy: float, R: float, *, tol: float = 0.08
) -> bool:
    d = math.hypot(x - cx, y - cy)
    return abs(d - R) <= tol * max(R, 1.0)


def _append_limb_arc(
    cr,
    x_from: float,
    y_from: float,
    x_to: float,
    y_to: float,
    cx: float,
    cy: float,
    R: float,
    *,
    land_cx: float,
    land_cy: float,
    max_arc_deg: float = 110.0,
) -> bool:
    """Trace orthographic limb from *from* → *to*. Prefer arc away from land mass.

    Returns False if the arc would be a huge pie-slice (caller should skip fill).
    """
    a0 = math.atan2(y_from - cy, x_from - cx)
    a1 = math.atan2(y_to - cy, x_to - cx)
    d_ccw = (a1 - a0) % (2.0 * math.pi)
    d_cw = d_ccw - 2.0 * math.pi  # ≤ 0

    def mid_dist2(delta: float) -> float:
        ang = a0 + 0.5 * delta
        mx = cx + R * math.cos(ang)
        my = cy + R * math.sin(ang)
        return (mx - land_cx) ** 2 + (my - land_cy) ** 2

    # Limb close should hug the horizon on the ocean side of the land blob:
    # pick the arc whose midpoint is *farther* from the land centroid.
    if mid_dist2(d_ccw) >= mid_dist2(d_cw):
        delta = d_ccw
    else:
        delta = d_cw

    if abs(delta) > math.radians(max_arc_deg):
        return False
    if abs(delta) < 1e-4:
        return True
    steps = max(3, int(abs(delta) / math.radians(5.0)))
    for i in range(1, steps + 1):
        ang = a0 + delta * (i / steps)
        cr.line_to(cx + R * math.cos(ang), cy + R * math.sin(ang))
    return True


def _add_land_paths(cr, land: list, proj, *, min_pts: int = 3) -> bool:
    """Append all visible land subpaths to the current context. Returns True if any."""
    any_path = False
    for ring in land:
        if len(ring) < 3:
            continue
        for run in _iter_visible_runs(ring, proj, closed=True):
            if len(run) < min_pts:
                continue
            cr.move_to(run[0][0], run[0][1])
            for x, y in run[1:]:
                cr.line_to(x, y)
            if min_pts >= 3 and _chord(run) <= 0.2 * max(_path_length(run), 1.0):
                cr.close_path()
            any_path = True
    return any_path


def _paint_flag_cover(
    cr,
    flag,
    pts: list[tuple[float, float]],
    *,
    opacity: float,
    filter_best: bool = True,
) -> None:
    """Clip *flag* into polygon *pts* with object-fit cover (no stretch)."""
    if flag is None or len(pts) < 3:
        return
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    bw, bh = x1 - x0, y1 - y0
    if bw < 8.0 or bh < 8.0:
        return
    fw = max(1, flag.get_width())
    fh = max(1, flag.get_height())
    scale = max(bw / fw, bh / fh)
    if scale <= 1e-6:
        return
    ox = x0 + (bw - fw * scale) * 0.5
    oy = y0 + (bh - fh * scale) * 0.5
    alpha = max(0.08, min(0.55, opacity))
    cr.save()
    cr.move_to(pts[0][0], pts[0][1])
    for x, y in pts[1:]:
        cr.line_to(x, y)
    cr.close_path()
    cr.clip()
    cr.translate(ox, oy)
    cr.scale(scale, scale)
    try:
        cr.set_source_surface(flag, 0, 0)
        try:
            cr.get_source().set_filter(
                cairo.FILTER_BEST if filter_best else cairo.FILTER_BILINEAR
            )
        except Exception:
            try:
                cr.get_source().set_filter(cairo.FILTER_BILINEAR)
            except Exception:
                pass
        cr.paint_with_alpha(alpha)
    except Exception:
        pass
    cr.restore()


def _paint_country_flags(
    cr,
    *,
    country_rings: dict[str, list[list[tuple[float, float]]]],
    server_codes: set[str],
    proj,
    opacity: float = 0.32,
) -> None:
    """Clip a national flag into each server-country silhouette (settled quality).

    Per landmass ring (US / Alaska / … separately). **Cover** fit: uniform scale
    (no stretch). High-res textures from ``flag_surface``.
    """
    if cairo is None or not server_codes or not country_rings:
        return
    try:
        cr.set_antialias(cairo.ANTIALIAS_BEST)
    except Exception:
        try:
            cr.set_antialias(cairo.ANTIALIAS_DEFAULT)
        except Exception:
            pass

    for code in sorted(server_codes):
        rings = country_rings.get(code)
        if not rings:
            continue
        flag = flag_surface(code)
        if flag is None:
            continue

        pieces: list[
            tuple[float, list[list[tuple[float, float]]], float, float, float, float]
        ] = []
        for ring in rings:
            if not _ring_fully_visible(ring, proj):
                continue
            runs = [
                r
                for r in _iter_visible_runs(ring, proj, closed=True)
                if len(r) >= 3
            ]
            if not runs:
                continue
            xs = [p[0] for run in runs for p in run]
            ys = [p[1] for run in runs for p in run]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            bw, bh = x1 - x0, y1 - y0
            if bw < 10.0 or bh < 10.0:
                continue
            pieces.append((bw * bh, runs, x0, y0, bw, bh))

        if not pieces:
            continue
        pieces.sort(key=lambda t: -t[0])
        main_area = pieces[0][0]
        min_area = main_area * 0.08

        for i, (area, runs, x0, y0, bw, bh) in enumerate(pieces):
            if i > 0 and area < min_area:
                continue
            # Merge runs into one path for cover paint
            # (cover uses combined bbox via all points)
            pts: list[tuple[float, float]] = []
            for run in runs:
                pts.extend(run)
            if len(pts) < 3:
                continue
            # Clip is multi-run; paint via first run path + full piece clip
            cr.save()
            for run in runs:
                cr.move_to(run[0][0], run[0][1])
                for x, y in run[1:]:
                    cr.line_to(x, y)
                cr.close_path()
            cr.clip()
            fw = max(1, flag.get_width())
            fh = max(1, flag.get_height())
            scale = max(bw / fw, bh / fh)
            if scale <= 1e-6:
                cr.restore()
                continue
            ox = x0 + (bw - fw * scale) * 0.5
            oy = y0 + (bh - fh * scale) * 0.5
            cr.translate(ox, oy)
            cr.scale(scale, scale)
            try:
                cr.set_source_surface(flag, 0, 0)
                try:
                    cr.get_source().set_filter(cairo.FILTER_BEST)
                except Exception:
                    try:
                        cr.get_source().set_filter(cairo.FILTER_BILINEAR)
                    except Exception:
                        pass
                cr.paint_with_alpha(max(0.08, min(0.55, opacity)))
            except Exception:
                pass
            cr.restore()


def _paint_country_flags_fast(
    cr,
    *,
    country_rings: dict[str, list[list[tuple[float, float]]]],
    country_meta: dict[str, dict] | None,
    server_codes: set[str],
    proj,
    opacity: float = 0.30,
    stride: int = 2,
) -> None:
    """Interactive flag wash (~3–5ms): largest ring only, strided verts, centroid cull.

    Same camera as live land/cities — keeps flags locked during drag without the
    full multi-ring settled cost.
    """
    if cairo is None or not server_codes or not country_rings:
        return
    try:
        cr.set_antialias(cairo.ANTIALIAS_DEFAULT)
    except Exception:
        pass
    meta = country_meta or {}
    stride = max(1, int(stride))

    for code in server_codes:
        # Cheap far-side reject via admin centroid
        cent = (meta.get(code) or {}).get("centroid")
        if (
            isinstance(cent, (list, tuple))
            and len(cent) >= 2
            and proj(float(cent[0]), float(cent[1])) is None
        ):
            continue
        rlist = country_rings.get(code)
        if not rlist:
            continue
        # Main landmass only (skip Alaska/islets in the hot path)
        ring = max(rlist, key=len)
        core = _ring_core(ring)
        if len(core) < 4:
            continue
        if stride > 1 and len(core) > 10:
            samp = core[::stride]
            if samp[-1] != core[-1]:
                samp = samp + [core[-1]]
            core = samp
        pts: list[tuple[float, float]] = []
        for lat, lon in core:
            p = proj(lat, lon)
            if p is None:
                pts = []
                break
            pts.append(p)
        if len(pts) < 3:
            continue
        flag = flag_surface(code)
        if flag is None:
            continue
        _paint_flag_cover(cr, flag, pts, opacity=opacity, filter_best=False)


def _project_open_runs(lines: list, proj) -> list[list[tuple[float, float]]]:
    runs: list[list[tuple[float, float]]] = []
    for line in lines:
        if len(line) < 2:
            continue
        for run in _iter_visible_runs(line, proj, closed=False):
            if len(run) >= 2:
                runs.append(run)
    return runs


def _emit_fill_path(
    cr,
    run: list[tuple[float, float]],
    *,
    cx: float,
    cy: float,
    R: float,
    mode: str,
) -> bool:
    """Build a closed fill path. *mode*: 'loop' | 'limb'. Returns False if unsafe."""
    if len(run) < 3:
        return False
    cr.move_to(run[0][0], run[0][1])
    for x, y in run[1:]:
        cr.line_to(x, y)
    if mode == "loop":
        cr.close_path()
        return True
    # Limb-close for partial continents (no straight ocean chord)
    x0, y0 = run[0]
    x1, y1 = run[-1]
    ch = math.hypot(x0 - x1, y0 - y1)
    plen = _path_length(run)
    on_limb = _near_limb(x0, y0, cx, cy, R, tol=0.14) and _near_limb(
        x1, y1, cx, cy, R, tol=0.14
    )
    lcx = sum(p[0] for p in run) / len(run)
    lcy = sum(p[1] for p in run) / len(run)
    if on_limb:
        if _append_limb_arc(
            cr,
            x1,
            y1,
            x0,
            y0,
            cx,
            cy,
            R,
            land_cx=lcx,
            land_cy=lcy,
            max_arc_deg=110.0,
        ):
            cr.close_path()
            return True
    # Modest gap: safe to close as a loop (avoids stroke-only continents while pan)
    if plen > 12.0 and ch < 0.28 * plen and ch < 0.55 * R:
        cr.close_path()
        return True
    return False


def _classify_ring_fills(
    ring: list[tuple[float, float]], proj
) -> list[tuple[list[tuple[float, float]], str]]:
    """Return (run, mode) for fillable pieces of a closed ring."""
    if len(ring) < 3:
        return []
    core = _ring_core(ring)
    if len(core) < 3:
        return []
    pts: list[tuple[float, float] | None] = [proj(lat, lon) for lat, lon in core]
    n = len(pts)
    n_vis = sum(1 for p in pts if p is not None)
    if n_vis < 3:
        return []

    # Fully on the front hemisphere → solid fill
    if n_vis == n:
        run = [p for p in pts if p is not None]
        return [(run, "loop")]  # type: ignore[list-item]

    # Majority-visible: keep solid continents while panning (was stroke-only)
    frac = n_vis / float(n)
    runs = _iter_visible_runs(ring, proj, closed=True)
    out: list[tuple[list[tuple[float, float]], str]] = []
    for run in runs:
        if len(run) < 4:
            continue
        ch = _chord(run)
        plen = _path_length(run)
        if plen < 6.0:
            continue
        if ch < 0.18 * plen:
            # Nearly closed (few verts behind limb) → treat as solid loop
            out.append((run, "loop"))
        elif frac >= 0.55:
            # Most of the ring is still in front — try limb close, else loop if
            # the open gap is modest relative to the earth (handled in emit).
            out.append((run, "limb"))
        else:
            # Heavily clipped — limb fill only when endpoints sit on the horizon
            out.append((run, "limb"))
    return out


def _fill_landmass_only(
    cr,
    land: list,
    proj,
    *,
    cx: float,
    cy: float,
    R: float,
    light: bool = False,
) -> None:
    """Fill continuous continents + islands (physical land only).

    Never straight-close a limb-clipped ring — that caused Atlantic pie wedges
    and random blobs. Fully front-facing rings fill solid; partial rings either
    close along the horizon or are stroked only.
    """
    if cairo is None:
        return
    land_r, land_g, land_b = 0.16, 0.20, 0.24
    try:
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
    except Exception:
        pass
    try:
        cr.set_antialias(cairo.ANTIALIAS_DEFAULT if light else cairo.ANTIALIAS_GOOD)
    except Exception:
        pass

    fill_paths: list[tuple[list[tuple[float, float]], str]] = []
    stroke_only: list[list[tuple[float, float]]] = []
    for ring in land:
        if len(ring) < 3:
            continue
        pieces = _classify_ring_fills(ring, proj)
        if not pieces:
            # Still draw coastline for partials we won't fill
            for run in _iter_visible_runs(ring, proj, closed=True):
                if len(run) >= 2:
                    stroke_only.append(run)
            continue
        for run, mode in pieces:
            # Guard: refuse only extreme viewport-spanning glitches (not real continents)
            xs = [p[0] for p in run]
            ys = [p[1] for p in run]
            if (max(xs) - min(xs)) > 2.05 * R or (max(ys) - min(ys)) > 2.05 * R:
                stroke_only.append(run)
                continue
            fill_paths.append((run, mode))

    cr.set_source_rgb(land_r, land_g, land_b)
    filled: list[tuple[list[tuple[float, float]], str]] = []
    for run, mode in fill_paths:
        cr.new_path()
        if _emit_fill_path(cr, run, cx=cx, cy=cy, R=R, mode=mode):
            cr.fill()
            filled.append((run, mode))
        else:
            stroke_only.append(run)

    # Coastline for unfilled partials only (open stroke — no ocean chords)
    if stroke_only:
        cr.set_source_rgb(land_r, land_g, land_b)
        cr.set_line_width(1.8 if light else 1.5)
        for run in stroke_only:
            if len(run) < 2:
                continue
            cr.move_to(run[0][0], run[0][1])
            for x, y in run[1:]:
                cr.line_to(x, y)
            cr.stroke()
    # Soft edge on solid fills
    if filled:
        cr.set_source_rgb(land_r, land_g, land_b)
        cr.set_line_width(1.4 if light else 1.6)
        for run, mode in filled:
            cr.move_to(run[0][0], run[0][1])
            for x, y in run[1:]:
                cr.line_to(x, y)
            if mode == "loop" or _chord(run) < 0.2 * max(_path_length(run), 1.0):
                cr.close_path()
            cr.stroke()


def _cut_lakes(
    cr,
    lakes: list,
    proj,
    *,
    cx: float,
    cy: float,
    R: float,
    light: bool = False,
) -> None:
    """Punch major inland water through land (and flags) so ocean shows."""
    if cairo is None or not lakes:
        return
    border_r, border_g, border_b = 0.07, 0.10, 0.14
    paths: list[tuple[list[tuple[float, float]], str]] = []
    for ring in lakes:
        paths.extend(_classify_ring_fills(ring, proj))
    if not paths:
        return

    try:
        cr.set_operator(cairo.OPERATOR_CLEAR)
        for run, mode in paths:
            cr.new_path()
            if _emit_fill_path(cr, run, cx=cx, cy=cy, R=R, mode=mode):
                cr.fill()
        cr.set_operator(cairo.OPERATOR_OVER)
    except Exception:
        cr.set_source_rgba(0.06, 0.09, 0.13, 1.0)
        for run, mode in paths:
            cr.new_path()
            if _emit_fill_path(cr, run, cx=cx, cy=cy, R=R, mode=mode):
                cr.fill()
    if not light:
        cr.set_source_rgba(border_r, border_g, border_b, 0.35)
        cr.set_line_width(0.8)
        for run, mode in paths:
            if mode != "loop":
                continue
            cr.move_to(run[0][0], run[0][1])
            for x, y in run[1:]:
                cr.line_to(x, y)
            cr.close_path()
            cr.stroke()


def _stroke_borders(cr, borders: list, proj, *, light: bool = False) -> None:
    """Admin-0 political boundary linework."""
    if cairo is None or not borders:
        return
    border_runs = _project_open_runs(borders, proj)
    if not border_runs:
        return
    border_r, border_g, border_b = 0.07, 0.10, 0.14
    try:
        cr.set_antialias(cairo.ANTIALIAS_GOOD)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
    except Exception:
        pass
    alpha = 0.48 if light else 0.70
    width = 0.85 if light else 1.05
    cr.set_source_rgba(border_r, border_g, border_b, alpha)
    cr.set_line_width(width)
    for run in border_runs:
        # Skip pathological long segments (limb wrap artifacts)
        if _chord(run) > 0 and _path_length(run) > 0:
            # Drop runs with a single jump larger than half the path (glitch)
            max_step = 0.0
            for i in range(1, len(run)):
                max_step = max(
                    max_step,
                    math.hypot(run[i][0] - run[i - 1][0], run[i][1] - run[i - 1][1]),
                )
            if max_step > 80 and max_step > 0.45 * _path_length(run):
                continue
        cr.move_to(run[0][0], run[0][1])
        for x, y in run[1:]:
            cr.line_to(x, y)
        cr.stroke()


def _fill_projected_land(
    cr,
    land: list,
    proj,
    *,
    cx: float,
    cy: float,
    R: float,
    lakes: list | None = None,
    borders: list | None = None,
    light: bool = False,
) -> None:
    """Landmass fill + lake cutouts + political borders (no per-country land)."""
    _fill_landmass_only(cr, land, proj, cx=cx, cy=cy, R=R, light=light)
    if lakes:
        _cut_lakes(cr, lakes, proj, cx=cx, cy=cy, R=R, light=light)
    if borders:
        _stroke_borders(cr, borders, proj, light=light)


def _project_fast(
    lat: float,
    lon: float,
    *,
    R: float,
    cx: float,
    cy: float,
    sin_φ0: float,
    cos_φ0: float,
    λ0: float,
    limb_eps: float = 0.02,
) -> tuple[float, float] | None:
    """Orthographic project; None if on the far side."""
    φ = math.radians(lat)
    dλ = math.radians(lon) - λ0
    sin_φ = math.sin(φ)
    cos_φ = math.cos(φ)
    cos_dλ = math.cos(dλ)
    cos_c = sin_φ0 * sin_φ + cos_φ0 * cos_φ * cos_dλ
    if cos_c < limb_eps:
        return None
    x = cos_φ * math.sin(dλ)
    y = cos_φ0 * sin_φ - sin_φ0 * cos_φ * cos_dλ
    return cx + R * x, cy - R * y


class MullvadMap(Gtk.Box):
    """Animated globe map with Mullvad relay cities."""

    def __init__(
        self,
        *,
        height: int = 220,
        on_location: Callable[[str, str, str], None] | None = None,
        on_toast: Callable[[str], None] | None = None,
        interactive: bool = True,
        fill: bool = False,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("mullvad-map")
        self.set_hexpand(True)
        self._height = max(160, height)
        self._on_location = on_location
        self._on_toast = on_toast
        self._interactive = interactive
        self._fill = fill

        self._cities: list[RelayCity] = []
        # Camera framing region (may frame live relay without cyan selection)
        self._active_country = ""
        self._active_city = ""
        # Cyan “selected” highlight — independent of camera frame
        self._sel_country = ""
        self._sel_city = ""
        # Live tunnel endpoint (green marker); empty when disconnected.
        self._connected_country = ""
        self._connected_city = ""
        self._hover: RelayCity | None = None
        self._t0 = GLib.get_monotonic_time()
        # CLI / async work in progress (blocks map click). Spinner may lag.
        self._busy = False
        self._loading_gen = 0  # cancel delayed spinner tokens
        self._loading_source: int = 0
        self._last_t = 0.0
        self._last_pulse_t = 0.0

        # Camera focus in world coords + orthographic scale (1 = full hemisphere
        # covering the full rectangular viewport corners).
        self._cam_x = _WORLD_W / 2
        self._cam_y = _WORLD_H / 2
        self._cam_scale = 1.0
        self._target_x = self._cam_x
        self._target_y = self._cam_y
        self._target_scale = 1.0
        # Auto-frame scale is the max zoom-out; scroll may go in closer.
        self._base_scale = 1.0
        self._base_x = self._cam_x
        self._base_y = self._cam_y
        self._max_user_scale = 40.0
        self._pointer_x = 0.0
        self._pointer_y = 0.0
        # Free-nav drag state (any-country mode)
        self._dragging = False
        self._drag_moved = False
        self._drag_prev_ox = 0.0
        self._drag_prev_oy = 0.0
        self._press_xy: tuple[float, float] | None = None

        # Land+graticule (camera-dependent). Lighting/ocean is a separate
        # viewport-only layer so drag rebuilds never “blink” the light.
        self._base_surf = None  # cairo.ImageSurface | None
        self._base_key: tuple | None = None
        self._base_pose: tuple | None = None  # (lat0, lon0, scale, R) of base
        self._last_base_build_t = 0.0
        self._bg_surf = None  # ocean + lighting (rebuild on resize only)
        self._bg_key: tuple | None = None
        # Prefer preloaded landmass; never block __init__ on JSON (bootstrap warms).
        try:
            cached = load_landmass_rings.cache_info().currsize > 0
            self._land_ll = load_landmass_rings() if cached else []
            self._land_drag_ll = land_latlon_drag() if cached else []
        except Exception:
            self._land_ll = []
            self._land_drag_ll = []
        try:
            self._lakes_ll = (
                load_lake_rings() if load_lake_rings.cache_info().currsize else []
            )
        except Exception:
            self._lakes_ll = []
        try:
            self._borders_ll = (
                load_border_lines()
                if load_border_lines.cache_info().currsize
                else []
            )
        except Exception:
            self._borders_ll = []
        try:
            self._country_rings = (
                load_country_rings()
                if load_country_rings.cache_info().currsize
                else {}
            )
        except Exception:
            self._country_rings = {}
        try:
            self._country_meta = (
                load_country_meta()
                if load_country_meta.cache_info().currsize
                else {}
            )
        except Exception:
            self._country_meta = {}
        self._server_country_codes: set[str] = set()
        self._cities_grid: dict[tuple[int, int], list[RelayCity]] = {}
        self._last_hover_t = 0.0
        self._tick_id = 0
        self._pulse_timer_id = 0
        self._city_fade = 1.0  # 0→1 after city list arrives
        self._city_fade_t0 = 0.0
        # Off-thread base bake: generation so stale workers never clobber newer keys
        self._bake_gen = 0
        self._bake_pending_key: tuple | None = None
        self._bake_pending_args: tuple | None = None
        self._bake_inflight = False
        self._flag_opacity = 0.30

        self._area = Gtk.DrawingArea()
        self._area.add_css_class("mullvad-map-viewport")
        self._area.set_hexpand(True)
        self._area.set_halign(Gtk.Align.FILL)
        self._area.set_draw_func(self._draw)
        if interactive:
            self._area.set_cursor_from_name("pointer")

        # Full-bleed rectangle: stretch to the pane — no AspectFrame (that
        # letterboxes and couples width↔height during window resize).
        # content_width left unset so measure is not height-for-width.
        if fill:
            self._area.set_vexpand(True)
            self._area.set_valign(Gtk.Align.FILL)
            self.set_vexpand(True)
            # Minimum natural height only; allocation can grow freely.
            self._area.set_content_height(max(140, self._height))
            try:
                # GTK 4: avoid width-driven height requests on the map.
                self._area.set_content_width(0)
            except Exception:
                pass
        else:
            self._area.set_vexpand(False)
            self._area.set_content_height(self._height)

        # Overlay: circular spinner while set_location / fly-to runs so the
        # map doesn't look frozen between country pick and camera settle.
        self._overlay = Gtk.Overlay()
        self._overlay.set_hexpand(True)
        self._overlay.set_halign(Gtk.Align.FILL)
        if fill:
            self._overlay.set_vexpand(True)
            self._overlay.set_valign(Gtk.Align.FILL)
        self._overlay.set_child(self._area)

        self._busy_layer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._busy_layer.add_css_class("mullvad-map-busy")
        self._busy_layer.set_halign(Gtk.Align.FILL)
        self._busy_layer.set_valign(Gtk.Align.FILL)
        self._busy_layer.set_hexpand(True)
        self._busy_layer.set_vexpand(True)
        self._busy_layer.set_can_target(True)

        busy_center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        busy_center.set_halign(Gtk.Align.CENTER)
        busy_center.set_valign(Gtk.Align.CENTER)
        busy_center.set_hexpand(True)
        busy_center.set_vexpand(True)

        self._spinner = Gtk.Spinner()
        self._spinner.add_css_class("mullvad-map-spinner")
        self._spinner.set_halign(Gtk.Align.CENTER)
        busy_center.append(self._spinner)

        self._busy_label = Gtk.Label(label="Loading…")
        self._busy_label.add_css_class("mullvad-map-busy-label")
        self._busy_label.set_halign(Gtk.Align.CENTER)
        busy_center.append(self._busy_label)

        self._busy_layer.append(busy_center)
        self._busy_layer.set_visible(False)
        self._overlay.add_overlay(self._busy_layer)
        self.append(self._overlay)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self._area.add_controller(motion)

        if interactive:
            # Drag to pan in free-nav (any country); click-to-select on release.
            drag = Gtk.GestureDrag()
            drag.set_button(1)
            drag.connect("drag-begin", self._on_drag_begin)
            drag.connect("drag-update", self._on_drag_update)
            drag.connect("drag-end", self._on_drag_end)
            self._area.add_controller(drag)

            click = Gtk.GestureClick()
            click.set_button(1)
            click.connect("pressed", self._on_press)
            click.connect("released", self._on_release)
            self._area.add_controller(click)

            # Continuous + kinetic: trackpads/mice get fractional dy for smooth zoom.
            scroll = Gtk.EventControllerScroll.new(
                Gtk.EventControllerScrollFlags.VERTICAL
                | Gtk.EventControllerScrollFlags.KINETIC
            )
            scroll.connect("scroll", self._on_scroll)
            self._area.add_controller(scroll)

        self._caption = Gtk.Label(label="", xalign=0.5)
        self._caption.add_css_class("mullvad-map-caption")
        self._caption.set_halign(Gtk.Align.CENTER)
        self._caption.set_wrap(True)
        self.append(self._caption)

        # Start idle pulse path; promote to frame-clock tick when animating.
        self._ensure_anim_loop(force_tick=False)
        # Never load catalog/API on the GTK thread — disk first, network in worker.
        GLib.idle_add(self._bootstrap_map_data)

    def do_unrealize(self) -> None:
        self._stop_anim_loop()
        self._bake_gen += 1  # drop any in-flight base bakes
        self._base_surf = None
        self._base_key = None
        self._bg_surf = None
        self._bg_key = None
        Gtk.Box.do_unrealize(self)

    def _needs_frame_clock(self) -> bool:
        """True when we need vsync (camera ease, city fade, or active pulse)."""
        if self._dragging:
            return True
        if not self._camera_settled():
            return True
        if self._city_fade < 0.999:
            return True
        if self._busy:
            return True
        return False

    def _needs_idle_pulse(self) -> bool:
        return bool(
            self._sel_country
            or self._connected_city
            or self._hover is not None
        )

    def _stop_anim_loop(self) -> None:
        if self._tick_id:
            try:
                self._area.remove_tick_callback(self._tick_id)
            except Exception:
                pass
            self._tick_id = 0
        if self._pulse_timer_id:
            try:
                GLib.source_remove(self._pulse_timer_id)
            except Exception:
                pass
            self._pulse_timer_id = 0

    def _ensure_anim_loop(self, *, force_tick: bool = False) -> None:
        """Use frame clock only while moving; otherwise low-rate pulse or nothing."""
        if not self._area.get_mapped() and not force_tick:
            # Still allow setup before first map
            pass
        want_tick = force_tick or self._needs_frame_clock()
        if want_tick:
            if self._pulse_timer_id:
                try:
                    GLib.source_remove(self._pulse_timer_id)
                except Exception:
                    pass
                self._pulse_timer_id = 0
            if not self._tick_id:
                self._tick_id = self._area.add_tick_callback(self._on_tick)
            return
        # Idle: drop vsync tick
        if self._tick_id:
            try:
                self._area.remove_tick_callback(self._tick_id)
            except Exception:
                pass
            self._tick_id = 0
        if self._needs_idle_pulse():
            if not self._pulse_timer_id:
                self._pulse_timer_id = GLib.timeout_add(
                    _IDLE_PULSE_MS, self._on_idle_pulse
                )
        elif self._pulse_timer_id:
            try:
                GLib.source_remove(self._pulse_timer_id)
            except Exception:
                pass
            self._pulse_timer_id = 0

    def _on_idle_pulse(self) -> bool:
        if self._needs_frame_clock():
            self._pulse_timer_id = 0
            self._ensure_anim_loop(force_tick=True)
            return False
        if not self._area.get_mapped() or not self._needs_idle_pulse():
            self._pulse_timer_id = 0
            return False
        self._area.queue_draw()
        return True

    def _kick_anim(self) -> None:
        """Call after any user/camera change that needs smooth frames."""
        self._ensure_anim_loop(force_tick=True)

    def _bootstrap_map_data(self) -> bool:
        """Fill landmass/lakes/borders + cities without freezing the main loop."""
        if not self._land_ll:

            def land_worker() -> None:
                try:
                    warm_map_geo()
                    land = load_landmass_rings()
                    lakes = load_lake_rings()
                    borders = load_border_lines()
                    countries = load_country_rings()
                    meta = load_country_meta()
                except Exception:
                    land, lakes, borders, countries, meta = [], [], [], {}, {}

                def apply() -> bool:
                    self._land_ll = land
                    self._land_drag_ll = _stride_rings(land, step=2) if land else []
                    self._lakes_ll = lakes
                    self._borders_ll = borders
                    self._country_rings = countries
                    self._country_meta = meta
                    self._invalidate_base()
                    self._area.queue_draw()
                    return False

                GLib.idle_add(apply)

            threading.Thread(target=land_worker, name="map-land", daemon=True).start()

        # Cities: disk immediately, network refresh in background
        disk = load_map_cities_disk()
        if disk:
            self._set_cities(disk)
        self._load_cities_async(prefer_network=True)
        return False

    def _set_cities(self, cities: list[RelayCity]) -> None:
        prev_n = len(self._cities)
        prev_servers = set(self._server_country_codes)
        self._cities = list(cities)
        self._server_country_codes = {
            (c.country_code or "").lower()
            for c in cities
            if c.country_code and len(c.country_code) == 2
        }
        self._rebuild_city_grid()
        self._update_caption()
        if not self._is_free_nav():
            self._fly_to_active()
        # Fade in when first populated or count jumps (network replaced empty/disk)
        if cities and (prev_n == 0 or abs(len(cities) - prev_n) > 3):
            self._city_fade = 0.0
            self._city_fade_t0 = GLib.get_monotonic_time() / 1_000_000.0
            self._kick_anim()
        else:
            self._city_fade = 1.0
        # Server set changed → rebuild land layer so flag fills update
        if self._server_country_codes != prev_servers:
            self._invalidate_base()
        self._area.queue_draw()

    def _rebuild_city_grid(self) -> None:
        """Coarse lon/lat grid for O(1) hover hits instead of scanning all cities."""
        grid: dict[tuple[int, int], list[RelayCity]] = {}
        for c in self._cities:
            # 10° cells
            key = (int(c.longitude // 10), int(c.latitude // 10))
            grid.setdefault(key, []).append(c)
        self._cities_grid = grid

    def _load_cities_async(self, *, prefer_network: bool = False) -> None:
        def worker() -> None:
            cities: list[RelayCity] = []
            try:
                if prefer_network:
                    from core.mullvad import fetch_map_cities

                    try:
                        cities = fetch_map_cities(timeout=6.0)
                    except Exception:
                        cities = get_map_cities(allow_network=False)
                else:
                    cities = get_map_cities(allow_network=True)
            except Exception:
                cities = get_map_cities(allow_network=False)

            def apply() -> bool:
                if cities:
                    self._set_cities(cities)
                return False

            GLib.idle_add(apply)

        threading.Thread(target=worker, name="map-cities", daemon=True).start()

    def _invalidate_base(self) -> None:
        # Drop cache key only — keep last surface on screen until the off-thread
        # bake lands (avoids blank frame + a blocking first-paint hitch).
        self._base_key = None
        # Keep _bg_surf — lighting must not flash when land rebuilds.

    def _is_free_nav(self) -> bool:
        """Any-country mode: pan/zoom freely with no region re-center lock."""
        return not self._active_country or self._active_country == "any"

    def set_loading(
        self,
        busy: bool,
        message: str | None = None,
        *,
        delay_ms: int = 0,
    ) -> None:
        """Show or hide the circular loading overlay on the map.

        *delay_ms* > 0 defers the spinner so fast operations never flash it.
        Fly-to is independent — keep the camera free while CLI work runs.
        """
        # Cancel any pending delayed show.
        if self._loading_source:
            try:
                GLib.source_remove(self._loading_source)
            except Exception:
                pass
            self._loading_source = 0

        if not busy:
            self._busy = False
            self._loading_gen += 1
            self._busy_layer.set_visible(False)
            self._spinner.stop()
            self._update_caption()
            self._area.queue_draw()
            return

        self._busy = True
        self._loading_gen += 1
        gen = self._loading_gen
        label = message or "Loading…"
        self._busy_label.set_text(label)

        def _show() -> bool:
            self._loading_source = 0
            if gen != self._loading_gen or not self._busy:
                return False
            self._busy_layer.set_visible(True)
            self._spinner.start()
            self._caption.set_text(label)
            self._area.queue_draw()
            return False

        if delay_ms > 0:
            # Fast path: map can already be flying; only paint spinner if
            # the background job is still running after the grace period.
            self._loading_source = GLib.timeout_add(delay_ms, _show)
        else:
            _show()

    def set_active(
        self,
        country: str = "",
        city: str = "",
        *,
        highlight: bool = True,
    ) -> None:
        """Frame the camera on a region; optionally cyan-highlight its cities.

        *country* empty/any → free world nav (clears selection highlight).
        *highlight=False* → camera only (used when Any country + connected so
        the live country is framed without all its cities pulsing as selected).

        Camera fly is never gated on the loading spinner.
        """
        country = (country or "").lower()
        city = (city or "").lower()
        if city in ("any",):
            city = ""
        if country in ("any",):
            country = ""
        prev = self._active_country
        same_cam = country == self._active_country and city == self._active_city
        if highlight:
            same_sel = (
                country == self._sel_country and city == self._sel_city
            )
        else:
            same_sel = self._sel_country == "" and self._sel_city == ""
        if same_cam and same_sel:
            self._update_caption()
            return

        self._active_country = country
        self._active_city = city
        if highlight:
            self._sel_country = country
            self._sel_city = city
        else:
            # Frame without selection pulse (clear stale US flash etc.)
            self._sel_country = ""
            self._sel_city = ""

        if self._is_free_nav():
            leaving_region = bool(prev) and prev not in ("", "any")
            self._enter_free_nav(reset_view=leaving_region)
        else:
            self._fly_to_active()
        self._update_caption()
        self._kick_anim()
        self._area.queue_draw()

    def set_connected(self, country: str = "", city: str = "") -> None:
        """Mark the live tunnel city (green pulse). Clear both to disconnect."""
        country = (country or "").lower()
        city = (city or "").lower()
        if city in ("any",):
            city = ""
        if country in ("any",):
            country = ""
        if (
            country == self._connected_country
            and city == self._connected_city
        ):
            return
        self._connected_country = country
        self._connected_city = city
        self._update_caption()
        self._kick_anim()
        self._area.queue_draw()

    def refresh(self) -> None:
        self._load_cities_async(prefer_network=True)

    def _update_caption(self) -> None:
        total = len(self._cities)
        if self._hover is not None:
            h = self._hover
            self._caption.set_text(
                f"{h.city_name}, {h.country_name} · click to select"
            )
            return
        # Caption prefers picker selection; fall back to camera frame
        show_cc = self._sel_country or self._active_country
        show_city = self._sel_city or (
            self._active_city if self._sel_country else ""
        )
        if show_cc and show_cc != "any":
            in_country = [c for c in self._cities if c.country_code == show_cc]
            n = len(in_country)
            bits = [show_cc.upper()]
            if show_city:
                name = show_city.upper()
                for c in in_country:
                    if c.city_code == show_city:
                        name = c.city_name
                        break
                bits.insert(0, name)
            city_word = "city" if n == 1 else "cities"
            if self._sel_country:
                self._caption.set_text(
                    f"Mullvad · {', '.join(bits)} · {n} {city_word} · click to set relay"
                )
            else:
                # Framed for live connection only — free-nav picker is Any country
                self._caption.set_text(
                    f"Mullvad · live in {', '.join(bits)} · drag to pan · click a city"
                )
        else:
            city_word = "city" if total == 1 else "cities"
            self._caption.set_text(
                f"Mullvad · {total} {city_word} · drag to pan · scroll to zoom · click a city"
            )

    def _enter_free_nav(self, *, reset_view: bool) -> None:
        """Configure free navigation; optionally fly back to the whole world."""
        self._base_scale = 1.0
        self._base_x = _WORLD_W / 2
        self._base_y = _WORLD_H / 2
        if reset_view:
            self._target_x = self._base_x
            self._target_y = self._base_y
            self._target_scale = 1.0

    def _fly_to_active(self) -> None:
        """Zoom to the selected country (border shape) or city (tight)."""
        if self._is_free_nav():
            self._enter_free_nav(reset_view=False)
            return

        city_mode = bool(self._active_city)
        matches = [
            c
            for c in self._cities
            if c.country_code == self._active_country
            and (not city_mode or c.city_code == self._active_city)
        ]
        if not matches:
            matches = [
                c for c in self._cities if c.country_code == self._active_country
            ]

        # City: tight frame on relay points
        if city_mode and matches:
            lats = [c.latitude for c in matches]
            lons = [c.longitude for c in matches]
            lat0 = sum(lats) / len(lats)
            lon0 = sum(lons) / len(lons)
            unwrapped = []
            for lon in lons:
                d = lon - lon0
                while d > 180.0:
                    d -= 360.0
                while d < -180.0:
                    d += 360.0
                unwrapped.append(lon0 + d)
            lon0 = sum(unwrapped) / len(unwrapped)
            c_max = math.radians(7.0)
            fill = 0.72
            min_scale, max_scale = 6.0, 28.0
            sin_c = max(math.sin(c_max), 1e-3)
            scale = fill / (math.sqrt(2.0) * sin_c)
            scale = max(min_scale, min(scale, max_scale))
            self._target_x, self._target_y = lonlat_to_world(lat0, lon0)
            self._target_scale = scale
            self._base_scale = scale
            self._base_x = self._target_x
            self._base_y = self._target_y
            return

        # Country: prefer admin shape (bbox/centroid from border rings)
        focus = country_focus(self._active_country)
        if focus is not None:
            lat0, lon0, c_max = focus
            # Expand slightly so coast + lakes inside frame
            c_max = max(c_max * 1.08, math.radians(8.0))
            fill = 0.90
            min_scale, max_scale = 1.15, 14.0
            sin_c = max(math.sin(min(c_max, math.radians(80))), 1e-3)
            scale = fill / (math.sqrt(2.0) * sin_c)
            scale = max(min_scale, min(scale, max_scale))
            self._target_x, self._target_y = lonlat_to_world(lat0, lon0)
            self._target_scale = scale
            self._base_scale = scale
            self._base_x = self._target_x
            self._base_y = self._target_y
            return

        # Fallback: cluster of Mullvad cities in country
        if not matches:
            return
        lats = [c.latitude for c in matches]
        lons = [c.longitude for c in matches]
        lat0 = sum(lats) / len(lats)
        lon0 = sum(lons) / len(lons)
        unwrapped = []
        for lon in lons:
            d = lon - lon0
            while d > 180.0:
                d -= 360.0
            while d < -180.0:
                d += 360.0
            unwrapped.append(lon0 + d)
        lon0 = sum(unwrapped) / len(unwrapped)
        c_max = 0.0
        for c in matches:
            c_max = max(
                c_max,
                angular_distance_rad(lat0, lon0, c.latitude, c.longitude),
            )
        c_max = max(c_max + math.radians(8.0), math.radians(12.0))
        fill = 0.90
        min_scale, max_scale = 1.15, 14.0
        sin_c = max(math.sin(c_max), 1e-3)
        scale = fill / (math.sqrt(2.0) * sin_c)
        scale = max(min_scale, min(scale, max_scale))
        self._target_x, self._target_y = lonlat_to_world(lat0, lon0)
        self._target_scale = scale
        self._base_scale = scale
        self._base_x = self._target_x
        self._base_y = self._target_y

    def _camera_settled(self) -> bool:
        return (
            abs(self._target_x - self._cam_x) < 0.12
            and abs(self._target_y - self._cam_y) < 0.12
            and abs(self._target_scale - self._cam_scale) < 0.006
        )

    def _ease_camera(self, dt: float) -> bool:
        """Ease camera toward target (zoom + pan + re-center). Returns True if moving."""
        # Drag pan is 1:1 — don't fight it with easing.
        if self._dragging:
            return False
        if self._camera_settled():
            # Snap so we don't keep invalidating the base cache with residual drift.
            was_off = (
                abs(self._cam_x - self._target_x) > 1e-6
                or abs(self._cam_y - self._target_y) > 1e-6
                or abs(self._cam_scale - self._target_scale) > 1e-6
            )
            self._cam_x = self._target_x
            self._cam_y = self._target_y
            self._cam_scale = self._target_scale
            if was_off:
                # One sharp full-detail land rebuild after fly/drag settle
                self._invalidate_base()
            return False
        # Critically damped-ish exponential ease — snappy but smooth.
        dist = math.hypot(
            self._target_x - self._cam_x, self._target_y - self._cam_y
        ) + abs(self._target_scale - self._cam_scale) * 24
        # Faster when far (fly-to / big zoom), still smooth near settle.
        rate = 9.5 if dist > 40 else 11.0 if dist > 8 else 14.0
        k = 1.0 - math.exp(-rate * dt)
        self._cam_scale += (self._target_scale - self._cam_scale) * k
        self._cam_x += (self._target_x - self._cam_x) * k
        self._cam_y += (self._target_y - self._cam_y) * k
        return True

    def _pan_by_pixels(self, dx: float, dy: float) -> None:
        """Rotate the globe by a screen-space drag (free-nav)."""
        if dx == 0 and dy == 0:
            return
        _aw, _ah, _half, R, _cx, _cy, lat0, lon0 = self._view_metrics()
        R = max(R, 1.0)
        # Pixel delta → angular delta on the orthographic plane.
        dλ = -dx / R  # radians
        dφ = dy / R
        lat1 = _clamp(lat0 + math.degrees(dφ), -85.0, 85.0)
        cos_lat = max(0.12, math.cos(math.radians(0.5 * (lat0 + lat1))))
        lon1 = lon0 + math.degrees(dλ / cos_lat)
        while lon1 > 180.0:
            lon1 -= 360.0
        while lon1 < -180.0:
            lon1 += 360.0
        wx, wy = lonlat_to_world(lat1, lon1)
        # Direct 1:1 camera update — land rebuilds sync on the next draw with
        # this exact pose so cities never slide off continents.
        self._cam_x = self._target_x = wx
        self._cam_y = self._target_y = wy
        self._base_key = None
        self._kick_anim()
        self._area.queue_draw()

    def _half_diag(self, aw: float, ah: float) -> float:
        """Half the viewport diagonal — limb distance that fills all four corners."""
        return max(40.0, 0.5 * math.hypot(aw, ah))

    def _earth_radius(self, aw: float, ah: float) -> float:
        """Orthographic earth radius so scale=1 fills the full rectangle corners."""
        return self._half_diag(aw, ah) * max(self._cam_scale, 0.2)

    def _view_metrics(
        self,
    ) -> tuple[float, float, float, float, float, float, float, float]:
        """Return (aw, ah, half_diag, R, cx, cy, lat0, lon0)."""
        alloc = self._area.get_allocation()
        aw = max(1.0, float(alloc.width))
        ah = max(1.0, float(alloc.height))
        half = self._half_diag(aw, ah)
        cx, cy = aw / 2.0, ah / 2.0
        R = self._earth_radius(aw, ah)
        lat0, lon0 = world_to_lonlat(self._cam_x, self._cam_y)
        return aw, ah, half, R, cx, cy, lat0, lon0

    def _project(
        self,
        lat: float,
        lon: float,
        *,
        R: float,
        cx: float,
        cy: float,
        lat0: float,
        lon0: float,
        limb_eps: float = 0.02,
    ) -> tuple[float, float] | None:
        φ0 = math.radians(lat0)
        return _project_fast(
            lat,
            lon,
            R=R,
            cx=cx,
            cy=cy,
            sin_φ0=math.sin(φ0),
            cos_φ0=math.cos(φ0),
            λ0=math.radians(lon0),
            limb_eps=limb_eps,
        )

    def _unproject(self, sx: float, sy: float) -> tuple[float, float] | None:
        """Inverse orthographic: screen → (lat, lon), or None if off the front."""
        _aw, _ah, _half, R, cx, cy, lat0, lon0 = self._view_metrics()
        x = (sx - cx) / R
        y = -(sy - cy) / R
        ρ = math.hypot(x, y)
        # Front hemisphere only (orthographic domain).
        if ρ > 1.0:
            return None
        if ρ < 1e-9:
            return lat0, lon0
        c = math.asin(_clamp(ρ, 0.0, 1.0))
        sin_c = math.sin(c)
        cos_c = math.cos(c)
        φ0 = math.radians(lat0)
        λ0 = math.radians(lon0)
        lat = math.degrees(
            math.asin(
                _clamp(
                    cos_c * math.sin(φ0) + (y * sin_c * math.cos(φ0)) / ρ,
                    -1.0,
                    1.0,
                )
            )
        )
        lon = math.degrees(
            λ0
            + math.atan2(
                x * sin_c,
                ρ * math.cos(φ0) * cos_c - y * math.sin(φ0) * sin_c,
            )
        )
        while lon > 180.0:
            lon -= 360.0
        while lon < -180.0:
            lon += 360.0
        return lat, lon

    def _nearest_city_screen(
        self, sx: float, sy: float, *, max_dist: float = 16.0
    ) -> RelayCity | None:
        _aw, _ah, _half, R, cx, cy, lat0, lon0 = self._view_metrics()
        φ0 = math.radians(lat0)
        sin_φ0, cos_φ0 = math.sin(φ0), math.cos(φ0)
        λ0 = math.radians(lon0)
        # Unproject pointer → only test nearby lon/lat grid cells
        under = self._unproject(sx, sy)
        candidates: list[RelayCity]
        if under is not None and self._cities_grid:
            ulat, ulon = under
            gx, gy = int(ulon // 10), int(ulat // 10)
            candidates = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    candidates.extend(self._cities_grid.get((gx + dx, gy + dy), ()))
            if not candidates:
                candidates = self._cities
        else:
            candidates = self._cities
        best: RelayCity | None = None
        best_d = max_dist
        for c in candidates:
            pt = _project_fast(
                c.latitude,
                c.longitude,
                R=R,
                cx=cx,
                cy=cy,
                sin_φ0=sin_φ0,
                cos_φ0=cos_φ0,
                λ0=λ0,
            )
            if pt is None:
                continue
            # Must be inside the square widget.
            if pt[0] < -8 or pt[0] > _aw + 8 or pt[1] < -8 or pt[1] > _ah + 8:
                continue
            d = math.hypot(pt[0] - sx, pt[1] - sy)
            if d < best_d:
                best_d = d
                best = c
        return best

    def _on_scroll(self, _controller, _dx: float, dy: float) -> bool:
        """Smooth zoom: update targets only; tick eases cam (incl. re-center).

        dy < 0 → zoom in; dy > 0 → zoom out (not past auto-frame floor).
        Continuous trackpad deltas and wheel steps both animate smoothly.
        Free-nav (any country): no forced re-center; pointer-anchored zoom only.
        """
        if dy == 0:
            return False

        free = self._is_free_nav()
        _aw, _ah, _half, R, cx, cy, lat0, lon0 = self._view_metrics()
        px, py = self._pointer_x, self._pointer_y
        if px <= 0 and py <= 0:
            px, py = cx, cy

        under = self._unproject(px, py)
        if under is not None:
            under_lat, under_lon = under
        else:
            under_lat, under_lon = lat0, lon0

        # Exponential zoom from target so stacked events stay smooth.
        # Mouse wheel often sends |dy|≈1; trackpads send fractions.
        zoom_factor = math.exp(-0.14 * dy)
        # Clamp single-event jump so a big fling doesn't overshoot too hard.
        zoom_factor = max(0.82, min(1.22, zoom_factor))

        # Free-nav floor is full earth; region mode floors at auto-frame.
        floor = 1.0 if free else max(0.5, self._base_scale)

        new_scale = self._target_scale * zoom_factor
        new_scale = max(floor, min(new_scale, self._max_user_scale))
        zooming_out = dy > 0

        # Region mode: at floor, animate re-center to region frame.
        if not free and zooming_out and new_scale <= floor + 1e-4:
            self._target_scale = floor
            self._target_x = self._base_x
            self._target_y = self._base_y
            return True

        if abs(new_scale - self._target_scale) < 1e-5:
            # Free-nav at floor: keep current center (no re-center yank).
            return True

        self._target_scale = new_scale
        ux, uy = lonlat_to_world(under_lat, under_lon)

        if free:
            # Free-nav: zoom toward cursor only; never pull to world center.
            self._target_x = self._target_x + (ux - self._target_x) * 0.28
            self._target_y = self._target_y + (uy - self._target_y) * 0.28
        elif zooming_out:
            # Region mode: pull target center back toward the auto-frame.
            span = max(self._max_user_scale - floor, 1e-3)
            t = 1.0 - (new_scale - floor) / span
            t = max(0.0, min(1.0, t))
            t = t * t
            pull = 0.28 + 0.72 * t
            self._target_x = self._target_x + (ux - self._target_x) * 0.08
            self._target_y = self._target_y + (uy - self._target_y) * 0.08
            self._target_x = self._target_x + (self._base_x - self._target_x) * pull
            self._target_y = self._target_y + (self._base_y - self._target_y) * pull
            if new_scale <= floor + 1e-3:
                self._target_x = self._base_x
                self._target_y = self._base_y
        else:
            # Region zoom-in: ease target toward the point under the cursor.
            self._target_x = self._target_x + (ux - self._target_x) * 0.28
            self._target_y = self._target_y + (uy - self._target_y) * 0.28

        # Frame-clock eases cam → target every frame.
        self._kick_anim()
        return True

    def _on_drag_begin(self, _g, _x: float, _y: float) -> None:
        if not self._is_free_nav() or self._busy:
            self._dragging = False
            return
        self._dragging = True
        self._drag_moved = False
        self._drag_prev_ox = 0.0
        self._drag_prev_oy = 0.0
        # Cancel any in-flight full bake — interactive path owns the surface.
        self._bake_gen += 1
        self._bake_inflight = False
        self._base_key = None
        self._kick_anim()
        try:
            self._area.set_cursor_from_name("grabbing")
        except Exception:
            pass

    def _on_drag_update(self, _g, offset_x: float, offset_y: float) -> None:
        if not self._dragging or not self._is_free_nav():
            return
        dx = float(offset_x) - self._drag_prev_ox
        dy = float(offset_y) - self._drag_prev_oy
        self._drag_prev_ox = float(offset_x)
        self._drag_prev_oy = float(offset_y)
        if abs(offset_x) + abs(offset_y) > 4.0:
            self._drag_moved = True
        self._pan_by_pixels(dx, dy)

    def _on_drag_end(self, _g, _offset_x: float, _offset_y: float) -> None:
        self._dragging = False
        # Drop interactive key so the settled full bake (flags/lakes/borders)
        # can replace it at this exact camera pose.
        self._invalidate_base()
        self._kick_anim()
        self._area.queue_draw()
        try:
            self._area.set_cursor_from_name("pointer")
        except Exception:
            pass

    def _on_motion(self, _c, x: float, y: float) -> None:
        self._pointer_x = float(x)
        self._pointer_y = float(y)
        if self._dragging:
            return
        now = GLib.get_monotonic_time() / 1_000_000.0
        if (now - self._last_hover_t) < _HOVER_MIN_DT:
            return
        self._last_hover_t = now
        hit = self._nearest_city_screen(x, y)
        if hit is not self._hover:
            self._hover = hit
            self._update_caption()
            # Cities-only redraw; base layer stays cached.
            self._ensure_anim_loop()
            self._area.queue_draw()

    def _on_leave(self, *_a) -> None:
        if self._hover is not None:
            self._hover = None
            self._update_caption()
            self._ensure_anim_loop()
            self._area.queue_draw()

    def _on_press(self, _g, _n: int, x: float, y: float) -> None:
        self._press_xy = (float(x), float(y))
        self._drag_moved = False

    def _on_release(self, _g, _n: int, x: float, y: float) -> None:
        # City select only if this wasn't a pan drag.
        if self._drag_moved:
            self._press_xy = None
            return
        self._select_city_at(float(x), float(y))
        self._press_xy = None

    def _select_city_at(self, x: float, y: float) -> None:
        if self._busy:
            return
        if not cli_path():
            if self._on_toast:
                self._on_toast("Mullvad CLI not installed")
            return
        city = self._nearest_city_screen(x, y, max_dist=20.0)
        if city is None:
            return
        # Fly immediately; spinner only if CLI is slow.
        self.set_active(city.country_code, city.city_code)
        self.set_loading(
            True,
            f"Selecting · {city.city_name}, {city.country_name}…",
            delay_ms=280,
        )

        def worker() -> None:
            # Keep tunnel up if already connected — only update constraints.
            ok, msg = set_location(
                city.country_code,
                city.city_code,
                None,
                disconnect_if_connected=False,
            )

            def done() -> bool:
                self.set_loading(False)
                if ok:
                    if self._on_location:
                        self._on_location(
                            city.country_code, city.city_code, city.city_name
                        )
                    if self._on_toast:
                        short = (
                            f"Selected {city.city_name}"
                            if "tunnel stays" in (msg or "").lower()
                            or "constraint" in (msg or "").lower()
                            else f"Selected {city.city_name} · press Connect"
                        )
                        self._on_toast(short)
                else:
                    if self._on_toast:
                        self._on_toast(msg or "Mullvad location failed")
                return False

            GLib.idle_add(done)

        threading.Thread(target=worker, name="mullvad-map-set", daemon=True).start()

    def _on_tick(self, _widget, frame_clock) -> bool:
        # Skip work when not on screen.
        if not self._area.get_mapped():
            return GLib.SOURCE_CONTINUE

        t = frame_clock.get_frame_time() / 1_000_000.0
        if not self._last_t:
            self._last_t = t
        dt = max(0.0, min(0.05, t - self._last_t))
        self._last_t = t

        # City fade-in
        if self._city_fade < 0.999 and self._city_fade_t0 > 0:
            elapsed = t - self._city_fade_t0
            # Smoothstep
            u = max(0.0, min(1.0, elapsed / _CITY_FADE_SEC))
            self._city_fade = u * u * (3.0 - 2.0 * u)
            self._area.queue_draw()

        moving = self._ease_camera(dt)
        if moving or self._dragging:
            # One redraw per tick while camera/gesture is live (land + cities
            # share this frame's camera in _draw).
            self._area.queue_draw()
            return GLib.SOURCE_CONTINUE

        # Settled: demote to idle pulse timer (or stop) — drop vsync load
        if not self._needs_frame_clock():
            self._tick_id = 0
            self._ensure_anim_loop(force_tick=False)
            if self._needs_idle_pulse():
                self._area.queue_draw()
            return GLib.SOURCE_REMOVE

        needs_pulse = self._needs_idle_pulse()
        if needs_pulse and (t - self._last_pulse_t) >= _PULSE_INTERVAL:
            self._last_pulse_t = t
            self._area.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _cache_key(
        self, aw: float, ah: float, half: float, lat0: float, lon0: float, scale: float
    ) -> tuple:
        return (
            int(aw),
            int(ah),
            round(half, 1),
            round(lat0 / _CACHE_XY_Q) * _CACHE_XY_Q,
            round(lon0 / _CACHE_XY_Q) * _CACHE_XY_Q,
            round(scale / _CACHE_SCALE_Q) * _CACHE_SCALE_Q,
        )

    def _ensure_bg_layer(
        self, aw: int, ah: int, half: float, cx: float, cy: float
    ):
        """Ocean + lighting only — depends on viewport size, never on camera.

        Separating this from land stops highlight/depth gradients from
        re-rasterizing every pan frame (the “blink” while dragging).
        """
        if cairo is None:
            return None
        key = (int(aw), int(ah), round(half, 1))
        if self._bg_surf is not None and self._bg_key == key:
            return self._bg_surf
        w, h = max(1, int(aw)), max(1, int(ah))
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)
        g_ocean = cairo.RadialGradient(
            cx - half * 0.18, cy - half * 0.22, 0, cx, cy, half
        )
        g_ocean.add_color_stop_rgba(0.0, 0.09, 0.14, 0.20, 1.0)
        g_ocean.add_color_stop_rgba(0.55, 0.06, 0.09, 0.13, 1.0)
        g_ocean.add_color_stop_rgba(1.0, 0.04, 0.06, 0.09, 1.0)
        cr.set_source(g_ocean)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        g_depth = cairo.RadialGradient(cx, cy, half * 0.2, cx, cy, half)
        g_depth.add_color_stop_rgba(0.0, 0.0, 0.0, 0.0, 0.0)
        g_depth.add_color_stop_rgba(0.7, 0.0, 0.02, 0.04, 0.10)
        g_depth.add_color_stop_rgba(1.0, 0.0, 0.01, 0.03, 0.28)
        cr.set_source(g_depth)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        hx = cx - half * 0.25
        hy = cy - half * 0.30
        g_hi = cairo.RadialGradient(hx, hy, 0, hx, hy, half * 0.85)
        g_hi.add_color_stop_rgba(0.0, 0.75, 0.88, 1.0, 0.09)
        g_hi.add_color_stop_rgba(0.45, 0.55, 0.72, 0.90, 0.03)
        g_hi.add_color_stop_rgba(1.0, 0.4, 0.5, 0.7, 0.0)
        cr.set_source(g_hi)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        surf.flush()
        self._bg_surf = surf
        self._bg_key = key
        return surf

    def _ensure_base_layer(
        self,
        aw: int,
        ah: int,
        half: float,
        R: float,
        cx: float,
        cy: float,
        lat0: float,
        lon0: float,
    ):
        """Build or reuse land ImageSurface for the current camera.

        Stability contract: land and city markers always share one camera pose
        on every drawn frame. During drag/fly that means a **sync** interactive
        land paint (~10ms) at the live camera — never a stale async bake under
        live cities (that was the “nodes lag / unstable” feel).

        When the camera settles, an off-thread full bake upgrades to multi-ring
        flags, lakes, and borders without changing the shared pose.
        """
        if cairo is None:
            return None
        moving = self._dragging or not self._camera_settled()
        if not self._land_ll and not self._land_drag_ll:
            return self._base_surf

        now = GLib.get_monotonic_time() / 1_000_000.0

        # ── Interactive (drag / fly): live camera, solid land, no desync ──
        if moving:
            # Exact pose key (small quantize only for float noise)
            key = (
                int(aw),
                int(ah),
                round(half, 1),
                round(lat0, 3),
                round(lon0, 3),
                round(self._cam_scale, 4),
                "live",
            )
            if self._base_surf is not None and self._base_key == key:
                return self._base_surf

            # Never return a stale surface for a new pose — that desyncs cities.
            # Interactive paint is ~10–15ms; rebuild every distinct camera key.
            land = self._land_drag_ll or self._land_ll
            # Reduced res then upscale — land (incl. limb continents) + fast flags
            scale_f = 0.65 if aw * ah > 280_000 else 0.78
            paw, pah = max(1, int(aw * scale_f)), max(1, int(ah * scale_f))
            surf = self._paint_interactive_base(
                paw,
                pah,
                half * scale_f,
                R * scale_f,
                cx * scale_f,
                cy * scale_f,
                lat0,
                lon0,
                land=land,
                server_codes=set(self._server_country_codes),
                country_rings=self._country_rings,
                country_meta=self._country_meta,
                flag_opacity=float(self._flag_opacity),
            )
            if surf is not None and scale_f < 0.999 and cairo is not None:
                full = cairo.ImageSurface(
                    cairo.FORMAT_ARGB32, max(1, int(aw)), max(1, int(ah))
                )
                fcr = cairo.Context(full)
                fcr.scale(aw / float(paw), ah / float(pah))
                fcr.set_source_surface(surf, 0, 0)
                fcr.paint()
                surf = full
            if surf is not None:
                self._base_surf = surf
                self._base_key = key
                self._last_base_build_t = now
                self._base_pose = (lat0, lon0, self._cam_scale, R)
            return self._base_surf

        # ── Settled: full-quality async (flags, lakes, borders) ──
        key = self._cache_key(aw, ah, half, lat0, lon0, self._cam_scale) + (
            "full",
        )
        if self._base_surf is not None and self._base_key == key:
            return self._base_surf

        self._schedule_base_bake(
            key,
            aw,
            ah,
            half,
            R,
            cx,
            cy,
            lat0,
            lon0,
            moving=False,
        )
        # Keep showing last interactive/full frame at this pose until bake ready
        return self._base_surf

    def _paint_interactive_base(
        self,
        aw: int,
        ah: int,
        half: float,
        R: float,
        cx: float,
        cy: float,
        lat0: float,
        lon0: float,
        *,
        land: list,
        server_codes: set[str] | None = None,
        country_rings: dict | None = None,
        country_meta: dict | None = None,
        flag_opacity: float = 0.30,
    ):
        """Sync land + flags for the live camera (drag/fly). No async lag.

        Flags use the fast path (~3–5ms) so the whole layer stays locked to the
        same pose as city markers.
        """
        if cairo is None or not land:
            return None
        w, h = max(1, int(aw)), max(1, int(ah))
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)
        try:
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()
            cr.set_operator(cairo.OPERATOR_OVER)
        except Exception:
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()

        φ0 = math.radians(lat0)
        sin_φ0, cos_φ0 = math.sin(φ0), math.cos(φ0)
        λ0 = math.radians(lon0)

        def proj(lat: float, lon: float, limb: float = 0.02):
            return _project_fast(
                lat,
                lon,
                R=R,
                cx=cx,
                cy=cy,
                sin_φ0=sin_φ0,
                cos_φ0=cos_φ0,
                λ0=λ0,
                limb_eps=limb,
            )

        cr.save()
        cr.arc(cx, cy, max(1.0, R * 0.998), 0, 2.0 * math.pi)
        cr.clip()
        _paint_interactive_land(cr, land, proj, cx=cx, cy=cy, R=R)
        if server_codes and country_rings:
            _paint_country_flags_fast(
                cr,
                country_rings=country_rings,
                country_meta=country_meta,
                server_codes=server_codes,
                proj=proj,
                opacity=flag_opacity,
                stride=2,
            )
        cr.restore()
        surf.flush()
        return surf

    def _schedule_base_bake(
        self,
        key: tuple,
        aw: int,
        ah: int,
        half: float,
        R: float,
        cx: float,
        cy: float,
        lat0: float,
        lon0: float,
        *,
        moving: bool = False,
        lod: bool = False,  # kept for callers; ignored (use moving)
    ) -> None:
        if self._bake_inflight and self._bake_pending_key == key:
            return
        # Coalesce: always remember the latest camera pose. Do not cancel the
        # in-flight bake — applying it keeps land filled while pan continues;
        # the worker then chains to the latest pending args.
        self._bake_pending_key = key
        self._bake_pending_args = (
            key,
            int(aw),
            int(ah),
            float(half),
            float(R),
            float(cx),
            float(cy),
            float(lat0),
            float(lon0),
            bool(moving),
        )
        if self._bake_inflight:
            return
        self._start_base_bake_worker()

    def _start_base_bake_worker(self) -> None:
        args = getattr(self, "_bake_pending_args", None)
        if not args or not self._land_ll:
            self._bake_inflight = False
            return
        (
            key,
            aw,
            ah,
            half,
            R,
            cx,
            cy,
            lat0,
            lon0,
            moving,
        ) = args
        self._bake_gen += 1
        gen = self._bake_gen
        self._bake_inflight = True
        self._bake_pending_key = key
        land = list(self._land_ll)
        lakes = list(self._lakes_ll)
        borders = list(self._borders_ll)
        servers = set(self._server_country_codes)
        country_rings = self._country_rings
        flag_opacity = float(self._flag_opacity)
        paw, pah = max(1, int(aw)), max(1, int(ah))

        def worker() -> None:
            try:
                surf = MullvadMap._paint_base_static(
                    paw,
                    pah,
                    half,
                    R,
                    cx,
                    cy,
                    lat0,
                    lon0,
                    land=land,
                    lakes=lakes,
                    borders=borders,
                    moving=False,
                    server_codes=servers,
                    country_rings=country_rings,
                    flag_opacity=flag_opacity,
                )
            except Exception:
                surf = None

            def apply() -> bool:
                # Drop only if this generation was cancelled (unrealize / hard reset)
                if gen != self._bake_gen:
                    self._bake_inflight = False
                    pending = getattr(self, "_bake_pending_args", None)
                    if pending:
                        self._start_base_bake_worker()
                    return False
                self._bake_inflight = False
                # Never paste a full bake over a live interactive drag/fly frame —
                # that reintroduces land/city desync mid-gesture.
                if self._dragging or not self._camera_settled():
                    pending = getattr(self, "_bake_pending_args", None)
                    if pending and pending[0] != key:
                        self._start_base_bake_worker()
                    return False
                if surf is not None:
                    self._base_surf = surf
                    self._base_key = key
                    self._last_base_build_t = (
                        GLib.get_monotonic_time() / 1_000_000.0
                    )
                    self._base_pose = (lat0, lon0, self._cam_scale, R)
                    self._area.queue_draw()
                pending = getattr(self, "_bake_pending_args", None)
                if pending and pending[0] != key:
                    self._start_base_bake_worker()
                else:
                    self._bake_pending_key = key if surf is not None else None
                return False

            GLib.idle_add(apply)

        threading.Thread(target=worker, name="map-base-bake", daemon=True).start()

    def _paint_base_surface(
        self,
        aw: int,
        ah: int,
        half: float,
        R: float,
        cx: float,
        cy: float,
        lat0: float,
        lon0: float,
        *,
        lod: bool,
        drag: bool = False,
        reuse=None,
    ):
        # Landmass + lakes + borders; flags only when settled.
        return self._paint_base_static(
            aw,
            ah,
            half,
            R,
            cx,
            cy,
            lat0,
            lon0,
            land=self._land_ll,
            lakes=self._lakes_ll,
            borders=self._borders_ll,
            moving=bool(lod or drag),
            reuse=reuse,
            server_codes=set(self._server_country_codes),
            country_rings=self._country_rings,
            flag_opacity=float(self._flag_opacity),
        )

    @staticmethod
    def _paint_base_static(
        aw: int,
        ah: int,
        half: float,
        R: float,
        cx: float,
        cy: float,
        lat0: float,
        lon0: float,
        *,
        land: list,
        moving: bool,
        reuse=None,
        lakes: list | None = None,
        borders: list | None = None,
        server_codes: set[str] | None = None,
        country_rings: dict | None = None,
        flag_opacity: float = 0.30,
    ):
        """Cairo paint used on UI or worker thread (no GTK calls)."""
        if cairo is None:
            return None
        w, h = max(1, int(aw)), max(1, int(ah))
        # Reuse buffer only when size matches (UI drag path); workers pass reuse=None
        if (
            reuse is not None
            and reuse.get_width() == w
            and reuse.get_height() == h
        ):
            surf = reuse
        else:
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)

        cr = cairo.Context(surf)
        # Transparent clear — ocean/lighting painted separately so it never blinks
        try:
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()
            cr.set_operator(cairo.OPERATOR_OVER)
        except Exception:
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()

        φ0 = math.radians(lat0)
        sin_φ0, cos_φ0 = math.sin(φ0), math.cos(φ0)
        λ0 = math.radians(lon0)

        def proj(lat: float, lon: float, limb: float = 0.02):
            return _project_fast(
                lat,
                lon,
                R=R,
                cx=cx,
                cy=cy,
                sin_φ0=sin_φ0,
                cos_φ0=cos_φ0,
                λ0=λ0,
                limb_eps=limb,
            )

        # Soft graticule under land — skip while moving (settled bake has it).
        # Never connect across limb gaps (that drew fake “longitude wedges”).
        if not moving:
            lon_step = 30
            lat_step = 6
            cr.set_line_width(0.65)
            cr.set_source_rgba(0.28, 0.36, 0.42, 0.22)
            for lon in range(-180, 180, lon_step):
                first = True
                prev = None
                for lat in range(-90, 91, lat_step):
                    pt = proj(float(lat), float(lon))
                    if pt is None:
                        first = True
                        prev = None
                        continue
                    if first:
                        cr.move_to(pt[0], pt[1])
                        first = False
                    else:
                        # Break if projection jumped (near-limb numeric glitch)
                        if prev is not None and math.hypot(
                            pt[0] - prev[0], pt[1] - prev[1]
                        ) > 0.55 * R:
                            cr.stroke()
                            cr.move_to(pt[0], pt[1])
                        else:
                            cr.line_to(pt[0], pt[1])
                    prev = pt
                cr.stroke()
            for lat in range(-60, 61, 30):
                first = True
                prev = None
                for lon in range(-180, 181, lat_step):
                    pt = proj(float(lat), float(lon))
                    if pt is None:
                        first = True
                        prev = None
                        continue
                    if first:
                        cr.move_to(pt[0], pt[1])
                        first = False
                    else:
                        if prev is not None and math.hypot(
                            pt[0] - prev[0], pt[1] - prev[1]
                        ) > 0.55 * R:
                            cr.stroke()
                            cr.move_to(pt[0], pt[1])
                        else:
                            cr.line_to(pt[0], pt[1])
                    prev = pt
                cr.stroke()

        # Clip land/flags/borders to the earth disc so corners stay clean ocean
        cr.save()
        cr.arc(cx, cy, max(1.0, R * 0.998), 0, 2.0 * math.pi)
        cr.clip()

        # 1) Continuous landmasses  2) flag wash  3) lakes punch through
        # 4) political borders on top. Lakes after flags so inland water
        # stays water (Great Lakes, Baikal, Victoria, …).
        # Flags also while moving so pan doesn't strip the map to outlines.
        _fill_landmass_only(
            cr, land, proj, cx=cx, cy=cy, R=R, light=bool(moving)
        )

        if server_codes and country_rings:
            _paint_country_flags(
                cr,
                country_rings=country_rings,
                server_codes=server_codes,
                proj=proj,
                opacity=flag_opacity,
            )

        if lakes:
            _cut_lakes(cr, lakes, proj, cx=cx, cy=cy, R=R, light=bool(moving))
        if borders:
            _stroke_borders(cr, borders, proj, light=bool(moving))

        cr.restore()

        surf.flush()
        return surf

    def _draw_base_fallback(
        self,
        cr,
        aw: float,
        ah: float,
        half: float,
        R: float,
        cx: float,
        cy: float,
        lat0: float,
        lon0: float,
    ) -> None:
        """Direct land draw when the land ImageSurface cache is unavailable."""
        φ0 = math.radians(lat0)
        sin_φ0, cos_φ0 = math.sin(φ0), math.cos(φ0)
        λ0 = math.radians(lon0)

        def proj(lat: float, lon: float, limb: float = 0.02):
            return _project_fast(
                lat,
                lon,
                R=R,
                cx=cx,
                cy=cy,
                sin_φ0=sin_φ0,
                cos_φ0=cos_φ0,
                λ0=λ0,
                limb_eps=limb,
            )

        _fill_projected_land(
            cr,
            self._land_ll,
            proj,
            cx=cx,
            cy=cy,
            R=R,
            lakes=self._lakes_ll,
            borders=self._borders_ll,
            light=False,
        )

    def _draw(self, _area, cr, width: int, height: int, *_ud) -> None:
        aw, ah = float(width), float(height)
        if aw < 2 or ah < 2:
            return
        t = (GLib.get_monotonic_time() - self._t0) / 1_000_000.0

        half = self._half_diag(aw, ah)
        cx, cy = aw / 2.0, ah / 2.0
        R = self._earth_radius(aw, ah)
        lat0, lon0 = world_to_lonlat(self._cam_x, self._cam_y)

        # 1) Static ocean + lighting (never rebuilds while dragging)
        bg = self._ensure_bg_layer(int(aw), int(ah), half, cx, cy)
        if bg is not None:
            cr.set_source_surface(bg, 0, 0)
            cr.paint()
        else:
            cr.set_source_rgb(0.06, 0.09, 0.13)
            cr.rectangle(0, 0, aw, ah)
            cr.fill()

        # 2) Land + graticule + borders (camera-dependent, no lighting baked in)
        base = self._ensure_base_layer(
            int(aw), int(ah), half, R, cx, cy, lat0, lon0
        )
        if base is not None:
            cr.set_source_surface(base, 0, 0)
            cr.paint()
        else:
            self._draw_base_fallback(cr, aw, ah, half, R, cx, cy, lat0, lon0)

        # Cities only (cheap) — full square; alpha for progressive fade-in
        φ0 = math.radians(lat0)
        sin_φ0, cos_φ0 = math.sin(φ0), math.cos(φ0)
        λ0 = math.radians(lon0)
        fade = max(0.0, min(1.0, float(self._city_fade)))
        if fade < 0.02:
            return

        for city in self._cities:
            pt = _project_fast(
                city.latitude,
                city.longitude,
                R=R,
                cx=cx,
                cy=cy,
                sin_φ0=sin_φ0,
                cos_φ0=cos_φ0,
                λ0=λ0,
                limb_eps=0.05,
            )
            if pt is None:
                continue
            vx, vy = pt
            if vx < -8 or vx > aw + 8 or vy < -8 or vy > ah + 8:
                continue

            is_connected = (
                bool(self._connected_country)
                and bool(self._connected_city)
                and city.country_code == self._connected_country
                and city.city_code == self._connected_city
            )
            # Selection pulse uses _sel_*, not camera frame (_active_*)
            is_active = (
                not is_connected
                and bool(self._sel_country)
                and city.country_code == self._sel_country
                and (not self._sel_city or city.city_code == self._sel_city)
            )
            is_hover = self._hover is city

            if is_connected:
                # Live tunnel endpoint — green pulse
                phase = math.sin(t * 3.2) * 0.5 + 0.5
                r = 4.6 + phase * 2.6
                cr.set_source_rgba(0.22, 0.72, 0.38, (0.18 + phase * 0.22) * fade)
                cr.arc(vx, vy, r * 2.5, 0, 2 * math.pi)
                cr.fill()
                cr.set_source_rgba(0.18, 0.78, 0.40, (0.92 + phase * 0.08) * fade)
            elif is_active:
                # Selection only (not connected) — cool cyan pulse
                phase = math.sin(t * 3.2) * 0.5 + 0.5
                r = 4.2 + phase * 2.4
                cr.set_source_rgba(0.55, 0.85, 1.0, (0.15 + phase * 0.2) * fade)
                cr.arc(vx, vy, r * 2.4, 0, 2 * math.pi)
                cr.fill()
                cr.set_source_rgba(0.95, 0.97, 1.0, (0.85 + phase * 0.15) * fade)
            elif is_hover:
                r = 3.8
                cr.set_source_rgba(0.85, 0.92, 1.0, 0.95 * fade)
            else:
                r = 2.25
                cr.set_source_rgba(0.45, 0.72, 0.82, 0.72 * fade)

            cr.arc(vx, vy, r, 0, 2 * math.pi)
            cr.fill()
