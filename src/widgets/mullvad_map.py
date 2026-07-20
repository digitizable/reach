"""Custom animated Mullvad relay map viewport (real world land + city dots).

- Equirectangular land from simplified public-domain country outlines
- Mullvad city markers (public API) with frame-clock pulse
- Fly-to active location; click city to set Mullvad relay (GPL-3 CLI)
"""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from gi.repository import GLib, Gtk

from app_config import project_root
from core.mullvad import RelayCity, cli_path, load_catalog, set_location

_WORLD_W = 720.0
_WORLD_H = 360.0


def lonlat_to_world(lat: float, lon: float) -> tuple[float, float]:
    x = (lon + 180.0) / 360.0 * _WORLD_W
    y = (90.0 - lat) / 180.0 * _WORLD_H
    return x, y


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


class MullvadMap(Gtk.Box):
    """Animated world map with Mullvad relay cities."""

    def __init__(
        self,
        *,
        height: int = 220,
        on_location: Callable[[str, str, str], None] | None = None,
        on_toast: Callable[[str], None] | None = None,
        interactive: bool = True,
        fill: bool = False,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
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

        # Camera focus in world coords + scale (1 = full world)
        self._cam_x = _WORLD_W / 2
        self._cam_y = _WORLD_H / 2
        self._cam_scale = 1.0
        self._target_x = self._cam_x
        self._target_y = self._cam_y
        self._target_scale = 1.0

        self._land_w, self._land_h, self._land = load_land_polygons()

        self._area = Gtk.DrawingArea()
        self._area.add_css_class("mullvad-map-viewport")
        if fill:
            self._area.set_vexpand(True)
            self._area.set_hexpand(True)
            # Fill remaining pane height without forcing a tall minimum
            self._area.set_content_height(max(140, self._height))
        else:
            self._area.set_content_height(self._height)
            self._area.set_hexpand(True)
        self._area.set_draw_func(self._draw)
        if interactive:
            self._area.set_cursor_from_name("pointer")
        self.append(self._area)

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
        if getattr(self, "_tick_id", 0):
            self._area.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        Gtk.Box.do_unrealize(self)

    def set_active(self, country: str = "", city: str = "") -> None:
        self._active_country = (country or "").lower()
        self._active_city = (city or "").lower()
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
                # Prefer human city name if we have it
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
                f"Mullvad map · {n} cities · open-source client (GPL-3)"
            )

    def _fly_to_active(self) -> None:
        """Zoom to the selected country (full span) or city (tight)."""
        if not self._active_country or self._active_country == "any":
            self._target_x = _WORLD_W / 2
            self._target_y = _WORLD_H / 2
            self._target_scale = 1.0
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

        pts = [lonlat_to_world(c.latitude, c.longitude) for c in matches]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]

        if city_mode or len(pts) == 1:
            # City: tight frame on the marker
            cx, cy = xs[0], ys[0]
            half = 14.0
            min_x, max_x = cx - half, cx + half
            min_y, max_y = cy - half, cy + half
            fill = 0.70
            min_scale, max_scale = 10.0, 24.0
        else:
            # Country: full bounding box of ALL cities so coasts stay in frame
            # (e.g. USA must include California and the East Coast).
            pad = 18.0
            min_x, max_x = min(xs) - pad, max(xs) + pad
            min_y, max_y = min(ys) - pad, max(ys) + pad
            # Modest minimum span for tiny countries (1–2 cities)
            min_span_x, min_span_y = 40.0, 30.0
            if max_x - min_x < min_span_x:
                mid = (min_x + max_x) / 2
                min_x, max_x = mid - min_span_x / 2, mid + min_span_x / 2
            if max_y - min_y < min_span_y:
                mid = (min_y + max_y) / 2
                min_y, max_y = mid - min_span_y / 2, mid + min_span_y / 2
            # Fit whole country with a little margin; don't force extreme zoom
            fill = 0.88
            min_scale, max_scale = 1.4, 12.0

        min_x = max(0.0, min_x)
        max_x = min(_WORLD_W, max_x)
        min_y = max(0.0, min_y)
        max_y = min(_WORLD_H, max_y)

        bbox_w = max(max_x - min_x, 8.0)
        bbox_h = max(max_y - min_y, 8.0)
        scale_x = (_WORLD_W * fill) / bbox_w
        scale_y = (_WORLD_H * fill) / bbox_h
        scale = min(scale_x, scale_y)
        scale = max(min_scale, min(scale, max_scale))

        self._target_x = (min_x + max_x) / 2.0
        self._target_y = (min_y + max_y) / 2.0
        self._target_scale = scale

    def _ease_camera(self, dt: float) -> None:
        # Smooth but decisive ease toward a deep zoom target.
        dist = math.hypot(
            self._target_x - self._cam_x, self._target_y - self._cam_y
        ) + abs(self._target_scale - self._cam_scale) * 28
        base = 3.2 if dist < 50 else 5.0
        k = min(1.0, base * dt)
        self._cam_scale += (self._target_scale - self._cam_scale) * k
        self._cam_x += (self._target_x - self._cam_x) * k
        self._cam_y += (self._target_y - self._cam_y) * k

    def _view_to_world(self, vx: float, vy: float) -> tuple[float, float]:
        alloc = self._area.get_allocation()
        aw = max(1.0, float(alloc.width))
        ah = max(1.0, float(alloc.height))
        fit = min(aw / _WORLD_W, ah / _WORLD_H)
        s = fit * self._cam_scale
        cx, cy = aw / 2.0, ah / 2.0
        return self._cam_x + (vx - cx) / s, self._cam_y + (vy - cy) / s

    def _nearest_city(
        self, wx: float, wy: float, *, max_dist: float = 16.0
    ) -> RelayCity | None:
        best: RelayCity | None = None
        best_d = max_dist
        for c in self._cities:
            x, y = lonlat_to_world(c.latitude, c.longitude)
            d = math.hypot(x - wx, y - wy)
            if d < best_d:
                best_d = d
                best = c
        return best

    def _on_motion(self, _c, x: float, y: float) -> None:
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

    def _on_click(self, _g, _n: int, x: float, y: float) -> None:
        if self._busy:
            return
        if not cli_path():
            if self._on_toast:
                self._on_toast("Mullvad CLI not installed")
            return
        wx, wy = self._view_to_world(x, y)
        city = self._nearest_city(wx, wy, max_dist=20.0)
        if city is None:
            return
        self._busy = True
        self._caption.set_text(
            f"Selecting · {city.city_name}, {city.country_name}…"
        )

        def worker() -> None:
            # Location only — do not leave Mullvad connected.
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
        t = frame_clock.get_frame_time() / 1_000_000.0
        if not self._last_t:
            self._last_t = t
        dt = max(0.0, min(0.05, t - self._last_t))
        self._last_t = t
        self._ease_camera(dt)
        self._area.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _draw(self, _area, cr, width: int, height: int, *_ud) -> None:
        aw, ah = float(width), float(height)
        t = (GLib.get_monotonic_time() - self._t0) / 1_000_000.0

        # Ocean
        cr.set_source_rgb(0.055, 0.07, 0.10)  # deep blue-gray
        cr.rectangle(0, 0, aw, ah)
        cr.fill()

        fit = min(aw / _WORLD_W, ah / _WORLD_H)
        s = fit * self._cam_scale
        cx, cy = aw / 2.0, ah / 2.0

        def w2v(wx: float, wy: float) -> tuple[float, float]:
            return cx + (wx - self._cam_x) * s, cy + (wy - self._cam_y) * s

        cr.save()
        radius = 12.0
        self._rounded_rect(cr, 1, 1, aw - 2, ah - 2, radius)
        cr.clip()

        # Graticule
        cr.set_line_width(max(0.5, 0.5 * s / fit))
        cr.set_source_rgba(0.25, 0.32, 0.38, 0.35)
        for lon in range(-180, 181, 30):
            wx, _ = lonlat_to_world(0.0, float(lon))
            x, _ = w2v(wx, 0)
            cr.move_to(x, 0)
            cr.line_to(x, ah)
            cr.stroke()
        for lat in range(-60, 61, 30):
            _, wy = lonlat_to_world(float(lat), 0.0)
            _, y = w2v(0, wy)
            cr.move_to(0, y)
            cr.line_to(aw, y)
            cr.stroke()

        # Land — real country outlines
        cr.set_source_rgb(0.14, 0.17, 0.20)  # muted land
        for ring in self._land:
            if len(ring) < 3:
                continue
            x0, y0 = w2v(ring[0][0], ring[0][1])
            cr.move_to(x0, y0)
            for px, py in ring[1:]:
                x, y = w2v(px, py)
                cr.line_to(x, y)
            cr.close_path()
        cr.fill()

        # Land edges for definition
        cr.set_source_rgba(0.22, 0.28, 0.32, 0.55)
        cr.set_line_width(max(0.4, 0.45 * s / fit))
        for ring in self._land:
            if len(ring) < 3:
                continue
            x0, y0 = w2v(ring[0][0], ring[0][1])
            cr.move_to(x0, y0)
            for px, py in ring[1:]:
                x, y = w2v(px, py)
                cr.line_to(x, y)
            cr.close_path()
            cr.stroke()

        # Relay cities
        for i, city in enumerate(self._cities):
            wx, wy = lonlat_to_world(city.latitude, city.longitude)
            vx, vy = w2v(wx, wy)
            if vx < -16 or vx > aw + 16 or vy < -16 or vy > ah + 16:
                continue

            is_active = (
                self._active_country
                and city.country_code == self._active_country
                and (not self._active_city or city.city_code == self._active_city)
            )
            is_hover = self._hover is city
            phase = math.sin(t * 2.1 + i * 0.41) * 0.5 + 0.5

            if is_active:
                phase = math.sin(t * 3.2) * 0.5 + 0.5
                r = 4.2 + phase * 2.4
                cr.set_source_rgba(0.55, 0.85, 1.0, 0.15 + phase * 0.2)
                cr.arc(vx, vy, r * 2.4, 0, 2 * math.pi)
                cr.fill()
                cr.set_source_rgba(0.95, 0.97, 1.0, 0.85 + phase * 0.15)
            elif is_hover:
                r = 3.6 + phase * 0.7
                cr.set_source_rgba(0.85, 0.92, 1.0, 0.95)
            else:
                r = 2.0 + phase * 0.65
                cr.set_source_rgba(0.45, 0.72, 0.82, 0.5 + phase * 0.4)

            cr.arc(vx, vy, r, 0, 2 * math.pi)
            cr.fill()

        cr.restore()

        # Frame
        cr.set_source_rgba(0.18, 0.20, 0.24, 1.0)
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
