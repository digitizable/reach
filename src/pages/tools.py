"""Tools — lab companions (Drift, Mirage, Sounding).

Reach is the operator shell. These products stay independent; this page
explains when to use each and how they connect, without burying CLIs in Settings.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from gi.repository import Gtk

from widgets.chrome import page_header, scroll_body


@dataclass(frozen=True)
class ToolCard:
    name: str
    role: str
    body: str
    when: str
    href: str


_TOOLS: tuple[ToolCard, ...] = (
    ToolCard(
        name="Drift",
        role="Inverse Snowflake",
        body=(
            "A willing foothold dials out to an accept host you operate. "
            "Maps SOCKS for the outside operator — Snowflake inverted."
        ),
        when="Use when you cannot open inbound ports on the far side.",
        href="https://github.com/digitizable/drift",
    ),
    ToolCard(
        name="Mirage",
        role="Probe-resistant cover",
        body=(
            "REALITY face in front of reverse control so DPI and active probes "
            "see TLS-class traffic, not a custom cleartext protocol."
        ),
        when="Optional wrap for Drift’s control path on hostile networks.",
        href="https://github.com/digitizable/mirage",
    ),
    ToolCard(
        name="Sounding",
        role="Measurement lab",
        body=(
            "Measure residual CONNECT exits, invite handshakes, and cover faces. "
            "Not a tunnel client — depth sounding only."
        ),
        when="Use to test residual paths and public faces before relying on them.",
        href="https://github.com/digitizable/sounding",
    ),
)


class ToolsPage(Gtk.Box):
    def __init__(
        self,
        *,
        on_toast: Callable[[str], None] | None = None,
        on_navigate: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("page")
        self.add_css_class("tools-page")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._on_toast = on_toast
        self._on_navigate = on_navigate

        self.append(page_header("Tools"))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.add_css_class("page-body")
        body.add_css_class("tools-body")
        body.set_valign(Gtk.Align.START)
        body.set_hexpand(True)

        lede = Gtk.Label(
            label=(
                "Lab tools compose with Reach. Spectre still owns the local path; "
                "open Doors for territory ingress, or use these when you need "
                "dial-out capacity, cover, or measurement."
            ),
            wrap=True,
            xalign=0,
        )
        lede.add_css_class("muted")
        lede.add_css_class("tools-lede")
        body.append(lede)

        grid = Gtk.FlowBox()
        grid.add_css_class("tools-grid")
        grid.set_valign(Gtk.Align.START)
        grid.set_max_children_per_line(3)
        grid.set_min_children_per_line(1)
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_homogeneous(True)
        grid.set_column_spacing(12)
        grid.set_row_spacing(12)
        grid.set_hexpand(True)
        for card in _TOOLS:
            cell = Gtk.FlowBoxChild()
            cell.set_child(self._card(card))
            grid.append(cell)
        body.append(grid)

        doors = Gtk.Button(label="Open Doors…")
        doors.add_css_class("flat")
        doors.set_halign(Gtk.Align.START)
        doors.set_tooltip_text("Territory ingress — inbound host or dial-out")
        doors.connect("clicked", self._go_doors)
        body.append(doors)

        self.append(scroll_body(body, margin=16))

    def _go_doors(self, *_a) -> None:
        if self._on_navigate is not None:
            self._on_navigate("china")

    def _card(self, tool: ToolCard) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("tool-card")
        box.set_hexpand(True)
        box.set_vexpand(True)
        box.set_size_request(200, -1)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=tool.name, xalign=0)
        name.add_css_class("tool-card-name")
        name.set_hexpand(True)
        head.append(name)
        role = Gtk.Label(label=tool.role, xalign=1)
        role.add_css_class("tool-card-role")
        head.append(role)
        box.append(head)

        body = Gtk.Label(label=tool.body, wrap=True, xalign=0)
        body.add_css_class("muted")
        box.append(body)

        when = Gtk.Label(label=tool.when, wrap=True, xalign=0)
        when.add_css_class("tool-card-when")
        box.append(when)

        link = Gtk.LinkButton(uri=tool.href, label=f"{tool.name} on GitHub")
        link.set_halign(Gtk.Align.START)
        link.add_css_class("tool-card-link")
        box.append(link)
        return box

    def reload(self) -> None:
        """No-op for window.refresh_all symmetry."""
        return
