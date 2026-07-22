"""Smooth scrolling for Gtk.ScrolledWindow.

Enables kinetic + overlay scrollbars everywhere, and eases discrete mouse-wheel
jumps so lists and pages glide instead of hard-stepping.
"""

from __future__ import annotations

from gi.repository import GLib, Gtk

# Per-frame blend toward the target (higher = snappier). ~0.32 settles faster
# without discrete hard jumps.
_EASE = 0.32
# Discrete wheel: fraction of page height per click (capped in pixels).
_WHEEL_PAGE_FRAC = 0.14
_WHEEL_PX_MIN = 36.0
_WHEEL_PX_MAX = 100.0
# Trackpad / continuous: pixels per unit of dy.
_TRACKPAD_PX = 40.0


def configure_scrolled(scroll: Gtk.ScrolledWindow) -> Gtk.ScrolledWindow:
    """Turn on kinetic/overlay scrolling and smooth wheel animation."""
    if getattr(scroll, "_reach_smooth_scroll", False):
        return scroll
    scroll._reach_smooth_scroll = True  # type: ignore[attr-defined]

    scroll.set_kinetic_scrolling(True)
    try:
        scroll.set_overlay_scrolling(True)
    except Exception:
        pass
    scroll.add_css_class("smooth-scroll")

    # Tune step sizes once the adjustment exists (after child is set / mapped).
    scroll.connect("map", _on_map_tune)
    scroll.connect("notify::vadjustment", lambda *_: _tune_adjustment(scroll.get_vadjustment()))
    scroll.connect("notify::hadjustment", lambda *_: _tune_adjustment(scroll.get_hadjustment()))
    _tune_adjustment(scroll.get_vadjustment())
    _tune_adjustment(scroll.get_hadjustment())

    _attach_smooth_wheel(scroll)
    return scroll


def scrolled_window(
    *,
    h_policy: Gtk.PolicyType = Gtk.PolicyType.NEVER,
    v_policy: Gtk.PolicyType = Gtk.PolicyType.AUTOMATIC,
    hexpand: bool = True,
    vexpand: bool = True,
    css_class: str = "",
) -> Gtk.ScrolledWindow:
    """Factory for a smooth-scrolling ScrolledWindow."""
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(h_policy, v_policy)
    scroll.set_hexpand(hexpand)
    scroll.set_vexpand(vexpand)
    if css_class:
        scroll.add_css_class(css_class)
    return configure_scrolled(scroll)


def _on_map_tune(scroll: Gtk.ScrolledWindow, *_a) -> None:
    _tune_adjustment(scroll.get_vadjustment())
    _tune_adjustment(scroll.get_hadjustment())


def _tune_adjustment(adj: Gtk.Adjustment | None) -> None:
    if adj is None:
        return
    page = adj.get_page_size() or 0.0
    # Smaller steps than GTK defaults so wheel + arrows feel less jumpy.
    step = max(24.0, min(64.0, page * 0.08 if page > 0 else 32.0))
    page_inc = max(step * 2, page * 0.85 if page > 0 else 120.0)
    if abs(adj.get_step_increment() - step) > 0.5:
        adj.set_step_increment(step)
    if abs(adj.get_page_increment() - page_inc) > 0.5:
        adj.set_page_increment(page_inc)


def _attach_smooth_wheel(scroll: Gtk.ScrolledWindow) -> None:
    """Ease discrete mouse-wheel steps; pass continuous trackpad motion through gently."""
    state: dict = {
        "v_target": None,
        "h_target": None,
        "tick_id": 0,
    }

    def _clamp(adj: Gtk.Adjustment, value: float) -> float:
        upper = max(adj.get_lower(), adj.get_upper() - adj.get_page_size())
        return max(adj.get_lower(), min(upper, value))

    def _wheel_delta(adj: Gtk.Adjustment, amount: float) -> float:
        """Map controller dy/dx to pixels."""
        page = adj.get_page_size() or 200.0
        # Discrete notches are typically ±1.0 (or small integers).
        if abs(amount) >= 0.85 and abs(amount - round(amount)) < 0.08:
            step = max(_WHEEL_PX_MIN, min(_WHEEL_PX_MAX, page * _WHEEL_PAGE_FRAC))
            return step * amount
        # Fractional trackpad: scale and accumulate toward target for mild ease.
        return amount * _TRACKPAD_PX

    def _ensure_tick() -> None:
        if state["tick_id"]:
            return

        def tick(_widget, _clock) -> bool:
            moved = False
            for key, getter, setter_name in (
                ("v_target", scroll.get_vadjustment, "v"),
                ("h_target", scroll.get_hadjustment, "h"),
            ):
                target = state[key]
                if target is None:
                    continue
                adj = getter()
                if adj is None:
                    state[key] = None
                    continue
                cur = adj.get_value()
                diff = target - cur
                if abs(diff) < 0.6:
                    adj.set_value(target)
                    state[key] = None
                    continue
                # Frame-rate independent ease (assume ~16ms frames; scale if slower)
                nxt = cur + diff * _EASE
                if abs(target - nxt) < 0.6 or (diff > 0 and nxt > target) or (
                    diff < 0 and nxt < target
                ):
                    nxt = target
                    state[key] = None
                adj.set_value(nxt)
                moved = True
            if not moved and state["v_target"] is None and state["h_target"] is None:
                state["tick_id"] = 0
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        state["tick_id"] = scroll.add_tick_callback(tick)

    def on_scroll(_ctrl, dx: float, dy: float) -> bool:
        handled = False

        if dy != 0.0:
            vadj = scroll.get_vadjustment()
            if vadj is not None and (vadj.get_upper() - vadj.get_lower()) > vadj.get_page_size() + 1:
                delta = _wheel_delta(vadj, dy)
                base = (
                    state["v_target"]
                    if state["v_target"] is not None
                    else vadj.get_value()
                )
                state["v_target"] = _clamp(vadj, base + delta)
                handled = True

        if dx != 0.0:
            hadj = scroll.get_hadjustment()
            if hadj is not None and (hadj.get_upper() - hadj.get_lower()) > hadj.get_page_size() + 1:
                delta = _wheel_delta(hadj, dx)
                base = (
                    state["h_target"]
                    if state["h_target"] is not None
                    else hadj.get_value()
                )
                state["h_target"] = _clamp(hadj, base + delta)
                handled = True

        if handled:
            _ensure_tick()
            return True
        return False

    ctrl = Gtk.EventControllerScroll.new(
        Gtk.EventControllerScrollFlags.BOTH_AXES
        | Gtk.EventControllerScrollFlags.KINETIC
    )
    # Capture so we ease before GTK's default discrete jump.
    ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    ctrl.connect("scroll", on_scroll)
    scroll.add_controller(ctrl)
