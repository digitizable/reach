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
from core.mullvad import RelayCity, cli_path, load_catalog, set_location

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
# Cap land-layer rebuild rate during fly/zoom (~10 fps); cities still track live.
_BASE_REBUILD_MIN_DT = 0.10


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
    """Load simplified land outlines (equirectangular 720×360)."""
    path = project_root() / "data" / "assets" / "world-land.json"
    if not path.is_file():
        return _WORLD_W, _WORLD_H, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _WORLD_W, _WORLD_H, []
    w = float(data.get("w") or _WORLD_W)
    h = float(data.get("h") or _WORLD_H)
    polys: list[list[tuple[float, float]]] = []
    for ring in data.get("polys") or []:
        pts = [(float(p[0]), float(p[1])) for p in ring if len(p) >= 2]
        if len(pts) >= 3:
            polys.append(pts)
    return w, h, polys


@lru_cache(maxsize=1)
def land_latlon() -> list[list[tuple[float, float]]]:
    """Land rings pre-converted to (lat, lon) — avoids per-frame world→geo."""
    _w, _h, polys = load_land_polygons()
    out: list[list[tuple[float, float]]] = []
    for ring in polys:
        out.append([world_to_lonlat(wx, wy) for wx, wy in ring])
    return out


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
        self._active_country = ""
        self._active_city = ""
        self._hover: RelayCity | None = None
        self._t0 = GLib.get_monotonic_time()
        self._busy = False
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

        # Cached static globe layer (land + graticule + shading). Cities drawn live.
        self._base_surf = None  # cairo.ImageSurface | None
        self._base_key: tuple | None = None
        self._last_base_build_t = 0.0
        self._land_ll = land_latlon()

        self._area = Gtk.DrawingArea()
        self._area.add_css_class("mullvad-map-viewport")
        self._area.set_hexpand(True)
        self._area.set_halign(Gtk.Align.FILL)
        self._area.set_draw_func(self._draw)
        if interactive:
            self._area.set_cursor_from_name("pointer")

        # Full-bleed rectangle: stretch to the pane’s left/right/top — no
        # AspectFrame square letterbox that insets the map from the edges.
        if fill:
            self._area.set_vexpand(True)
            self._area.set_valign(Gtk.Align.FILL)
            self.set_vexpand(True)
            self._area.set_content_height(max(140, self._height))
        else:
            self._area.set_vexpand(False)
            self._area.set_content_height(self._height)
        self.append(self._area)

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

        self._tick_id = self._area.add_tick_callback(self._on_tick)
        GLib.idle_add(self._load_cities)

    def do_unrealize(self) -> None:
        if getattr(self, "_tick_id", 0):
            self._area.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        self._base_surf = None
        self._base_key = None
        Gtk.Box.do_unrealize(self)

    def _invalidate_base(self) -> None:
        self._base_surf = None
        self._base_key = None

    def _is_free_nav(self) -> bool:
        """Any-country mode: pan/zoom freely with no region re-center lock."""
        return not self._active_country or self._active_country == "any"

    def set_active(self, country: str = "", city: str = "") -> None:
        prev = self._active_country
        self._active_country = (country or "").lower()
        self._active_city = (city or "").lower()
        if self._is_free_nav():
            # Entering free-nav from a country → fly home once.
            # Already free → keep the user’s current view.
            leaving_region = bool(prev) and prev not in ("", "any")
            self._enter_free_nav(reset_view=leaving_region)
        else:
            self._fly_to_active()
        self._update_caption()
        self._area.queue_draw()

    def refresh(self) -> None:
        self._load_cities()

    def _load_cities(self) -> bool:
        try:
            self._cities = list(load_catalog().map_cities)
        except Exception:
            self._cities = []
        self._update_caption()
        if self._is_free_nav():
            # Don't yank the camera on catalog refresh.
            self._base_scale = 1.0
            self._base_x = _WORLD_W / 2
            self._base_y = _WORLD_H / 2
        else:
            self._fly_to_active()
        self._area.queue_draw()
        return False

    def _update_caption(self) -> None:
        n = len(self._cities)
        if self._hover is not None:
            h = self._hover
            self._caption.set_text(
                f"{h.city_name}, {h.country_name} · click to select"
            )
            return
        if self._active_country and self._active_country != "any":
            bits = [self._active_country.upper()]
            if self._active_city:
                name = self._active_city.upper()
                for c in self._cities:
                    if (
                        c.country_code == self._active_country
                        and c.city_code == self._active_city
                    ):
                        name = c.city_name
                        break
                bits.insert(0, name)
            self._caption.set_text(
                f"Mullvad · {', '.join(bits)} · {n} cities · click to set relay"
            )
        else:
            self._caption.set_text(
                f"Mullvad · {n} cities · drag to pan · scroll to zoom · click a city"
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
        """Zoom to the selected country (full span) or city (tight)."""
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

        if city_mode or len(matches) == 1:
            c_max = math.radians(7.0)
            fill = 0.72
            min_scale, max_scale = 6.0, 28.0
        else:
            c_max = 0.0
            for c in matches:
                c_max = max(
                    c_max,
                    angular_distance_rad(lat0, lon0, c.latitude, c.longitude),
                )
            c_max = max(c_max + math.radians(8.0), math.radians(12.0))
            fill = 0.90
            min_scale, max_scale = 1.15, 14.0

        # Fit angular span inside the short side of the viewport.
        # (Half-diagonal cover is applied in _earth_radius; scale is relative.)
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
            self._cam_x = self._target_x
            self._cam_y = self._target_y
            self._cam_scale = self._target_scale
            return False
        # Critically damped-ish exponential ease — snappy but smooth.
        dist = math.hypot(
            self._target_x - self._cam_x, self._target_y - self._cam_y
        ) + abs(self._target_scale - self._cam_scale) * 24
        # Faster when far (fly-to / big zoom), still smooth near settle.
        rate = 7.5 if dist > 40 else 9.0 if dist > 8 else 11.0
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
        # Direct update for responsive dragging.
        self._cam_x = self._target_x = wx
        self._cam_y = self._target_y = wy
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
        best: RelayCity | None = None
        best_d = max_dist
        for c in self._cities:
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

        # Tick callback eases cam → target every frame.
        return True

    def _on_drag_begin(self, _g, _x: float, _y: float) -> None:
        if not self._is_free_nav() or self._busy:
            self._dragging = False
            return
        self._dragging = True
        self._drag_moved = False
        self._drag_prev_ox = 0.0
        self._drag_prev_oy = 0.0
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
        try:
            self._area.set_cursor_from_name("pointer")
        except Exception:
            pass

    def _on_motion(self, _c, x: float, y: float) -> None:
        self._pointer_x = float(x)
        self._pointer_y = float(y)
        if self._dragging:
            return
        hit = self._nearest_city_screen(x, y)
        if hit is not self._hover:
            self._hover = hit
            self._update_caption()
            # Cities-only redraw; base layer stays cached.
            self._area.queue_draw()

    def _on_leave(self, *_a) -> None:
        if self._hover is not None:
            self._hover = None
            self._update_caption()
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
        self._busy = True
        self._caption.set_text(
            f"Selecting · {city.city_name}, {city.country_name}…"
        )

        def worker() -> None:
            ok, msg = set_location(
                city.country_code,
                city.city_code,
                None,
                disconnect_if_connected=True,
            )

            def done() -> bool:
                self._busy = False
                if ok:
                    self.set_active(city.country_code, city.city_code)
                    if self._on_location:
                        self._on_location(
                            city.country_code, city.city_code, city.city_name
                        )
                    if self._on_toast:
                        self._on_toast(
                            f"Selected {city.city_name} · press Connect"
                        )
                else:
                    self._update_caption()
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

        moving = self._ease_camera(dt)
        if moving:
            # Base layer rebuilds only when the quantized camera key changes.
            self._area.queue_draw()
            return GLib.SOURCE_CONTINUE

        # Idle: only pulse markers at a low rate (active city / hover).
        needs_pulse = bool(self._active_country) or self._hover is not None
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
        """Build or reuse ImageSurface: full-square ocean, land, graticule."""
        if cairo is None:
            return None
        key = self._cache_key(aw, ah, half, lat0, lon0, self._cam_scale)
        if self._base_surf is not None and self._base_key == key:
            return self._base_surf

        # While the camera is mid-flight, reuse a slightly stale base so we do
        # not reproject 5k land points every vsync. Final settle rebuilds exact.
        now = GLib.get_monotonic_time() / 1_000_000.0
        if (
            self._base_surf is not None
            and not self._camera_settled()
            and (now - self._last_base_build_t) < _BASE_REBUILD_MIN_DT
        ):
            return self._base_surf

        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, aw), max(1, ah))
        cr = cairo.Context(surf)

        # Full-rect ocean (edge-to-edge — no circular ball, no letterbox)
        g_ocean = cairo.RadialGradient(
            cx - half * 0.18, cy - half * 0.22, 0, cx, cy, half
        )
        g_ocean.add_color_stop_rgba(0.0, 0.09, 0.14, 0.20, 1.0)
        g_ocean.add_color_stop_rgba(0.55, 0.06, 0.09, 0.13, 1.0)
        g_ocean.add_color_stop_rgba(1.0, 0.04, 0.06, 0.09, 1.0)
        cr.set_source(g_ocean)
        cr.rectangle(0, 0, aw, ah)
        cr.fill()

        φ0 = math.radians(lat0)
        sin_φ0, cos_φ0 = math.sin(φ0), math.cos(φ0)
        λ0 = math.radians(lon0)

        def proj(lat: float, lon: float, limb: float = 0.0):
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

        # Coarser graticule (cheap, still readable)
        cr.set_line_width(0.65)
        cr.set_source_rgba(0.28, 0.36, 0.42, 0.26)
        for lon in range(-180, 180, 30):
            first = True
            for lat in range(-90, 91, 6):
                pt = proj(float(lat), float(lon))
                if pt is None:
                    first = True
                    continue
                if first:
                    cr.move_to(pt[0], pt[1])
                    first = False
                else:
                    cr.line_to(pt[0], pt[1])
            cr.stroke()
        for lat in range(-60, 61, 30):
            first = True
            for lon in range(-180, 181, 6):
                pt = proj(float(lat), float(lon))
                if pt is None:
                    first = True
                    continue
                if first:
                    cr.move_to(pt[0], pt[1])
                    first = False
                else:
                    cr.line_to(pt[0], pt[1])
            cr.stroke()

        # Land — single fill pass
        cr.set_source_rgb(0.16, 0.20, 0.24)
        for ring in self._land_ll:
            if len(ring) < 3:
                continue
            started = False
            for lat, lon in ring:
                pt = proj(lat, lon)
                if pt is None:
                    started = False
                    continue
                if not started:
                    cr.move_to(pt[0], pt[1])
                    started = True
                else:
                    cr.line_to(pt[0], pt[1])
            if started:
                cr.close_path()
        cr.fill()

        # Soft depth toward corners (full-bleed rect, not a circular rim)
        g_depth = cairo.RadialGradient(cx, cy, half * 0.2, cx, cy, half)
        g_depth.add_color_stop_rgba(0.0, 0.0, 0.0, 0.0, 0.0)
        g_depth.add_color_stop_rgba(0.7, 0.0, 0.02, 0.04, 0.10)
        g_depth.add_color_stop_rgba(1.0, 0.0, 0.01, 0.03, 0.28)
        cr.set_source(g_depth)
        cr.rectangle(0, 0, aw, ah)
        cr.fill()

        # Mild top-left highlight
        hx = cx - half * 0.25
        hy = cy - half * 0.30
        g_hi = cairo.RadialGradient(hx, hy, 0, hx, hy, half * 0.85)
        g_hi.add_color_stop_rgba(0.0, 0.75, 0.88, 1.0, 0.09)
        g_hi.add_color_stop_rgba(0.45, 0.55, 0.72, 0.90, 0.03)
        g_hi.add_color_stop_rgba(1.0, 0.4, 0.5, 0.7, 0.0)
        cr.set_source(g_hi)
        cr.rectangle(0, 0, aw, ah)
        cr.fill()

        self._base_surf = surf
        self._base_key = key
        self._last_base_build_t = now
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
        """Direct draw when cairo ImageSurface cache is unavailable."""
        cr.set_source_rgb(0.06, 0.09, 0.13)
        cr.rectangle(0, 0, aw, ah)
        cr.fill()
        φ0 = math.radians(lat0)
        sin_φ0, cos_φ0 = math.sin(φ0), math.cos(φ0)
        λ0 = math.radians(lon0)
        cr.set_source_rgb(0.16, 0.20, 0.24)
        for ring in self._land_ll:
            started = False
            for lat, lon in ring:
                pt = _project_fast(
                    lat,
                    lon,
                    R=R,
                    cx=cx,
                    cy=cy,
                    sin_φ0=sin_φ0,
                    cos_φ0=cos_φ0,
                    λ0=λ0,
                    limb_eps=0.0,
                )
                if pt is None:
                    started = False
                    continue
                if not started:
                    cr.move_to(pt[0], pt[1])
                    started = True
                else:
                    cr.line_to(pt[0], pt[1])
            if started:
                cr.close_path()
        cr.fill()

    def _draw(self, _area, cr, width: int, height: int, *_ud) -> None:
        aw, ah = float(width), float(height)
        if aw < 2 or ah < 2:
            return
        t = (GLib.get_monotonic_time() - self._t0) / 1_000_000.0

        half = self._half_diag(aw, ah)
        cx, cy = aw / 2.0, ah / 2.0
        R = self._earth_radius(aw, ah)
        lat0, lon0 = world_to_lonlat(self._cam_x, self._cam_y)

        base = self._ensure_base_layer(
            int(aw), int(ah), half, R, cx, cy, lat0, lon0
        )
        if base is not None:
            cr.set_source_surface(base, 0, 0)
            cr.paint()
        else:
            self._draw_base_fallback(cr, aw, ah, half, R, cx, cy, lat0, lon0)

        # Cities only (cheap) — full square
        φ0 = math.radians(lat0)
        sin_φ0, cos_φ0 = math.sin(φ0), math.cos(φ0)
        λ0 = math.radians(lon0)

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

            is_active = (
                self._active_country
                and city.country_code == self._active_country
                and (not self._active_city or city.city_code == self._active_city)
            )
            is_hover = self._hover is city

            if is_active:
                phase = math.sin(t * 3.2) * 0.5 + 0.5
                r = 4.2 + phase * 2.4
                cr.set_source_rgba(0.55, 0.85, 1.0, 0.15 + phase * 0.2)
                cr.arc(vx, vy, r * 2.4, 0, 2 * math.pi)
                cr.fill()
                cr.set_source_rgba(0.95, 0.97, 1.0, 0.85 + phase * 0.15)
            elif is_hover:
                r = 3.8
                cr.set_source_rgba(0.85, 0.92, 1.0, 0.95)
            else:
                r = 2.25
                cr.set_source_rgba(0.45, 0.72, 0.82, 0.72)

            cr.arc(vx, vy, r, 0, 2 * math.pi)
            cr.fill()
