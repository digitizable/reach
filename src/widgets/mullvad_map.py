"""Animated Mullvad-style relay map (city dots from public Mullvad API).

Mullvad's client is open source (GPL-3). Map data uses their public
relay location feed; the SVG is generated in-app (no proprietary assets).
"""

from __future__ import annotations

import math
from pathlib import Path

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from core.mullvad import RelayCity, load_catalog

# Equirectangular world map viewBox
_MAP_W = 720
_MAP_H = 360


def _project(lat: float, lon: float) -> tuple[float, float]:
    """lat/lon → SVG x,y (equirectangular, y-down)."""
    x = (lon + 180.0) / 360.0 * _MAP_W
    y = (90.0 - lat) / 180.0 * _MAP_H
    return x, y


def build_map_svg(
    cities: list[RelayCity],
    *,
    active_country: str = "",
    active_city: str = "",
    width: int = _MAP_W,
    height: int = _MAP_H,
) -> str:
    """Return SVG with pulsing city markers."""
    dots: list[str] = []
    active_country = (active_country or "").lower()
    active_city = (active_city or "").lower()

    for i, c in enumerate(cities):
        x, y = _project(c.latitude, c.longitude)
        if x < 0 or x > _MAP_W or y < 0 or y > _MAP_H:
            continue
        is_active = (
            active_country
            and c.country_code == active_country
            and (not active_city or c.city_code == active_city)
        )
        delay = (i % 20) * 0.12
        r = 4.2 if is_active else 2.4
        fill = "#e8e8e8" if is_active else "#6a8a9a"
        opacity = "1" if is_active else "0.75"
        # Soft pulse via SMIL (works in rsvg / many SVG viewers)
        anim = (
            f'<animate attributeName="opacity" values="0.35;1;0.35" '
            f'dur="2.4s" begin="{delay:.2f}s" repeatCount="indefinite"/>'
            f'<animate attributeName="r" values="{r * 0.85};{r * 1.35};{r * 0.85}" '
            f'dur="2.4s" begin="{delay:.2f}s" repeatCount="indefinite"/>'
        )
        if is_active:
            anim = (
                f'<animate attributeName="opacity" values="0.55;1;0.55" '
                f'dur="1.6s" repeatCount="indefinite"/>'
                f'<animate attributeName="r" values="{r};{r * 1.6};{r}" '
                f'dur="1.6s" repeatCount="indefinite"/>'
            )
        dots.append(
            f'<circle class="mv-dot{" mv-active" if is_active else ""}" '
            f'cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}" opacity="{opacity}">'
            f"{anim}</circle>"
        )

    # Subtle graticule + dark ocean (Mullvad-ish)
    lines = []
    for lon in range(-180, 181, 30):
        x = (lon + 180) / 360 * _MAP_W
        lines.append(
            f'<line x1="{x}" y1="0" x2="{x}" y2="{_MAP_H}" '
            f'stroke="#2a2a30" stroke-width="0.6" opacity="0.5"/>'
        )
    for lat in range(-60, 61, 30):
        y = (90 - lat) / 180 * _MAP_H
        lines.append(
            f'<line x1="0" y1="{y}" x2="{_MAP_W}" y2="{y}" '
            f'stroke="#2a2a30" stroke-width="0.6" opacity="0.5"/>'
        )

    # Simplified land hint: soft equatorial band (not a full atlas)
    land = (
        f'<ellipse cx="{_MAP_W/2}" cy="{_MAP_H*0.48}" rx="{_MAP_W*0.42}" '
        f'ry="{_MAP_H*0.28}" fill="#1a1a20" opacity="0.55"/>'
    )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_MAP_W} {_MAP_H}"
     width="{width}" height="{height}">
  <rect width="100%" height="100%" fill="#0c0c10"/>
  {land}
  {"".join(lines)}
  <g class="mv-cities">
    {"".join(dots)}
  </g>
</svg>
"""


class MullvadMap(Gtk.Box):
    """Widget showing animated Mullvad city markers."""

    def __init__(self, *, height: int = 200) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("mullvad-map")
        self.set_hexpand(True)
        self._height = height
        self._active_country = ""
        self._active_city = ""

        self._picture = Gtk.Picture()
        self._picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._picture.set_can_shrink(True)
        self._picture.set_size_request(-1, height)
        self._picture.set_hexpand(True)
        self.append(self._picture)

        cap = Gtk.Label(
            label="Mullvad relay cities · open-source client (GPL-3)",
            xalign=0.5,
        )
        cap.add_css_class("mullvad-map-caption")
        cap.set_halign(Gtk.Align.CENTER)
        self.append(cap)

        self._pulse_id: int | None = None
        self._frame = 0
        self.refresh()

    def set_active(self, country: str = "", city: str = "") -> None:
        self._active_country = (country or "").lower()
        self._active_city = (city or "").lower()
        self.refresh()

    def refresh(self) -> None:
        try:
            cities = load_catalog().map_cities
            if not cities:
                cities = []
        except Exception:
            cities = []
        # Animate by regenerating with phase via GLib timeout on frame
        svg = build_map_svg(
            cities,
            active_country=self._active_country,
            active_city=self._active_city,
            width=_MAP_W,
            height=_MAP_H,
        )
        self._load_svg(svg)
        self._ensure_pulse()

    def _load_svg(self, svg: str) -> None:
        try:
            data = svg.encode("utf-8")
            # GdkPixbuf loader for SVG
            loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
            loader.write(data)
            loader.close()
            pb = loader.get_pixbuf()
            if pb is None:
                return
            # Scale to widget height
            h = max(120, self._height)
            w = int(h * (_MAP_W / _MAP_H))
            pb2 = pb.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
            texture = Gdk.Texture.new_for_pixbuf(pb2 or pb)
            self._picture.set_paintable(texture)
        except Exception:
            # Fallback: write temp and load file
            try:
                path = Path("/tmp/reach-mullvad-map.svg")
                path.write_text(svg, encoding="utf-8")
                self._picture.set_filename(str(path))
            except Exception:
                pass

    def _ensure_pulse(self) -> None:
        """Re-render periodically so SMIL-less pixbuf still 'breathes' via frame."""
        if self._pulse_id is not None:
            return

        def tick() -> bool:
            self._frame = (self._frame + 1) % 24
            # Rebuild with slight radius modulation for active dots without SMIL
            try:
                cities = load_catalog().map_cities
            except Exception:
                return True
            # Inject frame into opacity via custom build
            svg = self._svg_frame(cities, self._frame)
            self._load_svg(svg)
            return True

        self._pulse_id = GLib.timeout_add(180, tick)

    def _svg_frame(self, cities: list[RelayCity], frame: int) -> str:
        # Modulate active pulse with sin
        phase = (math.sin(frame / 24.0 * 2 * math.pi) + 1) / 2  # 0..1
        dots: list[str] = []
        for i, c in enumerate(cities):
            x, y = _project(c.latitude, c.longitude)
            if x < 0 or x > _MAP_W or y < 0 or y > _MAP_H:
                continue
            is_active = (
                self._active_country
                and c.country_code == self._active_country
                and (not self._active_city or c.city_code == self._active_city)
            )
            # Stagger idle dots
            idle_phase = (math.sin((frame + i * 0.7) / 24.0 * 2 * math.pi) + 1) / 2
            if is_active:
                r = 3.5 + phase * 2.5
                fill = "#f0f0f0"
                op = 0.65 + phase * 0.35
            else:
                r = 2.2 + idle_phase * 0.6
                fill = "#5a7a88"
                op = 0.45 + idle_phase * 0.4
            dots.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}" '
                f'fill="{fill}" opacity="{op:.2f}"/>'
            )
        lines = []
        for lon in range(-180, 181, 30):
            x = (lon + 180) / 360 * _MAP_W
            lines.append(
                f'<line x1="{x}" y1="0" x2="{x}" y2="{_MAP_H}" '
                f'stroke="#2a2a30" stroke-width="0.6" opacity="0.45"/>'
            )
        for lat in range(-60, 61, 30):
            y = (90 - lat) / 180 * _MAP_H
            lines.append(
                f'<line x1="0" y1="{y}" x2="{_MAP_W}" y2="{y}" '
                f'stroke="#2a2a30" stroke-width="0.6" opacity="0.45"/>'
            )
        land = (
            f'<ellipse cx="{_MAP_W/2}" cy="{_MAP_H*0.48}" rx="{_MAP_W*0.42}" '
            f'ry="{_MAP_H*0.28}" fill="#1a1a20" opacity="0.55"/>'
        )
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_MAP_W} {_MAP_H}">
  <rect width="100%" height="100%" fill="#0c0c10"/>
  {land}
  {"".join(lines)}
  <g>{"".join(dots)}</g>
</svg>
"""

    def do_unrealize(self) -> None:
        if self._pulse_id is not None:
            GLib.source_remove(self._pulse_id)
            self._pulse_id = None
        Gtk.Box.do_unrealize(self)
