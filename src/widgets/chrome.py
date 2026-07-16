"""Shared page chrome — no scrollbars; content must fit the fixed window."""

from __future__ import annotations

from gi.repository import Gtk


def page_header(
    title: str,
    *,
    end: Gtk.Widget | None = None,
) -> Gtk.Widget:
    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    header.add_css_class("pane-header")
    header.set_hexpand(True)

    t = Gtk.Label(label=title, xalign=0)
    t.add_css_class("pane-header-title")
    t.set_hexpand(True)
    t.set_valign(Gtk.Align.CENTER)
    header.append(t)

    if end is not None:
        end.set_valign(Gtk.Align.CENTER)
        header.append(end)
    return header


def section_label(text: str) -> Gtk.Widget:
    lab = Gtk.Label(label=text, xalign=0)
    lab.add_css_class("section-label")
    return lab


def fit_body(child: Gtk.Widget, *, margin: int = 12) -> Gtk.Widget:
    """Padded content area with no ScrolledWindow."""
    wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    wrap.add_css_class("fit-body")
    wrap.set_hexpand(True)
    wrap.set_vexpand(True)
    wrap.set_margin_top(margin)
    wrap.set_margin_bottom(margin)
    wrap.set_margin_start(margin)
    wrap.set_margin_end(margin)

    child.set_hexpand(True)
    child.set_vexpand(True)
    wrap.append(child)
    return wrap


def scroll_body(child: Gtk.Widget, *, margin: int = 12) -> Gtk.Widget:
    """Scrollable content for tall pages (settings / editors)."""
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_hexpand(True)
    scroll.set_vexpand(True)
    scroll.add_css_class("fit-body")

    wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    wrap.set_margin_top(margin)
    wrap.set_margin_bottom(margin)
    wrap.set_margin_start(margin)
    wrap.set_margin_end(margin)
    wrap.set_hexpand(True)
    child.set_hexpand(True)
    wrap.append(child)
    scroll.set_child(wrap)
    return scroll


def clear_box(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt
