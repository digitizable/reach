"""Theme helpers — dark-only CSS."""

from __future__ import annotations

import sys

from gi.repository import Adw, Gdk, GLib, Gtk

from app_config import src_dir

_css_provider: Gtk.CssProvider | None = None


def apply_theme(_color_scheme: str | None = None) -> None:
    """Force dark chrome and load the dark stylesheet."""
    sm = Adw.StyleManager.get_default()
    sm.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    _load_css()


def _load_css() -> None:
    global _css_provider
    path = src_dir() / "style-dark.css"
    if not path.is_file():
        path = src_dir() / "style.css"

    provider = Gtk.CssProvider()
    try:
        provider.load_from_path(str(path))
    except GLib.Error as exc:  # type: ignore[attr-defined]
        print(f"[reach] CSS load failed ({path}): {exc}", file=sys.stderr)
        return

    display = Gdk.Display.get_default()
    if display is None:
        return

    if _css_provider is not None:
        Gtk.StyleContext.remove_provider_for_display(display, _css_provider)

    Gtk.StyleContext.add_provider_for_display(
        display,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _css_provider = provider
