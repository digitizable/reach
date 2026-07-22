"""Shared motion for stack / panel switches.

GTK crossfades both children for the full duration — long fades on heavy
pages feel like lag. Prefer short, decisive transitions and honor
gtk-enable-animations / reduced motion.

See docs/motion-smoothness.md for research.
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

# Target ~1–2 frames of intent at 60fps without long double-paint.
# Main rail page changes (Home ↔ Paths ↔ …)
PAGE_MS = 120
# Loading → app shell (short; real freeze was UI build, now staged)
BOOT_MS = 160
# Nested sub-pages (settings sections, tools drill-in)
SUBPAGE_MS = 115
# In-panel swaps (empty ↔ detail, REALITY ↔ Proxy)
PANEL_MS = 100
# Short pulse for choice-card / kind rebuild soft-fade
PULSE_MS = 80
# Known-heavy children (plugin desks, big maps): near-instant
HEAVY_PAGE_MS = 70


def effective_duration_ms(duration_ms: int) -> int:
    """Clamp duration; zero when the session disables animations."""
    ms = max(0, int(duration_ms))
    try:
        settings = Gtk.Settings.get_default()
        if settings is not None and not bool(
            settings.get_property("gtk-enable-animations")
        ):
            return 0
    except Exception:
        pass
    return ms


def is_heavy_page_id(page_id: str) -> bool:
    """Pages that cost a full paint; keep their stack transition minimal."""
    if not page_id:
        return False
    if page_id.startswith("plugin:"):
        return True
    # Built-in pages with dense widgets
    return page_id in {"marketplace", "china", "paths"}


def _configure_stack(
    stack: Gtk.Stack,
    *,
    duration_ms: int,
    hhomogeneous: bool,
    vhomogeneous: bool,
    css_class: str,
) -> Gtk.Stack:
    stack.set_transition_duration(effective_duration_ms(duration_ms))
    stack.set_hhomogeneous(hhomogeneous)
    stack.set_vhomogeneous(vhomogeneous)
    stack.set_hexpand(True)
    stack.set_vexpand(True)
    try:
        stack.set_interpolate_size(True)
    except Exception:
        pass
    if css_class:
        stack.add_css_class(css_class)
    return stack


def crossfade_stack(
    *,
    duration_ms: int = PAGE_MS,
    hhomogeneous: bool = False,
    vhomogeneous: bool = True,
    css_class: str = "",
) -> Gtk.Stack:
    """Gtk.Stack with a short crossfade (default for page-like switches)."""
    stack = Gtk.Stack()
    stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
    return _configure_stack(
        stack,
        duration_ms=duration_ms,
        hhomogeneous=hhomogeneous,
        vhomogeneous=vhomogeneous,
        css_class=css_class,
    )


def slide_stack(
    *,
    duration_ms: int = SUBPAGE_MS,
    left_right: bool = True,
    hhomogeneous: bool = False,
    vhomogeneous: bool = True,
    css_class: str = "",
) -> Gtk.Stack:
    """Gtk.Stack with a horizontal (or vertical) slide for sub-page drill-in."""
    stack = Gtk.Stack()
    stack.set_transition_type(
        Gtk.StackTransitionType.SLIDE_LEFT_RIGHT
        if left_right
        else Gtk.StackTransitionType.SLIDE_UP_DOWN
    )
    return _configure_stack(
        stack,
        duration_ms=duration_ms,
        hhomogeneous=hhomogeneous,
        vhomogeneous=vhomogeneous,
        css_class=css_class,
    )


def panel_stack(*, duration_ms: int = PANEL_MS, css_class: str = "") -> Gtk.Stack:
    """Stack for swapping panels; non-homogeneous so width follows the visible child."""
    stack = crossfade_stack(
        duration_ms=duration_ms,
        hhomogeneous=False,
        vhomogeneous=False,
        css_class=css_class or "panel-stack",
    )
    stack.set_vexpand(False)
    return stack


def set_stack_child_smooth(
    stack: Gtk.Stack,
    page_id: str,
    *,
    default_ms: int = PAGE_MS,
) -> None:
    """Switch visible child with duration tuned for page weight."""
    ms = HEAVY_PAGE_MS if is_heavy_page_id(page_id) else default_ms
    try:
        stack.set_transition_duration(effective_duration_ms(ms))
    except Exception:
        pass
    stack.set_visible_child_name(page_id)


def soft_fade(
    widget: Gtk.Widget,
    *,
    from_opacity: float = 0.0,
    to_opacity: float = 1.0,
    duration_ms: int = PULSE_MS,
    on_done: Callable[[], None] | None = None,
) -> None:
    """Opacity pulse via libadwaita (e.g. after rebuilding kind fields)."""
    ms = effective_duration_ms(duration_ms)
    if ms <= 0:
        widget.set_opacity(to_opacity)
        if on_done is not None:
            on_done()
        return
    widget.set_opacity(from_opacity)
    target = Adw.CallbackAnimationTarget.new(lambda v: widget.set_opacity(v))
    anim = Adw.TimedAnimation.new(
        widget,
        float(from_opacity),
        float(to_opacity),
        max(1, ms),
        target,
    )
    try:
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
    except Exception:
        pass
    if on_done is not None:

        def _done(*_a) -> None:
            on_done()

        anim.connect("done", _done)
    anim.play()
