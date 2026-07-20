"""Custom animated Mullvad relay viewport (DrawingArea).

Mullvad’s client is open source (GPL-3). City coordinates come from their
public relay API. The map chrome is Reach’s own (no proprietary assets).

- Equirectangular world viewport with soft land silhouettes
- Continuous pulse animation via frame clock
- Gentle fly-to when the active location changes
- Click a city to set Mullvad relay location (when CLI is present)
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable

from gi.repository import GLib, Gtk

from core.mullvad import RelayCity, cli_path, load_catalog, set_location

# Logical world map (equirectangular)
_WORLD_W = 720.0
_WORLD_H = 360.0

# Soft land “blobs” (lon_w, lat_n, lon_e, lat_s) — stylized continents, not GIS
_LAND_BOXES: tuple[tuple[float, float, float, float], ...] = (
    # North America
    (-130.0, 55.0, -60.0, 25.0),
    (-120.0, 72.0, -80.0, 55.0),
    # South America
    (-80.0, 10.0, -35.0, -55.0),
    # Europe
    (-10.0, 60.0, 40.0, 36.0),
    # Africa
    (-18.0, 35.0, 50.0, -35.0),
    # Asia
    (40.0, 55.0, 145.0, 10.0),
    (60.0, 70.0, 180.0, 45.0),
    # Australia / NZ
    (110.0, -10.0, 155.0, -45.0),
    (165.0, -34.0, 179.0, -48.0),
)


def lonlat_to_world(lat: float, lon: float) -> tuple[float, float]:
    x = (lon + 180.0) / 360.0 * _WORLD_W
    y = (90.0 - lat) / 180.0 * _WORLD_H
    return x, y


def world_to_lonlat(x: float, y: float) -> tuple[float, float]:
    lon = x / _WORLD_W * 360.0 - 180.0
    lat = 90.0 - y / _WORLD_H * 180.0
    return lat, lon


class MullvadMap(Gtk.Box):
    """Animated relay-city viewport for Doors (and anywhere else)."""

    def __init__(
        self,
        *,
        height: int = 200,
        on_location: Callable[[str, str, str], None] | None = None,
        on_toast: Callable[[str], None] | None = None,
        interactive: bool = True,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("mullvad-map")
        self.set_hexpand(True)
        self._height = max(140, height)
        self._on_location = on_location
        self._on_toast = on_toast
        self._interactive = interactive

        self._cities: list[RelayCity] = []
        self._active_country = ""
        self._active_city = ""
        self._hover: RelayCity | None = None
        self._t0 = GLib.get_monotonic_time()
        self._busy = False

        # Camera (world units). Default full world.
        self._cam_x = 0.0
        self._cam_y = 0.0
        self._cam_scale = 1.0
        self._target_x = 0.0
        self._target_y = 0.0
        self._target_scale = 1.0

        self._area = Gtk.DrawingArea()
        self._area.add_css_class("mullvad-map-viewport")
        self._area.set_content_height(self._height)
        self._area.set_hexpand(True)
        self._area.set_vexpand(False)
        self._area.set_draw_func(self._draw)
        self._area.set_cursor_from_name("pointer" if interactive else "default")
        self.append(self._area)

        # Motion for hover
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self._area.add_controller(motion)

        if interactive:
            click = Gtk.GestureClick()
            click.set_button(1)
            click.connect("pressed", self._on_click)
            self._area.add_controller(click)

        self._caption = Gtk.Label(label="", xalign=0.5)
        self._caption.add_css_class("mullvad-map-caption")
        self._caption.set_halign(Gtk.Align.CENTER)
        self._caption.set_wrap(True)
        self.append(self._caption)

        self._tick_id = self._area.add_tick_callback(self._on_tick)
        GLib.idle_add(self._load_cities)

    def do_unrealize(self) -> None:
        if self._tick_id:
            self._area.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        Gtk.Box.do_unrealize(self)

    # ── public API ────────────────────────────────────────────────

    def set_active(self, country: str = "", city: str = "") -> None:
        self._active_country = (country or "").lower()
        self._active_city = (city or "").lower()
        self._fly_to_active()
        self._update_caption()
        self._area.queue_draw()

    def refresh(self) -> None:
        self._load_cities()

    # ── data ──────────────────────────────────────────────────────

    def _load_cities(self) -> bool:
        try:
            cat = load_catalog()
            self._cities = list(cat.map_cities)
        except Exception:
            self._cities = []
        self._update_caption()
        self._fly_to_active()
        self._area.queue_draw()
        return False

    def _update_caption(self) -> None:
        n = len(self._cities)
        if self._hover is not None:
            h = self._hover
            self._caption.set_text(f"{h.city_name}, {h.country_name} · click to select")
            return
        if self._active_country and self._active_country != "any":
            label = self._active_country.upper()
            if self._active_city:
                label = f"{self._active_city.upper()}, {label}"
            self._caption.set_text(
                f"Mullvad · {label} · {n} cities · click a city to set relay"
            )
        else:
            self._caption.set_text(
                f"Mullvad relay map · {n} cities · open-source client (GPL-3)"
            )

    # ── camera ────────────────────────────────────────────────────

    def _fly_to_active(self) -> None:
        if not self._active_country or self._active_country == "any":
            self._target_x, self._target_y = 0.0, 0.0
            self._target_scale = 1.0
            return
        matches = [
            c
            for c in self._cities
            if c.country_code == self._active_country
            and (not self._active_city or c.city_code == self._active_city)
        ]
        if not matches:
            matches = [
                c for c in self._cities if c.country_code == self._active_country
            ]
        if not matches:
            return
        # Centroid of matches
        lats = [c.latitude for c in matches]
        lons = [c.longitude for c in matches]
        lat = sum(lats) / len(lats)
        lon = sum(lons) / len(lons)
        wx, wy = lonlat_to_world(lat, lon)
        # Zoom in a bit when city-level
        scale = 2.4 if self._active_city else 1.7
        # Camera is top-left of visible world window after scale
        # Center (wx,wy) in view
        self._target_scale = scale
        # Store as focus point; applied in draw via transform
        self._target_x = wx
        self._target_y = wy

    def _ease_camera(self, dt: float) -> None:
        # Exponential ease toward target focus / scale
        k = min(1.0, 4.0 * dt)
        self._cam_scale += (self._target_scale - self._cam_scale) * k
        self._cam_x += (self._target_x - self._cam_x) * k
        self._cam_y += (self._target_y - self._cam_y) * k

    # ── input ─────────────────────────────────────────────────────

    def _view_to_world(self, vx: float, vy: float) -> tuple[float, float]:
        """Widget coords → world coords under current camera."""
        alloc = self._area.get_allocation()
        aw = max(1.0, float(alloc.width))
        ah = max(1.0, float(alloc.height))
        # Uniform scale to fit world into widget, then camera zoom
        fit = min(aw / _WORLD_W, ah / _WORLD_H)
        s = fit * self._cam_scale
        # World point under center of widget is (cam_x, cam_y)
        cx, cy = aw / 2.0, ah / 2.0
        wx = self._cam_x + (vx - cx) / s
        wy = self._cam_y + (vy - cy) / s
        return wx, wy

    def _nearest_city(self, wx: float, wy: float, *, max_dist: float = 18.0) -> RelayCity | None:
        best: RelayCity | None = None
        best_d = max_dist
        for c in self._cities:
            x, y = lonlat_to_world(c.latitude, c.longitude)
            d = math.hypot(x - wx, y - wy)
            if d < best_d:
                best_d = d
                best = c
        return best

    def _on_motion(self, _c: Gtk.EventControllerMotion, x: float, y: float) -> None:
        wx, wy = self._view_to_world(x, y)
        hit = self._nearest_city(wx, wy)
        if hit is not self._hover:
            self._hover = hit
            self._update_caption()
            self._area.queue_draw()

    def _on_leave(self, *_a) -> None:
        if self._hover is not None:
            self._hover = None
            self._update_caption()
            self._area.queue_draw()

    def _on_click(self, _g: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        if self._busy or not cli_path():
            if self._on_toast and not cli_path():
                self._on_toast("Mullvad CLI not installed")
            return
        wx, wy = self._view_to_world(x, y)
        city = self._nearest_city(wx, wy, max_dist=22.0)
        if city is None:
            return
        self._busy = True
        self._caption.set_text(f"Setting · {city.city_name}, {city.country_name}…")

        def worker() -> None:
            ok, msg = set_location(city.country_code, city.city_code, None)

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
                            f"Mullvad · {city.city_name}, {city.country_name}"
                        )
                else:
                    self._caption.set_text(msg or "Could not set location")
                    if self._on_toast:
                        self._on_toast(msg or "Mullvad location failed")
                return False

            GLib.idle_add(done)

        threading.Thread(target=worker, name="mullvad-map-set", daemon=True).start()

    # ── animation ─────────────────────────────────────────────────

    def _on_tick(self, _widget: Gtk.Widget, frame_clock) -> bool:
        # Ease camera + continuous redraw for pulse
        t = frame_clock.get_frame_time() / 1_000_000.0
        if not hasattr(self, "_last_t"):
            self._last_t = t
        dt = max(0.0, min(0.05, t - self._last_t))
        self._last_t = t
        self._ease_camera(dt)
        self._area.queue_draw()
        return GLib.SOURCE_CONTINUE

    # ── draw ──────────────────────────────────────────────────────

    def _draw(
        self,
        area: Gtk.DrawingArea,
        cr,  # cairo.Context
        width: int,
        height: int,
        *_ud,
    ) -> None:
        aw = float(width)
        ah = float(height)
        t = (GLib.get_monotonic_time() - self._t0) / 1_000_000.0

        # Ocean
        cr.set_source_rgb(0.047, 0.047, 0.063)  # #0c0c10
        cr.rectangle(0, 0, aw, ah)
        cr.fill()

        fit = min(aw / _WORLD_W, ah / _WORLD_H)
        s = fit * self._cam_scale
        cx, cy = aw / 2.0, ah / 2.0

        def w2v(wx: float, wy: float) -> tuple[float, float]:
            return cx + (wx - self._cam_x) * s, cy + (wy - self._cam_y) * s

        # Clip to rounded viewport (soft inset)
        cr.save()
        radius = 10.0
        self._rounded_rect(cr, 1, 1, aw - 2, ah - 2, radius)
        cr.clip()

        # Graticule
        cr.set_line_width(max(0.6, 0.6 * s / fit))
        cr.set_source_rgba(0.16, 0.16, 0.19, 0.55)
        for lon in range(-180, 181, 30):
            wx0, _ = lonlat_to_world(0, float(lon))
            x0, _ = w2v(wx0, 0)
            cr.move_to(x0, 0)
            cr.line_to(x0, ah)
            cr.stroke()
        for lat in range(-60, 61, 30):
            _, wy0 = lonlat_to_world(float(lat), 0)
            _, y0 = w2v(0, wy0)
            cr.move_to(0, y0)
            cr.line_to(aw, y0)
            cr.stroke()

        # Land silhouettes
        for lon_w, lat_n, lon_e, lat_s in _LAND_BOXES:
            x0, y0 = lonlat_to_world(lat_n, lon_w)
            x1, y1 = lonlat_to_world(lat_s, lon_e)
            vx0, vy0 = w2v(min(x0, x1), min(y0, y1))
            vx1, vy1 = w2v(max(x0, x1), max(y0, y1))
            cr.set_source_rgba(0.10, 0.10, 0.13, 0.85)
            self._rounded_rect(
                cr, vx0, vy0, max(2, vx1 - vx0), max(2, vy1 - vy0), 6 * s / fit
            )
            cr.fill()

        # Cities
        for i, city in enumerate(self._cities):
            wx, wy = lonlat_to_world(city.latitude, city.longitude)
            vx, vy = w2v(wx, wy)
            if vx < -20 or vx > aw + 20 or vy < -20 or vy > ah + 20:
                continue

            is_active = (
                self._active_country
                and city.country_code == self._active_country
                and (not self._active_city or city.city_code == self._active_city)
            )
            is_hover = self._hover is city

            # Phase: staggered pulse
            phase = math.sin(t * 2.2 + i * 0.37) * 0.5 + 0.5  # 0..1
            if is_active:
                phase = math.sin(t * 3.4) * 0.5 + 0.5
                base_r = 4.5
                r = base_r + phase * 2.8
                # Outer glow ring
                cr.set_source_rgba(0.85, 0.90, 1.0, 0.12 + phase * 0.18)
                cr.arc(vx, vy, r * 2.2, 0, 2 * math.pi)
                cr.fill()
                cr.set_source_rgba(0.94, 0.94, 0.96, 0.75 + phase * 0.25)
            elif is_hover:
                r = 3.8 + phase * 0.8
                cr.set_source_rgba(0.75, 0.85, 0.95, 0.9)
            else:
                r = 2.1 + phase * 0.7
                cr.set_source_rgba(0.38, 0.52, 0.58, 0.45 + phase * 0.4)

            cr.arc(vx, vy, r, 0, 2 * math.pi)
            cr.fill()

        cr.restore()

        # Soft vignette border
        cr.set_source_rgba(0.15, 0.15, 0.18, 1.0)
        cr.set_line_width(1.0)
        self._rounded_rect(cr, 0.5, 0.5, aw - 1, ah - 1, radius)
        cr.stroke()

    @staticmethod
    def _rounded_rect(cr, x, y, w, h, r) -> None:
        r = min(r, w / 2, h / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()


# Keep name used by china_ingress / tests
def build_map_svg(cities: list[RelayCity], **_kw) -> str:
    """Legacy helper — minimal static SVG snapshot."""
    dots = []
    for c in cities:
        x, y = lonlat_to_world(c.latitude, c.longitude)
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="#6a8a9a"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_WORLD_W} {_WORLD_H}">'
        f'<rect width="100%" height="100%" fill="#0c0c10"/>'
        f'{"".join(dots)}</svg>'
    )
