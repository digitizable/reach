"""Badged icons + .desktop entries for excluded (clearnet) app instances.

Taskbars (Cinnamon/GNOME/KDE on X11) map windows to a .desktop file via
StartupWMClass. We:

  1. Give each excluded instance a unique WM class (``SpectreExclude-…``)
  2. Composite a Tabler “world” SVG badge (blue wireframe) on the app icon
  3. Install a user .desktop file with that class + badged icon

Globe art: Tabler Icons ``world`` (MIT) — https://tabler.io/icons/icon/world
Menu/dock launches of the real app are unchanged (different WM class).
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
from pathlib import Path

from core.apps import RoutedApp

_BADGE_SIZE_FRAC = 0.48  # badge diameter relative to icon
_CACHE_SIZE = 128  # px
# Bump when badge art changes so cached PNGs regenerate.
_BADGE_ART = "tabler-world-v1"


def exclude_wm_class(app: RoutedApp) -> str:
    """Stable X11/WM class for an excluded instance of *app*."""
    raw = (app.desktop_id or app.id or app.name or "app").strip()
    raw = raw.removesuffix(".desktop")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", raw).strip("-")
    if not slug:
        slug = "app"
    # Keep it short — some WMs truncate class names.
    slug = slug[:40]
    # Must start with a letter for some parsers.
    if slug[0].isdigit():
        slug = "a" + slug
    return f"SpectreExclude-{slug}"


def _icons_dir() -> Path:
    from app_config import user_data_dir

    d = user_data_dir() / "icons" / "exclude"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _applications_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME", "").strip()
    root = Path(base) if base else Path.home() / ".local" / "share"
    d = root / "applications"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _app_file_key(app: RoutedApp) -> str:
    raw = (app.id or app.desktop_id or app.name or "app").strip()
    raw = raw.removesuffix(".desktop")
    key = re.sub(r"[^\w.\-]+", "-", raw)[:80].strip("-") or "app"
    return key


def _icon_cache_path(app: RoutedApp) -> Path:
    return _icons_dir() / f"{_app_file_key(app)}-{_BADGE_ART}.png"


def _desktop_path(app: RoutedApp) -> Path:
    return _applications_dir() / f"spectre-exclude-{_app_file_key(app)}.desktop"


def _load_base_pixbuf(icon_name: str, size: int = _CACHE_SIZE):
    """Load an app icon as GdkPixbuf, or None."""
    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
    except Exception:
        return None

    name = (icon_name or "").strip() or "application-x-executable"
    # Absolute file path
    if name.startswith("/") and Path(name).is_file():
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_size(name, size, size)
        except Exception:
            pass

    # Theme icon via GTK4 IconTheme → file path
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, Gtk

        display = Gdk.Display.get_default()
        if display is not None:
            theme = Gtk.IconTheme.get_for_display(display)
            paintable = theme.lookup_icon(
                name,
                None,
                size,
                1,
                Gtk.TextDirection.NONE,
                0,
            )
            if paintable is not None:
                gfile = paintable.get_file()
                if gfile is not None:
                    path = gfile.get_path()
                    if path and Path(path).is_file():
                        return GdkPixbuf.Pixbuf.new_from_file_at_size(path, size, size)
    except Exception:
        pass

    try:
        return GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
    except Exception:
        return None


def _globe_svg_path() -> Path | None:
    """Packaged Tabler world SVG (data/assets/exclude-globe.svg)."""
    try:
        from app_config import project_root, src_dir
    except Exception:
        project_root = None  # type: ignore[assignment]
        src_dir = None  # type: ignore[assignment]

    candidates: list[Path] = []
    if project_root is not None:
        candidates.append(project_root() / "data" / "assets" / "exclude-globe.svg")
    if src_dir is not None:
        candidates.append(src_dir().parent / "data" / "assets" / "exclude-globe.svg")
    # Dev / install fallbacks
    here = Path(__file__).resolve()
    candidates.extend(
        [
            here.parents[2] / "data" / "assets" / "exclude-globe.svg",
            Path.home() / "Downloads" / "spectre-globe-tabler.svg",
        ]
    )
    for p in candidates:
        if p.is_file():
            return p
    return None


def _render_globe_png(pixel_size: int) -> Path | None:
    """Rasterize the Tabler world SVG to a cached PNG (rsvg-convert or GdkPixbuf)."""
    svg = _globe_svg_path()
    if svg is None:
        return None
    out = _icons_dir() / f"tabler-world-{pixel_size}.png"
    if out.is_file() and out.stat().st_size > 32:
        return out

    rsvg = shutil.which("rsvg-convert")
    if rsvg:
        try:
            r = subprocess.run(
                [rsvg, "-w", str(pixel_size), "-h", str(pixel_size), "-o", str(out), str(svg)],
                capture_output=True,
                timeout=10,
                check=False,
            )
            if r.returncode == 0 and out.is_file():
                return out
        except (OSError, subprocess.TimeoutExpired):
            pass

    # GdkPixbuf with librsvg loader (if installed)
    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        pb = GdkPixbuf.Pixbuf.new_from_file_at_size(str(svg), pixel_size, pixel_size)
        if pb is not None:
            pb.savev(str(out), "png", [], [])
            return out
    except Exception:
        pass
    return None


def _pixbuf_to_cairo_surface(pixbuf):
    """Convert GdkPixbuf (RGBA) → cairo ImageSurface (ARGB32)."""
    import sys

    import cairo

    if not pixbuf.get_has_alpha():
        pixbuf = pixbuf.add_alpha(False, 0, 0, 0)
    w, h = pixbuf.get_width(), pixbuf.get_height()
    pixels = pixbuf.get_pixels()
    stride = pixbuf.get_rowstride()
    buf = bytearray(w * h * 4)
    le = sys.byteorder == "little"
    for y in range(h):
        row = y * stride
        for x in range(w):
            i = row + x * 4
            r, g, b, a = pixels[i], pixels[i + 1], pixels[i + 2], pixels[i + 3]
            o = (y * w + x) * 4
            if le:
                buf[o] = b
                buf[o + 1] = g
                buf[o + 2] = r
                buf[o + 3] = a
            else:
                buf[o] = a
                buf[o + 1] = r
                buf[o + 2] = g
                buf[o + 3] = b
    return cairo.ImageSurface.create_for_data(
        memoryview(buf), cairo.FORMAT_ARGB32, w, h, w * 4
    )


def _cairo_surface_to_pixbuf(surface):
    """cairo ImageSurface (ARGB32) → GdkPixbuf RGBA."""
    import sys

    from gi.repository import GdkPixbuf

    w, h = surface.get_width(), surface.get_height()
    surface.flush()
    data = surface.get_data()
    out = bytearray(w * h * 4)
    le = sys.byteorder == "little"
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 4
            if le:
                b, g, r, a = data[i], data[i + 1], data[i + 2], data[i + 3]
            else:
                a, r, g, b = data[i], data[i + 1], data[i + 2], data[i + 3]
            out[i] = r
            out[i + 1] = g
            out[i + 2] = b
            out[i + 3] = a
    return GdkPixbuf.Pixbuf.new_from_data(
        bytes(out),
        GdkPixbuf.Colorspace.RGB,
        True,
        8,
        w,
        h,
        w * 4,
    )


def _draw_exclude_badge(pixbuf) -> object | None:
    """Composite Tabler blue wireframe world badge on the bottom-right."""
    try:
        import cairo
        from gi.repository import GdkPixbuf
    except Exception:
        return None

    w, h = pixbuf.get_width(), pixbuf.get_height()
    if w < 16 or h < 16:
        return pixbuf

    try:
        surface = _pixbuf_to_cairo_surface(pixbuf)
        cr = cairo.Context(surface)
    except Exception:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surface)
        cr.set_source_rgb(0.12, 0.12, 0.12)
        cr.rectangle(0, 0, w, h)
        cr.fill()

    d = max(14, int(min(w, h) * _BADGE_SIZE_FRAC))
    cx = w - d * 0.40
    cy = h - d * 0.40
    radius = d * 0.46

    # Soft shadow + dark plate
    cr.set_source_rgba(0, 0, 0, 0.42)
    cr.arc(cx + 0.9, cy + 0.9, radius * 1.06, 0, 2 * math.pi)
    cr.fill()
    cr.set_source_rgba(0.05, 0.08, 0.14, 0.94)
    cr.arc(cx, cy, radius, 0, 2 * math.pi)
    cr.fill()

    # Tabler world PNG centered on the plate
    globe_px = max(12, int(radius * 1.55))
    globe_path = _render_globe_png(globe_px)
    if globe_path is not None:
        try:
            globe_pb = GdkPixbuf.Pixbuf.new_from_file_at_size(
                str(globe_path), globe_px, globe_px
            )
            gw, gh = globe_pb.get_width(), globe_pb.get_height()
            gx = int(cx - gw / 2)
            gy = int(cy - gh / 2)
            gsurf = _pixbuf_to_cairo_surface(globe_pb)
            cr.set_source_surface(gsurf, gx, gy)
            cr.paint()
        except Exception:
            globe_path = None

    if globe_path is None:
        # Minimal fallback if SVG rasterization is unavailable
        cr.set_source_rgb(0.30, 0.62, 0.98)
        cr.set_line_width(max(1.2, radius * 0.12))
        cr.arc(cx, cy, radius * 0.72, 0, 2 * math.pi)
        cr.stroke()
        cr.move_to(cx - radius * 0.72, cy)
        cr.line_to(cx + radius * 0.72, cy)
        cr.stroke()
        cr.save()
        cr.translate(cx, cy)
        cr.scale(0.45, 1.0)
        cr.arc(0, 0, radius * 0.72, 0, 2 * math.pi)
        cr.restore()
        cr.stroke()

    surface.flush()
    try:
        return _cairo_surface_to_pixbuf(surface)
    except Exception:
        try:
            tmp = _icons_dir() / ".badge-tmp.png"
            surface.write_to_png(str(tmp))
            return GdkPixbuf.Pixbuf.new_from_file(str(tmp))
        except Exception:
            return pixbuf


def ensure_badged_icon(app: RoutedApp) -> Path | None:
    """Return path to a PNG with exclude badge, or None on failure."""
    dest = _icon_cache_path(app)
    # Rebuild if missing (icons rarely change; keep simple)
    if dest.is_file() and dest.stat().st_size > 64:
        return dest

    pixbuf = _load_base_pixbuf(app.icon_name or "application-x-executable")
    if pixbuf is None:
        # Last resort: blank + badge only
        try:
            from gi.repository import GdkPixbuf

            pixbuf = GdkPixbuf.Pixbuf.new(
                GdkPixbuf.Colorspace.RGB, True, 8, _CACHE_SIZE, _CACHE_SIZE
            )
            pixbuf.fill(0x202020FF)
        except Exception:
            return None

    badged = _draw_exclude_badge(pixbuf)
    if badged is None:
        return None
    try:
        badged.savev(str(dest), "png", [], [])
        return dest
    except Exception:
        try:
            badged.save(str(dest), "png")
            return dest
        except Exception:
            return None


def ensure_exclude_desktop_entry(
    app: RoutedApp,
    *,
    wm_class: str,
    exec_argv: list[str] | None = None,
) -> Path | None:
    """Install a user .desktop so the taskbar shows name + badged icon.

    Never changes the real app's .desktop. Only writes spectre-exclude-*.desktop.
    """
    icon_path = ensure_badged_icon(app)
    icon_field = str(icon_path) if icon_path else (app.icon_name or "application-x-executable")

    # Exec is informational / for "pin"; real launch goes through Spectre.
    if exec_argv:
        # Escape for desktop files
        parts = []
        for a in exec_argv:
            if re.search(r'[\s"\\]', a):
                parts.append('"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"')
            else:
                parts.append(a)
        exec_line = " ".join(parts)
    else:
        exec_line = app.command

    name = f"{app.name} (clearnet)"
    comment = "Spectre excluded instance — clearnet / outside the path"
    path = _desktop_path(app)

    body = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={name}\n"
        f"Comment={comment}\n"
        f"Exec={exec_line}\n"
        f"Icon={icon_field}\n"
        "Terminal=False\n"
        "Categories=Network;\n"
        "NoDisplay=true\n"
        f"StartupWMClass={wm_class}\n"
        "StartupNotify=true\n"
        f"X-Spectre-Exclude=1\n"
        f"X-Spectre-App-Id={app.id}\n"
    )
    try:
        path.write_text(body, encoding="utf-8")
    except OSError:
        return None

    # Refresh desktop database so taskbars pick up StartupWMClass (best-effort)
    try:
        subprocess.run(
            ["update-desktop-database", str(_applications_dir())],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass

    return path


def prepare_exclude_window_identity(
    app: RoutedApp,
    argv: list[str],
) -> tuple[list[str], str]:
    """Apply unique WM class to *argv* and install badged .desktop entry.

    Returns (argv_with_class, note).
    """
    wm_class = exclude_wm_class(app)
    out = list(argv)

    # Inject / replace --class for toolkits that honor it (Firefox, Chromium, …)
    # Remove existing --class / --class= values first.
    cleaned: list[str] = [out[0]]
    skip_next = False
    for i, a in enumerate(out[1:], start=1):
        if skip_next:
            skip_next = False
            continue
        if a in ("--class", "-class"):
            skip_next = True
            continue
        if a.startswith("--class=") or a.startswith("-class="):
            continue
        cleaned.append(a)
    out = cleaned

    exe = Path(out[0]).name.lower() if out else ""
    # Chromium-style --class=value
    chromiumish = exe in {
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
        "google-chrome-beta",
        "brave-browser",
        "brave",
        "microsoft-edge",
        "microsoft-edge-stable",
        "vivaldi",
        "vivaldi-stable",
        "opera",
        "code",
        "code-oss",
        "codium",
        "cursor",
        "slack",
        "discord",
        "element-desktop",
        "signal-desktop",
        "spotify",
        "obsidian",
    }
    if chromiumish:
        out = [out[0], f"--class={wm_class}", *out[1:]]
    else:
        # Firefox / GTK style: --class Name
        out = [out[0], "--class", wm_class, *out[1:]]

    desktop = ensure_exclude_desktop_entry(app, wm_class=wm_class, exec_argv=out)
    if desktop is not None:
        note = f"taskbar badge · {wm_class}"
    else:
        note = f"wm class {wm_class}"
    return out, note
