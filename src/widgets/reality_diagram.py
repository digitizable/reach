"""Diagram-first REALITY parameter editor.

Illustrates the hop instead of a flat list of fields:

  You  →  REALITY landing (host · identity)  →  Cover site (SNI)

Default layout is horizontal. If the shell is narrow, the hop row scrolls
sideways so the window size is not forced wider.
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gtk


def _node_shell(
    *,
    title: str,
    icon: str,
    accent: str = "",
    compact: bool = False,
) -> tuple[Gtk.Box, Gtk.Box]:
    """Card chrome: returns (outer card, body column for fields).

    compact=True → short side node (You); does not stretch with the hop card.
    """
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6 if compact else 8)
    card.add_css_class("reality-node")
    if accent:
        card.add_css_class(accent)
    if compact:
        card.add_css_class("reality-node-compact")
    # Never fill the row height — each card is only as tall as its fields
    card.set_hexpand(False)
    card.set_vexpand(False)
    card.set_valign(Gtk.Align.CENTER)
    card.set_halign(Gtk.Align.START)

    head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    head.set_halign(Gtk.Align.CENTER if compact else Gtk.Align.START)
    ic = Gtk.Image.new_from_icon_name(icon)
    ic.set_pixel_size(16 if compact else 18)
    ic.add_css_class("reality-node-icon")
    head.append(ic)
    lab = Gtk.Label(label=title, xalign=0.5 if compact else 0)
    lab.add_css_class("reality-node-title")
    head.append(lab)
    card.append(head)

    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4 if compact else 6)
    body.add_css_class("reality-node-body")
    if compact:
        body.set_halign(Gtk.Align.CENTER)
    card.append(body)
    return card, body


def _field(label: str, widget: Gtk.Widget, *, hint: str = "") -> Gtk.Widget:
    wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    lab = Gtk.Label(label=label, xalign=0)
    lab.add_css_class("reality-field-label")
    wrap.append(lab)
    wrap.append(widget)
    if hint:
        h = Gtk.Label(label=hint, xalign=0, wrap=True)
        h.add_css_class("muted")
        h.add_css_class("reality-field-hint")
        wrap.append(h)
    return wrap


def _arrow() -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.set_halign(Gtk.Align.CENTER)
    box.set_valign(Gtk.Align.CENTER)
    box.add_css_class("reality-arrow-slot")
    lab = Gtk.Label(label="→")
    lab.add_css_class("reality-arrow")
    lab.set_halign(Gtk.Align.CENTER)
    lab.set_valign(Gtk.Align.CENTER)
    box.append(lab)
    cap = Gtk.Label(label="TLS", xalign=0.5)
    cap.add_css_class("reality-arrow-cap")
    box.append(cap)
    return box


class RealityDiagramEditor(Gtk.Box):
    """Editable REALITY hop diagram with share-link import (horizontal)."""

    def __init__(
        self,
        *,
        on_changed: Callable[[], None] | None = None,
        show_import: bool = True,
        show_advanced: bool = True,
        layout: str = "row",
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("reality-diagram")
        self.add_css_class("reality-diagram-row-layout")
        self.set_hexpand(True)
        self.set_vexpand(False)
        self._on_changed = on_changed
        # layout kept for API compat; always horizontal
        self._layout = "row"

        if show_import:
            imp = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            imp.add_css_class("reality-import-row")
            self.vless_entry = Gtk.Entry()
            self.vless_entry.set_placeholder_text("Paste vless://…reality share link")
            self.vless_entry.set_hexpand(True)
            imp.append(self.vless_entry)
            self.import_btn = Gtk.Button(label="Import")
            self.import_btn.add_css_class("suggested-action")
            imp.append(self.import_btn)
            self.append(imp)
            lede = Gtk.Label(
                label="You → REALITY hop → cover site — each field sits on its node.",
                xalign=0,
                wrap=True,
            )
            lede.add_css_class("muted")
            lede.add_css_class("reality-diagram-lede")
            self.append(lede)
        else:
            self.vless_entry = Gtk.Entry()
            self.import_btn = Gtk.Button(label="Import")
            self.vless_entry.set_visible(False)
            self.import_btn.set_visible(False)

        # ── Horizontal hop: You → REALITY → Cover ───────────────────
        # Side cards stay natural-height (not stretched to the hop card).
        you, you_body = _node_shell(
            title="You",
            icon="computer-symbolic",
            accent="reality-node-you",
            compact=True,
        )
        you_cap = Gtk.Label(
            label="Outside vantage",
            justify=Gtk.Justification.CENTER,
        )
        you_cap.add_css_class("reality-node-static")
        you_cap.set_xalign(0.5)
        you_cap.set_wrap(True)
        you_cap.set_max_width_chars(12)
        you_body.append(you_cap)
        you.set_size_request(96, -1)

        land, land_body = _node_shell(
            title="REALITY hop",
            icon="security-high-symbolic",
            accent="reality-node-hop",
        )
        land.set_size_request(220, -1)

        self.server = Gtk.Entry()
        self.server.set_placeholder_text("host or IP")
        self.server.set_hexpand(True)
        self.server.connect("changed", self._fire)
        land_body.append(_field("Server", self.server))

        port_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.port = Gtk.SpinButton.new_with_range(1, 65535, 1)
        self.port.set_value(443)
        self.port.set_hexpand(True)
        self.port.connect("value-changed", self._fire)
        port_row.append(self.port)
        land_body.append(_field("Port", port_row))

        self.uuid = Gtk.Entry()
        self.uuid.set_placeholder_text("VLESS UUID")
        self.uuid.set_hexpand(True)
        self.uuid.connect("changed", self._fire)
        land_body.append(_field("UUID", self.uuid))

        self.public_key = Gtk.Entry()
        self.public_key.set_placeholder_text("pbk")
        self.public_key.set_hexpand(True)
        self.public_key.connect("changed", self._fire)
        land_body.append(_field("Public key", self.public_key))

        self.short_id = Gtk.Entry()
        self.short_id.set_placeholder_text("optional")
        self.short_id.set_hexpand(True)
        self.short_id.connect("changed", self._fire)
        land_body.append(_field("Short ID", self.short_id))

        cover, cover_body = _node_shell(
            title="Cover site",
            icon="web-browser-symbolic",
            accent="reality-node-cover",
        )
        cover.set_size_request(200, -1)

        self.sni = Gtk.Entry()
        self.sni.set_placeholder_text("www.example.com")
        self.sni.set_hexpand(True)
        self.sni.connect("changed", self._fire)
        cover_body.append(
            _field(
                "SNI / serverName",
                self.sni,
                hint="Pretend destination",
            )
        )

        self.fingerprint = Gtk.Entry()
        self.fingerprint.set_placeholder_text("chrome")
        self.fingerprint.set_text("chrome")
        self.fingerprint.set_hexpand(True)
        self.fingerprint.connect("changed", self._fire)
        cover_body.append(_field("uTLS fingerprint", self.fingerprint))

        cover_note = Gtk.Label(
            label="Looks like normal HTTPS",
            justify=Gtk.Justification.CENTER,
            wrap=True,
        )
        cover_note.add_css_class("reality-node-static")
        cover_note.set_xalign(0.5)
        cover_note.set_max_width_chars(22)
        cover_body.append(cover_note)

        from widgets.scroll import scrolled_window

        scroll = scrolled_window(
            h_policy=Gtk.PolicyType.AUTOMATIC,
            v_policy=Gtk.PolicyType.NEVER,
            vexpand=False,
            css_class="reality-diagram-scroll",
        )
        # Do not push either axis into the shell — diagram scrolls if needed.
        scroll.set_propagate_natural_height(False)
        scroll.set_propagate_natural_width(False)
        scroll.set_min_content_height(120)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("reality-diagram-row")
        row.set_halign(Gtk.Align.START)
        # START + per-child CENTER: short cards don't stretch to hop height
        row.set_valign(Gtk.Align.START)
        row.set_vexpand(False)

        arrow1 = _arrow()
        arrow2 = _arrow()
        for w in (you, arrow1, land, arrow2, cover):
            w.set_valign(Gtk.Align.CENTER)
            w.set_vexpand(False)
            row.append(w)

        scroll.set_child(row)
        self.append(scroll)

        # Advanced (flow / spider)
        if show_advanced:
            adv = Gtk.Expander(label="Advanced (flow · spiderX)")
            adv.add_css_class("reality-advanced")
            adv_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            adv_box.set_margin_top(8)
            self.flow = Gtk.Entry()
            self.flow.set_text("xtls-rprx-vision")
            self.flow.set_hexpand(True)
            self.flow.connect("changed", self._fire)
            adv_box.append(_field("Flow", self.flow))
            self.spider_x = Gtk.Entry()
            self.spider_x.set_placeholder_text("optional path")
            self.spider_x.set_hexpand(True)
            self.spider_x.connect("changed", self._fire)
            adv_box.append(_field("SpiderX", self.spider_x))
            adv.set_child(adv_box)
            self.append(adv)
        else:
            self.flow = Gtk.Entry()
            self.flow.set_text("xtls-rprx-vision")
            self.spider_x = Gtk.Entry()

    def _fire(self, *_a) -> None:
        if self._on_changed is not None:
            self._on_changed()

    # ── Summary for compact cards / lists ─────────────────────────────

    def is_configured(self) -> bool:
        return bool(self.get_server() and self.get_uuid() and self.get_public_key())

    def summary_line(self) -> str:
        """One-line status for a summary card (e.g. host:port · SNI)."""
        host = self.get_server()
        if not host:
            return "Not configured — open to fill the hop"
        port = self.get_port()
        sni = self.get_sni()
        bits = [f"{host}:{port}"]
        if sni:
            bits.append(f"SNI {sni}")
        if not self.get_uuid() or not self.get_public_key():
            bits.append("incomplete")
        return " · ".join(bits)

    # ── Accessors ─────────────────────────────────────────────────────

    def get_server(self) -> str:
        return (self.server.get_text() or "").strip()

    def get_port(self) -> int:
        return int(self.port.get_value())

    def get_uuid(self) -> str:
        return (self.uuid.get_text() or "").strip()

    def get_public_key(self) -> str:
        return (self.public_key.get_text() or "").strip()

    def get_short_id(self) -> str:
        return (self.short_id.get_text() or "").strip()

    def get_sni(self) -> str:
        return (self.sni.get_text() or "").strip()

    def get_fingerprint(self) -> str:
        return (self.fingerprint.get_text() or "").strip() or "chrome"

    def get_flow(self) -> str:
        return (self.flow.get_text() or "").strip()

    def get_spider_x(self) -> str:
        return (self.spider_x.get_text() or "").strip()

    def set_values(
        self,
        *,
        server: str = "",
        port: int = 443,
        uuid: str = "",
        public_key: str = "",
        short_id: str = "",
        sni: str = "",
        fingerprint: str = "chrome",
        flow: str = "xtls-rprx-vision",
        spider_x: str = "",
    ) -> None:
        self.server.set_text(server or "")
        self.port.set_value(float(port or 443))
        self.uuid.set_text(uuid or "")
        self.public_key.set_text(public_key or "")
        self.short_id.set_text(short_id or "")
        self.sni.set_text(sni or "")
        self.fingerprint.set_text(fingerprint or "chrome")
        self.flow.set_text(flow or "xtls-rprx-vision")
        self.spider_x.set_text(spider_x or "")
