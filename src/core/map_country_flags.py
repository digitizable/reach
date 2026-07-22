"""Country rings + flag textures for the Home Mullvad map.

- ``world-countries.json``: simplified ISO2 land rings (lat, lon)
- Flags: local SVG under data/assets/flags/ when present, else cached PNG
  from flagcdn.com (Wikimedia-based CDN used by Flagpedia).

High-resolution textures are cached once per country at their **native aspect
ratio** (no forced 3:2 crop). Map paint scales with Cairo using object-fit
**cover** (fills the country silhouette without stretching).
"""

from __future__ import annotations

import urllib.request
from functools import lru_cache
from pathlib import Path

from app_config import project_root, user_data_dir

try:
    import cairo
except ImportError:  # pragma: no cover
    cairo = None  # type: ignore

try:
    from gi.repository import Gdk, GdkPixbuf
except Exception:  # pragma: no cover
    Gdk = None  # type: ignore
    GdkPixbuf = None  # type: ignore

# Master texture width from flagcdn (height follows real flag aspect).
# 640px stays sharp when scaled into large country silhouettes.
_FLAG_CDN_W = 640
# Allow up to this long-edge when rasterizing bundled SVGs / surfaces.
_FLAG_MAX_EDGE = 640


def load_country_rings() -> dict[str, list[list[tuple[float, float]]]]:
    """ISO2 → list of closed rings as (lat, lon) tuples.

    Delegates to ``core.map_geo`` (Natural Earth landmass pipeline).
    """
    from core.map_geo import load_country_rings as _load

    return _load()


def _flag_cache_dir() -> Path:
    d = user_data_dir() / "cache" / "flags"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _local_flag_svg(code: str) -> Path | None:
    p = project_root() / "data" / "assets" / "flags" / f"{code.lower()}.svg"
    return p if p.is_file() else None


def ensure_flag_png(code: str, *, width: int = _FLAG_CDN_W) -> Path | None:
    """Return path to a local PNG for *code*, downloading once if needed."""
    code = (code or "").lower()
    if len(code) != 2 or not code.isalpha():
        return None
    width = max(80, min(1280, int(width)))
    # Prefer larger cache file if a smaller one already exists only as fallback
    cache = _flag_cache_dir() / f"{code}_w{width}.png"
    if cache.is_file() and cache.stat().st_size > 80:
        return cache
    # Prefer bundled SVG → rasterize via GdkPixbuf when possible
    svg = _local_flag_svg(code)
    if svg is not None and GdkPixbuf is not None:
        try:
            # Width-driven; height follows SVG aspect (no stretch)
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(str(svg), width, 4096)
            if pb is not None:
                pb.savev(str(cache), "png", [], [])
                if cache.is_file():
                    return cache
        except Exception:
            pass
    # CDN (Flagpedia / flagcdn — free Wikimedia-based flags)
    url = f"https://flagcdn.com/w{width}/{code}.png"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Reach/0.4 (Mullvad map flags; offline cache)"},
        )
        with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310
            data = resp.read()
        if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff":
            cache.write_bytes(data)
            return cache
    except Exception:
        return None
    return None


def _pixbuf_to_surface(pb) -> "cairo.ImageSurface | None":
    """Copy a GdkPixbuf into an ARGB32 ImageSurface at native pixel size."""
    if cairo is None or Gdk is None or pb is None:
        return None
    w, h = int(pb.get_width()), int(pb.get_height())
    if w < 4 or h < 4:
        return None
    # Cap extreme sizes
    if max(w, h) > _FLAG_MAX_EDGE:
        scale = _FLAG_MAX_EDGE / float(max(w, h))
        nw, nh = max(4, int(w * scale)), max(4, int(h * scale))
        try:
            pb = pb.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
        except Exception:
            return None
        if pb is None:
            return None
        w, h = nw, nh
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    ctx = cairo.Context(surf)
    try:
        Gdk.cairo_set_source_pixbuf(ctx, pb, 0, 0)
        ctx.paint()
    except Exception:
        return None
    surf.flush()
    return surf


@lru_cache(maxsize=128)
def flag_surface(code: str):
    """High-res Cairo ImageSurface for *code* at native flag aspect ratio.

    Map paint scales this with object-fit **cover** into each country clip —
    never stretch, never re-decode per camera pose.
    """
    if cairo is None:
        return None
    code_u = (code or "").upper()
    if len(code_u) != 2:
        return None

    png = ensure_flag_png(code_u.lower(), width=_FLAG_CDN_W)
    if png is not None and GdkPixbuf is not None:
        try:
            # Load at file pixels (flagcdn w640 already sized); no forced crop
            pb = GdkPixbuf.Pixbuf.new_from_file(str(png))
            surf = _pixbuf_to_surface(pb)
            if surf is not None:
                return surf
        except Exception:
            pass
        # Fallback: size-limited load
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(
                str(png), _FLAG_CDN_W, _FLAG_CDN_W
            )
            surf = _pixbuf_to_surface(pb)
            if surf is not None:
                return surf
        except Exception:
            pass

    # Cairo painters / generic solid (~3:2 placeholder)
    tw, th = _FLAG_CDN_W, int(_FLAG_CDN_W * 2 / 3)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, tw, th)
    ctx = cairo.Context(surf)
    try:
        from core.territory_flags import paint_flag

        paint_flag(ctx, code_u, float(tw), float(th))
    except Exception:
        ctx.set_source_rgb(0.22, 0.35, 0.55)
        ctx.rectangle(0, 0, tw, th)
        ctx.fill()
    surf.flush()
    return surf


def warm_flag_surfaces(codes: list[str] | set[str], *, limit: int = 60) -> int:
    """Decode flag textures for *codes* (call off UI thread). Returns count."""
    n = 0
    for cc in sorted({(c or "").lower() for c in codes if c})[:limit]:
        if len(cc) != 2:
            continue
        if flag_surface(cc) is not None:
            n += 1
    return n


def clear_flag_surface_cache() -> None:
    flag_surface.cache_clear()
