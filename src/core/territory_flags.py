"""Flag-filled territory map silhouettes.

Country outlines (mapsicon / white SVG) act as an alpha mask over a
painted national flag — same shapes as Territories, with flag color inside.
"""

from __future__ import annotations

import math
from functools import lru_cache

from gi.repository import Gdk, GdkPixbuf

from app_config import project_root

try:
    import cairo
except ImportError:  # pragma: no cover
    cairo = None  # type: ignore


def flag_filled_map_pixbuf(
    *,
    code: str,
    map_asset: str | None,
    size: int = 256,
) -> GdkPixbuf.Pixbuf | None:
    """Render map silhouette filled with the territory’s flag.

    Returns None for globe / custom / missing assets (caller shows plain SVG).
    """
    if cairo is None:
        return None
    code = (code or "").strip().upper()
    if not code or code == "XX":
        return None
    name = (map_asset or "").strip()
    if not name or name == "globe.svg":
        return None
    path = project_root() / "data" / "assets" / name
    if not path.is_file():
        return None
    size = max(64, min(512, int(size)))
    return _render_cached(code, str(path.resolve()), size)


@lru_cache(maxsize=32)
def _render_cached(code: str, map_path: str, size: int) -> GdkPixbuf.Pixbuf | None:
    try:
        mask = GdkPixbuf.Pixbuf.new_from_file_at_size(map_path, size, size)
    except Exception:
        return None
    if mask.get_width() < 8 or mask.get_height() < 8:
        return None

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)

    # 1) Paint full-bleed national flag
    paint_flag(ctx, code, float(size), float(size))

    # 2) Keep only the map silhouette (white → opaque in the SVG mask)
    ctx.set_operator(cairo.OPERATOR_DEST_IN)
    Gdk.cairo_set_source_pixbuf(ctx, mask, 0, 0)
    ctx.paint()

    pb = Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
    return pb


def paint_flag(ctx: "cairo.Context", code: str, w: float, h: float) -> None:
    """Paint a national flag into *ctx* (0,0)–(w,h).

    Prefers a full SVG under data/assets/flags/{code}.svg when present
    (accurate emblem / script). Falls back to a simplified Cairo painter.
    """
    code = code.upper()
    if _paint_flag_svg(ctx, code, w, h):
        return
    painters = {
        "CN": _flag_cn,
        "IR": _flag_ir,
        "RU": _flag_ru,
        "TR": _flag_tr,
        "CU": _flag_cu,
        "AE": _flag_ae,
    }
    fn = painters.get(code)
    if fn is None:
        # Generic: soft blue field (unknown territory)
        ctx.set_source_rgb(0.22, 0.35, 0.55)
        ctx.rectangle(0, 0, w, h)
        ctx.fill()
        return
    fn(ctx, w, h)


def _paint_flag_svg(
    ctx: "cairo.Context", code: str, w: float, h: float
) -> bool:
    """Load data/assets/flags/{code}.svg with object-fit: cover (crop, no squash).

    Landscape flags are scaled so they fully cover the square canvas, then
    centered and cropped — same full-bleed look as the hand-drawn flags.
    """
    path = project_root() / "data" / "assets" / "flags" / f"{code.lower()}.svg"
    if not path.is_file():
        return False
    tw, th = max(1, int(w)), max(1, int(h))
    try:
        # Load large enough for cover at target size (preserve aspect, no stretch)
        # 2× long side so we have headroom for a clean bilinear scale.
        src = GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), tw * 4, th * 4)
    except Exception:
        try:
            src = GdkPixbuf.Pixbuf.new_from_file(str(path))
        except Exception:
            return False
    if src is None or src.get_width() < 1 or src.get_height() < 1:
        return False

    sw, sh = src.get_width(), src.get_height()
    # Cover: scale so flag fills the entire canvas; excess is cropped.
    scale = max(tw / sw, th / sh)
    nw = max(tw, int(round(sw * scale)))
    nh = max(th, int(round(sh * scale)))
    scaled = src.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
    if scaled is None:
        return False

    # Center crop to canvas
    x0 = max(0, (nw - tw) // 2)
    y0 = max(0, (nh - th) // 2)
    # Clamp crop window if rounding overshoots
    if x0 + tw > nw:
        x0 = max(0, nw - tw)
    if y0 + th > nh:
        y0 = max(0, nh - th)
    try:
        cropped = scaled.new_subpixbuf(x0, y0, tw, th)
    except Exception:
        cropped = scaled.scale_simple(tw, th, GdkPixbuf.InterpType.BILINEAR)
    if cropped is None:
        return False

    Gdk.cairo_set_source_pixbuf(ctx, cropped, 0, 0)
    ctx.paint()
    return True


# ── Flag painters ─────────────────────────────────────────────────


def _flag_cn(ctx: "cairo.Context", w: float, h: float) -> None:
    # PRC: red field, one large yellow star + four small
    ctx.set_source_rgb(0.871, 0.161, 0.125)  # #DE2910
    ctx.rectangle(0, 0, w, h)
    ctx.fill()
    ctx.set_source_rgb(1.0, 0.867, 0.0)  # #FFDE00
    # Large star (canton)
    _star(ctx, 0.17 * w, 0.30 * h, 0.11 * min(w, h), 0.11 * min(w, h) * 0.40)
    ctx.fill()
    # Four small stars — arc toward the large star
    r_small = 0.035 * min(w, h)
    positions = (
        (0.34, 0.14),
        (0.41, 0.24),
        (0.41, 0.38),
        (0.34, 0.48),
    )
    # Point each small star roughly toward the large one
    lx, ly = 0.17 * w, 0.30 * h
    for px, py in positions:
        cx, cy = px * w, py * h
        ang = math.atan2(ly - cy, lx - cx)
        _star(ctx, cx, cy, r_small, r_small * 0.40, rotation=ang)
        ctx.fill()


def _flag_ru(ctx: "cairo.Context", w: float, h: float) -> None:
    third = h / 3.0
    for i, rgb in enumerate(
        (
            (1.0, 1.0, 1.0),
            (0.0, 0.22, 0.65),  # #0039A6
            (0.85, 0.12, 0.15),  # #D52B1E
        )
    ):
        ctx.set_source_rgb(*rgb)
        ctx.rectangle(0, i * third, w, third + 0.5)
        ctx.fill()


def _flag_ir(ctx: "cairo.Context", w: float, h: float) -> None:
    """Fallback Iran tricolor if flags/ir.svg is missing (no fake emblem)."""
    third = h / 3.0
    ctx.set_source_rgb(0.137, 0.624, 0.251)  # #239F40
    ctx.rectangle(0, 0, w, third + 0.5)
    ctx.fill()
    ctx.set_source_rgb(1.0, 1.0, 1.0)
    ctx.rectangle(0, third, w, third + 0.5)
    ctx.fill()
    ctx.set_source_rgb(0.855, 0.0, 0.0)  # #DA0000
    ctx.rectangle(0, 2 * third, w, third + 0.5)
    ctx.fill()


def _flag_tr(ctx: "cairo.Context", w: float, h: float) -> None:
    ctx.set_source_rgb(0.890, 0.145, 0.165)  # #E30A17
    ctx.rectangle(0, 0, w, h)
    ctx.fill()
    # Crescent: white disc minus red disc
    cx = w * 0.40
    cy = h * 0.50
    r = min(w, h) * 0.22
    ctx.set_source_rgb(1.0, 1.0, 1.0)
    ctx.arc(cx, cy, r, 0, 2 * math.pi)
    ctx.fill()
    ctx.set_source_rgb(0.890, 0.145, 0.165)
    ctx.arc(cx + r * 0.32, cy, r * 0.78, 0, 2 * math.pi)
    ctx.fill()
    # Star
    ctx.set_source_rgb(1.0, 1.0, 1.0)
    _star(ctx, cx + r * 0.72, cy, r * 0.38, r * 0.38 * 0.40, rotation=-math.pi / 2)
    ctx.fill()


def _flag_cu(ctx: "cairo.Context", w: float, h: float) -> None:
    # Five stripes: blue white blue white blue
    band = h / 5.0
    for i in range(5):
        if i % 2 == 0:
            ctx.set_source_rgb(0.0, 0.33, 0.72)  # Cuban blue
        else:
            ctx.set_source_rgb(1.0, 1.0, 1.0)
        ctx.rectangle(0, i * band, w, band + 0.5)
        ctx.fill()
    # Red triangle at hoist
    ctx.set_source_rgb(0.80, 0.06, 0.15)
    ctx.move_to(0, 0)
    ctx.line_to(w * 0.38, h * 0.5)
    ctx.line_to(0, h)
    ctx.close_path()
    ctx.fill()
    # White star
    ctx.set_source_rgb(1.0, 1.0, 1.0)
    r = min(w, h) * 0.09
    _star(ctx, w * 0.13, h * 0.5, r, r * 0.40)
    ctx.fill()


def _flag_ae(ctx: "cairo.Context", w: float, h: float) -> None:
    # Red hoist (approx 1/4), then green / white / black
    hoist = w * 0.25
    ctx.set_source_rgb(1.0, 0.0, 0.0)
    ctx.rectangle(0, 0, hoist + 0.5, h)
    ctx.fill()
    third = h / 3.0
    for i, rgb in enumerate(
        (
            (0.0, 0.45, 0.20),
            (1.0, 1.0, 1.0),
            (0.0, 0.0, 0.0),
        )
    ):
        ctx.set_source_rgb(*rgb)
        ctx.rectangle(hoist, i * third, w - hoist + 0.5, third + 0.5)
        ctx.fill()


def _star(
    ctx: "cairo.Context",
    cx: float,
    cy: float,
    r_outer: float,
    r_inner: float,
    *,
    n: int = 5,
    rotation: float = -math.pi / 2,
) -> None:
    ctx.move_to(
        cx + r_outer * math.cos(rotation),
        cy + r_outer * math.sin(rotation),
    )
    for i in range(1, n * 2):
        ang = rotation + i * math.pi / n
        r = r_outer if i % 2 == 0 else r_inner
        ctx.line_to(cx + r * math.cos(ang), cy + r * math.sin(ang))
    ctx.close_path()


def clear_cache() -> None:
    _render_cached.cache_clear()
