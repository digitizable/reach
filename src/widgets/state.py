"""Connection / health state badge — semantic color without false safety."""

from __future__ import annotations

from enum import Enum

from gi.repository import Gtk

from core.client import CoreState


class StateKind(str, Enum):
    OFFLINE = "offline"  # core missing — neutral/warning, not “secure”
    IDLE = "idle"  # core up, not connected
    BUSY = "busy"  # connecting
    LIVE = "live"  # connected — success green only here
    UNKNOWN = "unknown"  # checks not yet meaningful
    BAD = "bad"  # real failure


def kind_from_core(state: CoreState) -> StateKind:
    return {
        CoreState.UNAVAILABLE: StateKind.OFFLINE,
        CoreState.DISCONNECTED: StateKind.IDLE,
        CoreState.CONNECTING: StateKind.BUSY,
        CoreState.CONNECTED: StateKind.LIVE,
    }.get(state, StateKind.UNKNOWN)


def state_badge(kind: StateKind, label: str) -> Gtk.Widget:
    """Dot + short label. Color encodes meaning; label carries the words."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    row.add_css_class("state-badge")
    row.add_css_class(f"state-{kind.value}")

    dot = Gtk.Box()
    dot.add_css_class("state-dot")
    dot.set_valign(Gtk.Align.CENTER)
    row.append(dot)

    text = Gtk.Label(label=label, xalign=0)
    text.add_css_class("state-label")
    text.set_hexpand(True)
    row.append(text)
    return row


def apply_state_classes(widget: Gtk.Widget, kind: StateKind) -> None:
    for k in StateKind:
        widget.remove_css_class(f"state-{k.value}")
    widget.add_css_class(f"state-{kind.value}")
