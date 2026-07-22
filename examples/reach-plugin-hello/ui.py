"""Example Reach plugin UI.

Install for local testing::

    mkdir -p ~/.local/share/reach/plugins/com__digitizable__hello
    cp -a examples/reach-plugin-hello/* \\
      ~/.local/share/reach/plugins/com__digitizable__hello/

Then restart Reach — Hello appears under Plugins in the rail.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


def create_page(ctx):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    box.add_css_class("page")
    box.set_margin_top(28)
    box.set_margin_bottom(28)
    box.set_margin_start(28)
    box.set_margin_end(28)

    title = Gtk.Label(label=ctx.manifest.name, xalign=0)
    title.add_css_class("pane-header-title")
    box.append(title)

    sub = Gtk.Label(
        label=ctx.manifest.description or "Example plugin",
        xalign=0,
        wrap=True,
    )
    sub.add_css_class("muted")
    box.append(sub)

    info = Gtk.Label(xalign=0, wrap=True)
    path_summary = "—"
    try:
        st = ctx.services.core.status(force=True)
        path_summary = getattr(st, "path_summary", None) or st.state.value
    except Exception as exc:
        path_summary = f"(status unavailable: {exc})"

    info.set_text(
        f"Plugin id: {ctx.plugin_id}\n"
        f"Version: {ctx.manifest.version}\n"
        f"Path status: {path_summary}\n"
        f"Data dir: {ctx.data_path()}"
    )
    box.append(info)

    btn = Gtk.Button(label="Toast hello")
    btn.add_css_class("suggested-action")
    btn.set_halign(Gtk.Align.START)

    def on_click(*_a):
        if ctx.toast:
            ctx.toast(f"Hello from {ctx.manifest.name}")

    btn.connect("clicked", on_click)
    box.append(btn)

    mkt = Gtk.Button(label="Open marketplace")
    mkt.add_css_class("flat")
    mkt.set_halign(Gtk.Align.START)
    mkt.connect(
        "clicked",
        lambda *_: ctx.navigate("marketplace") if ctx.navigate else None,
    )
    box.append(mkt)

    return box
