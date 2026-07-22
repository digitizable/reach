"""Territories — unique region ingress config only (no path Connect).

China is the default territory (deep research pack). Same doors everywhere:
Composition I inbound, Composition III Inverse Snowflake dial-out.
Save writes a recipe; Connect / Disconnect live on Home (and tray).
Plan: docs/CHINA_INGRESS.md (CN-specific notes); model generalizes to any territory.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from gi.repository import Adw, Gdk, GdkPixbuf, Gtk

from app_config import project_root, user_data_dir
from core.backends import PROXY_PROTOCOLS, Backend
from core.profiles import Hop, Profile
from core.readiness import is_mullvad_app_socks, is_vpn_underlay
from core.reverse_agent import (
    PATH_INTENT_INBOUND,
    PATH_INTENT_REVERSE,
    ReversePairing,
    agent_runbook_markdown,
    china_agent_xray_config,
    dumps_config,
    inverse_snowflake_readme,
    is_any_ingress_intent,
    is_reverse_intent,
    new_pairing_token,
    new_short_id,
    new_uuid,
    outside_accept_xray_config,
)
from core.territories import (
    DEFAULT_TERRITORY_CODE,
    TERRITORIES,
    Territory,
    get_territory,
    territory_labels,
)
from core.vless import parse_vless_uri
from services import Services
from widgets.chrome import clear_box, scroll_body

STUDY_URL_CN = "https://anguish.sh/studies/reaching-into-china-from-outside"
IMPL_PLAN = "docs/CHINA_INGRESS.md"
# Back-compat alias
STUDY_URL = STUDY_URL_CN

# Topology combo indices
_TOPO_DIRECT = 0
_TOPO_MULTIHOP = 1
_TOPO_REVERSE = 2


class ChinaIngressPage(Gtk.Box):
    """Territory ingress configuration surface (China default)."""

    def __init__(
        self,
        services: Services,
        *,
        parent_window: Gtk.Window | None = None,
        on_toast: Callable[[str], None] | None = None,
        on_changed: Callable[[], None] | None = None,
        on_navigate: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("page")
        self.add_css_class("china-ingress-page")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._services = services
        self._parent_window = parent_window
        self._on_toast = on_toast
        self._on_changed = on_changed
        self._on_navigate = on_navigate
        self._bound_backend_id: str | None = None
        self._bound_profile_id: str | None = None
        self._territory_code = DEFAULT_TERRITORY_CODE

        # Nested sub-pages (main setup · readiness detail) without new rail items
        from widgets.transitions import SUBPAGE_MS, slide_stack

        # Homogeneous so main / readiness / REALITY fill the shell equally —
        # none of them may push the window larger than the user set.
        self._view_stack = slide_stack(
            duration_ms=SUBPAGE_MS,
            left_right=True,
            hhomogeneous=True,
            vhomogeneous=True,
            css_class="territories-view-stack",
        )
        self._view_stack.set_hexpand(True)
        self._view_stack.set_vexpand(True)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main.set_hexpand(True)
        main.set_vexpand(True)

        self._page_title = Gtk.Label(label="Territories", xalign=0)
        self._page_title.add_css_class("pane-header-title")
        self._page_title.set_hexpand(True)
        self._page_title.set_valign(Gtk.Align.CENTER)
        self._page_sub = Gtk.Label(
            label="Region · door · setup · connect",
            xalign=0,
        )
        self._page_sub.add_css_class("pane-header-sub")
        self._page_sub.set_hexpand(True)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        titles.set_hexpand(True)
        titles.set_valign(Gtk.Align.CENTER)
        titles.append(self._page_title)
        titles.append(self._page_sub)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("pane-header")
        header.set_hexpand(True)
        header.append(titles)
        main.append(header)

        # Two-pane: choose region & mode (left) · set up (right)
        # Keep natural width modest so the shell stays ~default size (no forced stretch).
        split = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        split.add_css_class("master-detail")
        split.add_css_class("doors-split")
        split.set_hexpand(True)
        split.set_vexpand(True)
        split.set_halign(Gtk.Align.FILL)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        left.add_css_class("doors-left")
        left.set_hexpand(False)
        left.set_vexpand(True)
        left.append(self._hero_territory())
        left.append(self._door_cards())
        left.append(self._path_diagram())
        left.append(self._readiness_summary_card())
        left.append(self._group_actions())
        # Docs tucked away — keep left column about choosing, not reading manuals
        docs = self._group_docs()
        docs.set_margin_top(8)
        left.append(docs)
        # Hidden topology model (mode cards drive selection)
        topo_hidden = self._group_topology()
        topo_hidden.set_visible(False)
        left.append(topo_hidden)
        left_scroll = scroll_body(left, margin=14)
        left_scroll.set_hexpand(False)
        left_scroll.set_size_request(260, -1)
        # Never propagate content natural size into the shell (avoids resize glitch)
        left_scroll.set_propagate_natural_width(False)
        left_scroll.set_propagate_natural_height(False)
        split.append(left_scroll)

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.add_css_class("master-detail-sep")
        split.append(sep)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        right.add_css_class("doors-right")
        right.set_hexpand(True)
        right.set_vexpand(True)
        cfg_head = Gtk.Label(label="Set up", xalign=0)
        cfg_head.add_css_class("section-label")
        right.append(cfg_head)
        cfg_sub = Gtk.Label(
            label="Unique ingress setup · Save → path on Home",
            xalign=0,
            wrap=True,
        )
        cfg_sub.set_max_width_chars(36)
        cfg_sub.add_css_class("muted")
        cfg_sub.add_css_class("doors-setup-sub")
        right.append(cfg_sub)
        right.append(self._group_stack())
        right.append(self._group_saved())
        right_scroll = scroll_body(right, margin=14)
        right_scroll.set_hexpand(True)
        right_scroll.set_propagate_natural_width(False)
        right_scroll.set_propagate_natural_height(False)
        split.append(right_scroll)

        main.append(split)
        self._view_stack.add_named(main, "main")
        self._view_stack.add_named(self._build_readiness_page(), "readiness")
        self._view_stack.add_named(self._build_reality_page(), "reality")
        self.append(self._view_stack)

        self._reload_vpn_combo()
        self._on_topo_changed()
        self._set_door_selected(_TOPO_DIRECT)
        self._apply_territory()
        self._refresh_reality_summary()
        self._refresh_readiness()
        self._reload_saved()

    def _territory(self) -> Territory:
        return get_territory(self._territory_code)

    def _group_territory(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup()
        g.add_css_class("doors-territory-group")
        g.set_title("")
        g.set_description("")
        self._territory_row = Adw.ComboRow(title="Region")
        self._territory_row.set_subtitle("")
        self._territory_row.set_model(Gtk.StringList.new(territory_labels()))
        # CN is index 0
        self._territory_row.set_selected(0)
        self._territory_row.connect(
            "notify::selected", lambda *_: self._on_territory_changed()
        )
        g.add(self._territory_row)
        return g

    def _on_territory_changed(self) -> None:
        idx = int(self._territory_row.get_selected())
        if 0 <= idx < len(TERRITORIES):
            self._territory_code = TERRITORIES[idx].code
        else:
            self._territory_code = DEFAULT_TERRITORY_CODE
        self._apply_territory()
        self._on_fields_changed()

    def _apply_territory(self) -> None:
        """Refresh user-visible copy for the selected territory."""
        t = self._territory()
        self._page_title.set_text("Territories")
        self._page_sub.set_text(f"{t.short_name} · outside → inside")
        if hasattr(self, "_banner_title"):
            self._banner_title.set_text(t.short_name)
        if hasattr(self, "_banner_text"):
            # One short rule — long research copy lives in Study link
            self._banner_text.set_text(t.blurb)
        # Update mode card copy if present
        ib = getattr(self, "_door_inbound_btn", None)
        if ib is not None and hasattr(ib, "_door_body"):
            ib._door_body.set_text(f"Host you control in {t.short_name}")
            if hasattr(ib, "_door_hint") and ib._door_hint is not None:
                ib._door_hint.set_visible(False)
        rb = getattr(self, "_door_reverse_btn", None)
        if rb is not None and hasattr(rb, "_door_body"):
            rb._door_body.set_text("Inside peer dials out to you")
            if hasattr(rb, "_door_hint") and rb._door_hint is not None:
                rb._door_hint.set_visible(False)
        # Topology combo: keep III branded; inbound line uses side_label
        # Profile name defaults if still generic
        for entry, default_tpl in (
            (getattr(self, "_profile_name", None), f"Reach · {t.short_name}"),
            (
                getattr(self, "_rev_profile_name", None),
                f"Reach · {t.short_name} · Inverse Snowflake",
            ),
        ):
            if entry is None:
                continue
            cur = (entry.get_text() or "").strip()
            if (
                not cur
                or cur.startswith("Reach China")
                or cur.startswith("Reach ·")
                or cur == "Reach China · Inverse Snowflake"
                or cur == "Reach China · reverse"
            ):
                entry.set_text(default_tpl)
        if hasattr(self, "_saved_head"):
            self._saved_head.set_text(f"Saved Reach · {t.short_name} profiles")
        if hasattr(self, "_hop_group"):
            self._hop_group.set_title(f"{t.side_label} endpoint")
        if hasattr(self, "_study_row"):
            if t.study_url:
                self._study_row.set_subtitle("Open research study")
                self._study_row.set_sensitive(True)
            else:
                self._study_row.set_subtitle("No study for this region")
                self._study_row.set_sensitive(False)
        # Accept port suggestion for reverse
        if hasattr(self, "_rev_accept_port") and t.default_accept_port:
            # only nudge if still at a common default
            v = int(self._rev_accept_port.get_value())
            if v in (443, 8443, 18443):
                self._rev_accept_port.set_value(float(t.default_accept_port))
        self._set_territory_map(t)

    # ── Territory hero ────────────────────────────────────────────────────

    def _hero_territory(self) -> Gtk.Widget:
        """Compact region hero: map + name + short blurb + picker."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("doors-hero")
        card.set_halign(Gtk.Align.FILL)

        self._territory_map = Gtk.Image()
        self._territory_map.add_css_class("reach-territory-map")
        self._territory_map.set_pixel_size(120)
        self._territory_map.set_size_request(120, 120)
        self._territory_map.set_halign(Gtk.Align.CENTER)
        card.append(self._territory_map)

        self._banner_title = Gtk.Label(label="", xalign=0.5)
        self._banner_title.add_css_class("doors-hero-title")
        self._banner_title.set_halign(Gtk.Align.CENTER)
        self._banner_title.set_justify(Gtk.Justification.CENTER)
        self._banner_title.set_wrap(True)
        card.append(self._banner_title)

        self._banner_text = Gtk.Label(label="", xalign=0.5, wrap=True)
        self._banner_text.add_css_class("doors-hero-text")
        self._banner_text.set_halign(Gtk.Align.CENTER)
        self._banner_text.set_justify(Gtk.Justification.CENTER)
        self._banner_text.set_max_width_chars(34)
        card.append(self._banner_text)

        card.append(self._group_territory())
        return card

    def _banner(self) -> Gtk.Widget:
        # Back-compat alias
        return self._hero_territory()

    def _set_territory_map(self, territory: Territory) -> None:
        """Show map silhouette filled with the national flag (or plain globe)."""
        if not hasattr(self, "_territory_map"):
            return
        # HiDPI: render large, display ~120px
        display_px = 120
        render_px = 256

        from core.territory_flags import flag_filled_map_pixbuf

        pb = flag_filled_map_pixbuf(
            code=territory.code,
            map_asset=territory.silhouette_asset(),
            size=render_px,
        )
        if pb is not None:
            texture = Gdk.Texture.new_for_pixbuf(pb)
            self._territory_map.set_from_paintable(texture)
            self._territory_map.set_pixel_size(display_px)
            self._territory_map.set_size_request(display_px, display_px)
            self._territory_map.set_tooltip_text(territory.short_name)
            return

        # Fallback: plain white silhouette / globe
        name = (territory.silhouette_asset() or "globe.svg").strip()
        path = project_root() / "data" / "assets" / name
        if not path.is_file():
            path = project_root() / "data" / "assets" / "globe.svg"
        try:
            plain = GdkPixbuf.Pixbuf.new_from_file_at_size(
                str(path), render_px, render_px
            )
            texture = Gdk.Texture.new_for_pixbuf(plain)
            self._territory_map.set_from_paintable(texture)
            self._territory_map.set_pixel_size(display_px)
            self._territory_map.set_size_request(display_px, display_px)
            self._territory_map.set_tooltip_text(territory.short_name)
        except Exception:
            try:
                self._territory_map.set_from_file(str(path))
            except Exception:
                self._territory_map.set_from_icon_name("mark-location-symbolic")

    # ── Door cards ────────────────────────────────────────────────────────

    def _door_cards(self) -> Gtk.Widget:
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        wrap.add_css_class("door-cards")

        head = Gtk.Label(label="Door", xalign=0)
        head.add_css_class("section-label")
        wrap.append(head)

        self._doors_group = wrap  # for _apply_territory set_description no-ops

        self._door_inbound_btn = self._make_door_card(
            title="Host there",
            body="You control a host in the region",
            hint="VPN first · REALITY or proxy landing",
            icon="network-server-symbolic",
            on_click=lambda *_: self._pick_door(_TOPO_DIRECT),
        )
        wrap.append(self._door_inbound_btn)

        self._door_reverse_btn = self._make_door_card(
            title="Peer dials out",
            body="Inside peer connects to your accept",
            hint="Export client package for the foothold",
            icon="network-transmit-receive-symbolic",
            on_click=lambda *_: self._pick_door(_TOPO_REVERSE),
        )
        wrap.append(self._door_reverse_btn)

        # Keep ActionRow-shaped attrs for _apply_territory title updates
        self._door_inbound = self._door_inbound_btn
        self._door_reverse = self._door_reverse_btn

        pkg = Gtk.Button(label="Export folder")
        pkg.add_css_class("flat")
        pkg.add_css_class("door-pkg-btn")
        pkg.set_halign(Gtk.Align.START)
        pkg.set_tooltip_text("Inverse Snowflake packages")
        pkg.connect("clicked", self._on_open_reverse_dir)
        wrap.append(pkg)
        return wrap

    def _make_door_card(
        self,
        *,
        title: str,
        body: str,
        hint: str,
        icon: str,
        on_click,
    ) -> Gtk.Button:
        btn = Gtk.Button()
        btn.add_css_class("door-card")
        btn.add_css_class("flat")
        btn.set_hexpand(True)
        btn.set_tooltip_text(hint)
        btn.connect("clicked", on_click)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_halign(Gtk.Align.FILL)

        ic = Gtk.Image.new_from_icon_name(icon)
        ic.set_pixel_size(22)
        ic.add_css_class("door-card-icon")
        ic.set_valign(Gtk.Align.CENTER)
        row.append(ic)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col.set_hexpand(True)
        t = Gtk.Label(label=title, xalign=0)
        t.add_css_class("door-card-title")
        col.append(t)
        b = Gtk.Label(label=body, xalign=0, wrap=True)
        b.set_max_width_chars(26)
        b.add_css_class("door-card-body")
        col.append(b)
        # Hint only as tooltip — keep cards two lines (title + body)
        h = Gtk.Label(label=hint, xalign=0, wrap=True)
        h.set_max_width_chars(26)
        h.add_css_class("door-card-hint")
        h.set_visible(False)
        col.append(h)
        row.append(col)

        chev = Gtk.Image.new_from_icon_name("go-next-symbolic")
        chev.set_pixel_size(14)
        chev.add_css_class("door-card-chev")
        chev.set_valign(Gtk.Align.CENTER)
        row.append(chev)

        btn.set_child(row)
        # Stash labels for territory refresh
        btn._door_title = t  # type: ignore[attr-defined]
        btn._door_body = b  # type: ignore[attr-defined]
        btn._door_hint = h  # type: ignore[attr-defined]
        return btn

    def _set_door_selected(self, topo: int) -> None:
        for btn, active in (
            (getattr(self, "_door_inbound_btn", None), topo == _TOPO_DIRECT),
            (getattr(self, "_door_reverse_btn", None), topo == _TOPO_REVERSE),
        ):
            if btn is None:
                continue
            if active:
                btn.add_css_class("door-card-active")
            else:
                btn.remove_css_class("door-card-active")

    def _pick_door(self, topo: int) -> None:
        if hasattr(self, "_topo"):
            self._topo.set_selected(topo)
        self._set_door_selected(topo)
        self._on_topo_changed()
        if topo == _TOPO_REVERSE:
            self._toast("Peer dials out — set accept, export client")
        else:
            self._toast("Host there — VPN, then landing hop")

    def _on_open_reverse_dir(self, *_a) -> None:
        from app_config import user_data_dir

        path = Path(user_data_dir()) / "reverse"
        path.mkdir(parents=True, exist_ok=True)
        uri = path.resolve().as_uri()
        try:
            launcher = Gtk.FileLauncher.new(None)
        except Exception:
            launcher = None
        try:
            import subprocess

            subprocess.Popen(  # noqa: S603
                ["xdg-open", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._toast(f"Opened {path}")
        except Exception:
            self._toast(str(path))

    # ── Topology ──────────────────────────────────────────────────────────

    def _group_topology(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup()
        g.set_title("Topology")
        g.set_description("")

        self._topo = Adw.ComboRow(title="Composition")
        self._topo.set_model(
            Gtk.StringList.new(
                [
                    "Host there (inbound)",
                    "Multi-hop (later)",
                    "Peer dials out (reverse)",
                ]
            )
        )
        self._topo.set_selected(_TOPO_DIRECT)
        self._topo.connect("notify::selected", lambda *_: self._on_topo_changed())
        g.add(self._topo)

        self._vantage = Adw.ComboRow(title="Vantage")
        self._vantage.set_model(
            Gtk.StringList.new(
                [
                    "This machine",
                    "Research host",
                    "Operator host",
                ]
            )
        )
        self._vantage.set_selected(0)
        g.add(self._vantage)

        self._expect_probe = Adw.SwitchRow(
            title="Expect probing",
            subtitle="Prefer REALITY / mundane TLS face",
        )
        self._expect_probe.set_active(True)
        g.add(self._expect_probe)
        return g

    def _path_diagram(self) -> Gtk.Widget:
        """Compact path chips — caption is one short line, not a tutorial."""
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        section.add_css_class("china-path-section")
        section.set_hexpand(True)
        section.set_vexpand(False)

        head = Gtk.Label(label="Path", xalign=0)
        head.add_css_class("section-label")
        head.add_css_class("china-path-diagram-label")
        section.append(head)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("china-path-diagram")
        card.set_hexpand(True)

        # Plain HBox — FlowBox mis-measures height and overlaps siblings
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.add_css_class("china-path-row")
        row.set_halign(Gtk.Align.CENTER)
        row.set_hexpand(True)
        row.set_valign(Gtk.Align.CENTER)

        self._step_client = self._path_chip("You")
        self._step_vpn = self._path_chip("VPN")
        self._step_hop = self._path_chip("Cover")
        self._step_land = self._path_chip("Host")

        for w in (
            self._step_client,
            self._path_arrow(),
            self._step_vpn,
            self._path_arrow(),
            self._step_hop,
            self._path_arrow(),
            self._step_land,
        ):
            row.append(w)
        card.append(row)

        self._path_caption = Gtk.Label(label="", xalign=0, wrap=True)
        self._path_caption.set_max_width_chars(32)
        self._path_caption.set_wrap(True)
        self._path_caption.add_css_class("muted")
        self._path_caption.add_css_class("china-path-caption")
        card.append(self._path_caption)

        section.append(card)
        return section

    def _path_chip(self, text: str) -> Gtk.Widget:
        """Chip as a framed box so layout reserves real height (no CSS-only padding)."""
        from gi.repository import Pango

        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        frame.add_css_class("china-path-chip")
        frame.set_valign(Gtk.Align.CENTER)
        frame.set_halign(Gtk.Align.CENTER)

        lab = Gtk.Label(label=text, justify=Gtk.Justification.CENTER)
        lab.add_css_class("china-path-chip-label")
        lab.set_ellipsize(Pango.EllipsizeMode.END)
        lab.set_max_width_chars(7)
        lab.set_xalign(0.5)
        frame.append(lab)
        # Keep set_text / css class API used by _set_path_active / topo handlers
        frame.set_text = lab.set_text  # type: ignore[method-assign]
        frame.get_text = lab.get_text  # type: ignore[method-assign]
        return frame

    def _path_arrow(self) -> Gtk.Widget:
        a = Gtk.Label(label="→")
        a.add_css_class("china-path-arrow")
        a.set_valign(Gtk.Align.CENTER)
        a.set_halign(Gtk.Align.CENTER)
        return a

    def _group_stack(self) -> Gtk.Widget:
        from widgets.transitions import PANEL_MS, crossfade_stack

        # Non-homogeneous: inactive panels (esp. reverse) must not set the width
        self._stack = crossfade_stack(
            duration_ms=PANEL_MS,
            hhomogeneous=False,
            vhomogeneous=False,
            css_class="territories-mode-stack",
        )
        self._stack.set_hexpand(True)
        self._stack.set_vexpand(False)

        self._stack.add_named(self._panel_direct(), "direct")
        self._stack.add_named(self._panel_multihop(), "multihop")
        self._stack.add_named(self._panel_reverse(), "reverse")
        return self._stack

    # ── Composition I — full form ─────────────────────────────────────────

    def _panel_direct(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        # ── Required VPN underlay ─────────────────────────────────────
        vpn_g = Adw.PreferencesGroup()
        vpn_g.set_title("VPN underlay")
        vpn_g.set_description("Required · add under Adapters if empty")

        self._vpn_row = Adw.ComboRow(title="VPN backend")
        self._vpn_row.set_model(Gtk.StringList.new(["(none — add under Adapters)"]))
        self._vpn_row.connect("notify::selected", lambda *_: self._refresh_readiness())
        vpn_g.add(self._vpn_row)
        box.append(vpn_g)
        self._vpn_backend_ids: list[str] = []

        hop = Adw.PreferencesGroup()
        self._hop_group = hop
        hop.set_title("Landing hop")
        hop.set_description("After VPN · never clearnet")

        # Visual cover pick instead of a long ComboRow
        from widgets.choice_cards import Choice, ChoiceCards

        cover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        cover_lab = Gtk.Label(label="Cover type", xalign=0)
        cover_lab.add_css_class("field-label")
        cover_box.append(cover_lab)
        self._cover_cards = ChoiceCards(
            [
                Choice(
                    "reality",
                    "REALITY",
                    "TLS camouflage",
                    "security-high-symbolic",
                ),
                Choice(
                    "proxy",
                    "Proxy",
                    "SOCKS / HTTP",
                    "network-server-symbolic",
                ),
            ],
            selected="reality",
            on_changed=lambda _id: self._on_cover_kind(),
        )
        cover_box.append(self._cover_cards)
        # Hidden combo kept for _use_proxy() / existing logic
        self._cover_kind = Adw.ComboRow(title="Cover / hop kind")
        self._cover_kind.set_model(
            Gtk.StringList.new(
                [
                    "REALITY (TLS camouflage) — recommended",
                    "Proxy (SOCKS/HTTP you run with cover)",
                ]
            )
        )
        self._cover_kind.set_selected(0)
        self._cover_kind.set_visible(False)
        hop.add(self._cover_kind)
        box.append(cover_box)

        self._profile_name = Adw.EntryRow(title="Profile name")
        self._profile_name.set_text("Reach · China")
        self._profile_name.connect("changed", lambda *_: self._refresh_readiness())
        hop.add(self._profile_name)

        self._backend_name = Adw.EntryRow(title="Landing backend name")
        self._backend_name.set_text("Territory landing")
        self._backend_name.connect("changed", lambda *_: self._refresh_readiness())
        hop.add(self._backend_name)

        box.append(hop)

        # REALITY / Proxy — summary card (REALITY opens full diagram sub-page)
        from widgets.reality_diagram import RealityDiagramEditor
        from widgets.transitions import PANEL_MS, panel_stack

        self._cover_stack = panel_stack(
            duration_ms=PANEL_MS,
            css_class="cover-param-stack",
        )

        # Editor lives on the sub-page; create once so shims always work
        self._reality_editor = RealityDiagramEditor(
            on_changed=self._on_fields_changed,
            show_import=True,
            show_advanced=True,
            layout="row",
        )
        self._reality_editor.import_btn.connect("clicked", self._on_import_vless)

        # Shims so existing get_text / set_text / get_value call sites keep working
        self._vless_entry = self._reality_editor.vless_entry
        self._r_server = self._reality_editor.server
        self._r_port = self._reality_editor.port
        self._r_uuid = self._reality_editor.uuid
        self._r_pk = self._reality_editor.public_key
        self._r_sid = self._reality_editor.short_id
        self._r_sni = self._reality_editor.sni
        self._r_fp = self._reality_editor.fingerprint
        self._r_flow = self._reality_editor.flow
        self._r_spx = self._reality_editor.spider_x

        self._reality_group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._reality_group.add_css_class("reality-diagram-host")
        self._reality_group.append(self._reality_summary_card())
        self._cover_stack.add_named(self._reality_group, "reality")

        # Proxy fields (compact; stay on main setup)
        self._proxy_group = Adw.PreferencesGroup()
        self._proxy_group.set_title("Proxy")
        self._proxy_group.set_description("")

        self._p_proto = Adw.ComboRow(title="Protocol")
        self._p_proto.set_model(Gtk.StringList.new(list(PROXY_PROTOCOLS)))
        self._p_proto.set_selected(0)
        self._proxy_group.add(self._p_proto)

        self._p_host = Adw.EntryRow(title="Host")
        self._p_host.connect("changed", lambda *_: self._on_fields_changed())
        self._proxy_group.add(self._p_host)

        self._p_port = Adw.SpinRow(
            title="Port",
            adjustment=Gtk.Adjustment(
                value=1080, lower=1, upper=65535, step_increment=1, page_increment=10
            ),
        )
        self._p_port.connect("changed", lambda *_: self._refresh_readiness())
        self._proxy_group.add(self._p_port)

        self._p_user = Adw.EntryRow(title="Username (optional)")
        self._proxy_group.add(self._p_user)

        self._p_pass = Adw.PasswordEntryRow(title="Password (optional)")
        self._proxy_group.add(self._p_pass)

        self._cover_stack.add_named(self._proxy_group, "proxy")
        self._cover_stack.set_visible_child_name("reality")
        box.append(self._cover_stack)

        return box

    # ── Composition II / III shells ───────────────────────────────────────

    def _panel_multihop(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        banner = self._soon_banner(
            "Multi-hop (later)",
            "Not wired yet — use Host there or Peer dials out.",
        )
        box.append(banner)

        g = Adw.PreferencesGroup()
        g.set_title("Hops")
        g.set_description("")

        front = Adw.EntryRow(title="Outside front (optional)")
        front.set_text("")
        front.set_sensitive(False)
        g.add(front)

        mid = Adw.EntryRow(title="Middle host")
        mid.set_sensitive(False)
        g.add(mid)

        land = Adw.EntryRow(title="Target-side host")
        land.set_sensitive(False)
        g.add(land)

        cover = Adw.ComboRow(title="Cover on public segments")
        cover.set_model(
            Gtk.StringList.new(["TLS / REALITY on each public hop", "Per-hop custom"])
        )
        cover.set_sensitive(False)
        g.add(cover)
        box.append(g)
        return box

    def _panel_reverse(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        banner = self._soon_banner(
            "Peer dials out",
            "Export client · accept outside · VPN → SOCKS",
        )
        box.append(banner)

        # VPN underlay (shared pattern)
        vpn_g = Adw.PreferencesGroup()
        vpn_g.set_title("VPN underlay")
        vpn_g.set_description("Required · never clearnet")
        self._rev_vpn_row = Adw.ComboRow(title="VPN backend")
        self._rev_vpn_row.set_model(
            Gtk.StringList.new(["(none — add under Adapters)"])
        )
        self._rev_vpn_row.connect(
            "notify::selected", lambda *_: self._on_fields_changed()
        )
        vpn_g.add(self._rev_vpn_row)
        box.append(vpn_g)
        self._rev_vpn_backend_ids: list[str] = []

        accept = Adw.PreferencesGroup()
        accept.set_title("Outside accept")
        accept.set_description("Peer dials here")
        self._rev_accept_host = Adw.EntryRow(title="Accept host (public)")
        self._rev_accept_host.set_text("")
        self._rev_accept_host.connect(
            "notify::text", lambda *_: self._on_fields_changed()
        )
        accept.add(self._rev_accept_host)
        self._rev_accept_port = Adw.SpinRow(
            title="Accept port",
            adjustment=Gtk.Adjustment(
                value=18443, lower=1, upper=65535, step_increment=1, page_increment=10
            ),
        )
        self._rev_accept_port.connect(
            "notify::value", lambda *_: self._on_fields_changed()
        )
        accept.add(self._rev_accept_port)
        self._rev_sni = Adw.EntryRow(title="REALITY dest SNI")
        self._rev_sni.set_text("www.cloudflare.com")
        self._rev_sni.connect("notify::text", lambda *_: self._on_fields_changed())
        accept.add(self._rev_sni)
        self._rev_pub = Adw.EntryRow(title="REALITY public key")
        self._rev_pub.connect("notify::text", lambda *_: self._on_fields_changed())
        accept.add(self._rev_pub)
        self._rev_priv = Adw.EntryRow(title="REALITY private key")
        self._rev_priv.connect("notify::text", lambda *_: self._on_fields_changed())
        accept.add(self._rev_priv)
        self._rev_uuid = Adw.EntryRow(title="UUID / client id")
        self._rev_uuid.set_text(new_uuid())
        self._rev_uuid.connect("notify::text", lambda *_: self._on_fields_changed())
        accept.add(self._rev_uuid)
        self._rev_sid = Adw.EntryRow(title="Short ID")
        self._rev_sid.set_text("")
        self._rev_sid.connect("notify::text", lambda *_: self._on_fields_changed())
        accept.add(self._rev_sid)
        self._rev_token = Adw.EntryRow(title="Pairing token")
        self._rev_token.set_text(new_pairing_token())
        self._rev_token.connect("notify::text", lambda *_: self._on_fields_changed())
        accept.add(self._rev_token)
        gen_row = Adw.ActionRow(
            title="Regenerate UUID / shortId / token",
            subtitle="Does not rotate REALITY x25519 keys",
        )
        gen_btn = Gtk.Button(label="Regenerate")
        gen_btn.set_valign(Gtk.Align.CENTER)
        gen_btn.connect("clicked", self._on_rev_regen)
        gen_row.add_suffix(gen_btn)
        accept.add(gen_row)
        box.append(accept)

        socks = Adw.PreferencesGroup()
        socks.set_title("SOCKS map")
        socks.set_description("Spectre hop after peer connects · 127.0.0.1 if local")
        self._rev_map_host = Adw.EntryRow(title="Map SOCKS host")
        self._rev_map_host.set_text("127.0.0.1")
        self._rev_map_host.connect("notify::text", lambda *_: self._on_fields_changed())
        socks.add(self._rev_map_host)
        self._rev_map_port = Adw.SpinRow(
            title="Map SOCKS port",
            adjustment=Gtk.Adjustment(
                value=10808, lower=1, upper=65535, step_increment=1, page_increment=10
            ),
        )
        self._rev_map_port.connect(
            "notify::value", lambda *_: self._on_fields_changed()
        )
        socks.add(self._rev_map_port)
        box.append(socks)

        names = Adw.PreferencesGroup()
        names.set_title("Profile names")
        self._rev_profile_name = Adw.EntryRow(title="Profile name")
        self._rev_profile_name.set_text("Reach · China · Inverse Snowflake")
        self._rev_profile_name.connect(
            "notify::text", lambda *_: self._on_fields_changed()
        )
        names.add(self._rev_profile_name)
        self._rev_backend_name = Adw.EntryRow(title="Map backend name")
        self._rev_backend_name.set_text("Reverse SOCKS map")
        self._rev_backend_name.connect(
            "notify::text", lambda *_: self._on_fields_changed()
        )
        names.add(self._rev_backend_name)
        box.append(names)

        export = Adw.PreferencesGroup()
        export.set_title("Export package")
        export.set_description("~/.local/share/reach/reverse/")
        exp_row = Adw.ActionRow(
            title="Export client package",
            subtitle="For the inside foothold",
        )
        exp_btn = Gtk.Button(label="Export")
        exp_btn.add_css_class("suggested-action")
        exp_btn.set_valign(Gtk.Align.CENTER)
        exp_btn.connect("clicked", self._on_rev_export)
        exp_row.add_suffix(exp_btn)
        export.add(exp_row)
        box.append(export)
        return box

    def _soon_banner(self, title: str, body: str) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("china-ingress-banner")
        box.add_css_class("china-soon-banner")
        t = Gtk.Label(label=title, xalign=0)
        t.add_css_class("china-ingress-banner-title")
        box.append(t)
        b = Gtk.Label(label=body, xalign=0, wrap=True)
        b.add_css_class("china-ingress-banner-text")
        b.set_max_width_chars(40)
        box.append(b)
        return box

    # ── Nested sub-pages ──────────────────────────────────────────────────

    def _show_view(self, name: str) -> None:
        if hasattr(self, "_view_stack"):
            self._view_stack.set_visible_child_name(name)
            if name == "readiness":
                self._refresh_readiness()
            elif name == "main":
                self._refresh_reality_summary()

    def _show_main(self, *_a) -> None:
        self._show_view("main")

    def _show_readiness(self, *_a) -> None:
        self._show_view("readiness")

    def _show_reality(self, *_a) -> None:
        self._show_view("reality")

    # ── REALITY hop (summary card + full diagram sub-page) ───────────────

    def _reality_summary_card(self) -> Gtk.Widget:
        """Compact setup-pane card; opens the full hop diagram sub-page."""
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.add_css_class("ready-summary-card")
        btn.add_css_class("reality-summary-card")
        btn.set_hexpand(True)
        btn.connect("clicked", self._show_reality)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_halign(Gtk.Align.FILL)

        ic = Gtk.Image.new_from_icon_name("security-high-symbolic")
        ic.set_pixel_size(22)
        ic.add_css_class("reality-summary-icon")
        ic.set_valign(Gtk.Align.CENTER)
        row.append(ic)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col.set_hexpand(True)
        title = Gtk.Label(label="REALITY hop", xalign=0)
        title.add_css_class("ready-summary-title")
        col.append(title)
        self._reality_card_sub = Gtk.Label(
            label="Open diagram to configure",
            xalign=0,
            wrap=True,
        )
        self._reality_card_sub.add_css_class("ready-summary-sub")
        col.append(self._reality_card_sub)
        row.append(col)

        chev = Gtk.Image.new_from_icon_name("go-next-symbolic")
        chev.set_pixel_size(14)
        chev.add_css_class("door-card-chev")
        chev.set_valign(Gtk.Align.CENTER)
        row.append(chev)

        btn.set_child(row)
        return btn

    def _build_reality_page(self) -> Gtk.Widget:
        """Full sub-page: vertical hop diagram (scrolls; never forces window size)."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.add_css_class("reality-page")
        page.set_hexpand(True)
        page.set_vexpand(True)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.add_css_class("pane-header")
        header.set_hexpand(True)
        header.set_vexpand(False)

        back = Gtk.Button()
        back.add_css_class("flat")
        back.add_css_class("circular")
        back.set_icon_name("go-previous-symbolic")
        back.set_tooltip_text("Back to setup")
        back.set_valign(Gtk.Align.CENTER)
        back.connect("clicked", self._show_main)
        header.append(back)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        titles.set_hexpand(True)
        titles.set_valign(Gtk.Align.CENTER)
        t = Gtk.Label(label="REALITY hop", xalign=0)
        t.add_css_class("pane-header-title")
        titles.append(t)
        sub = Gtk.Label(
            label="You → REALITY → cover",
            xalign=0,
            wrap=True,
        )
        sub.add_css_class("pane-header-sub")
        titles.append(sub)
        header.append(titles)
        page.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.add_css_class("reality-page-body")
        body.set_hexpand(True)
        # Editor was created in _panel_direct; reparent if needed
        editor = getattr(self, "_reality_editor", None)
        if editor is not None:
            parent = editor.get_parent()
            if parent is not None:
                parent.remove(editor)
            body.append(editor)
        else:
            from widgets.reality_diagram import RealityDiagramEditor

            self._reality_editor = RealityDiagramEditor(
                on_changed=self._on_fields_changed,
                show_import=True,
                show_advanced=True,
                layout="row",
            )
            self._reality_editor.import_btn.connect("clicked", self._on_import_vless)
            body.append(self._reality_editor)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        actions.set_halign(Gtk.Align.CENTER)
        actions.set_margin_top(4)
        done = Gtk.Button(label="Done")
        done.add_css_class("suggested-action")
        done.connect("clicked", self._show_main)
        actions.append(done)
        body.append(actions)

        body_scroll = scroll_body(body, margin=20)
        body_scroll.set_vexpand(True)
        body_scroll.set_hexpand(True)
        page.append(body_scroll)
        return page

    def _refresh_reality_summary(self) -> None:
        lab = getattr(self, "_reality_card_sub", None)
        ed = getattr(self, "_reality_editor", None)
        if lab is None or ed is None:
            return
        lab.set_text(ed.summary_line())

    # ── Readiness (summary card + full sub-page) ──────────────────────────

    def _readiness_summary_card(self) -> Gtk.Widget:
        """Compact left-rail card that opens the readiness sub-page."""
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.add_css_class("ready-summary-card")
        btn.set_hexpand(True)
        btn.connect("clicked", self._show_readiness)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_halign(Gtk.Align.FILL)

        self._ready_badge = Gtk.Label(label="—")
        self._ready_badge.add_css_class("ready-summary-badge")
        self._ready_badge.set_valign(Gtk.Align.CENTER)
        row.append(self._ready_badge)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col.set_hexpand(True)
        title = Gtk.Label(label="Readiness", xalign=0)
        title.add_css_class("ready-summary-title")
        col.append(title)
        self._ready_card_sub = Gtk.Label(label="Status", xalign=0, wrap=True)
        self._ready_card_sub.add_css_class("ready-summary-sub")
        col.append(self._ready_card_sub)
        row.append(col)

        chev = Gtk.Image.new_from_icon_name("go-next-symbolic")
        chev.set_pixel_size(14)
        chev.add_css_class("door-card-chev")
        chev.set_valign(Gtk.Align.CENTER)
        row.append(chev)

        btn.set_child(row)
        # Keep legacy attrs so refresh can still update summary text
        self._ready_summary = self._ready_card_sub
        self._ready_list = Gtk.Box()  # unused on main; full list lives on sub-page
        return btn

    def _build_readiness_page(self) -> Gtk.Widget:
        """Full-screen sub-page: checklist with clear pass/fail affordances."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.add_css_class("ready-page")
        page.set_hexpand(True)
        page.set_vexpand(True)

        # Header with back
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.add_css_class("pane-header")
        header.set_hexpand(True)

        back = Gtk.Button()
        back.add_css_class("flat")
        back.add_css_class("circular")
        back.set_icon_name("go-previous-symbolic")
        back.set_tooltip_text("Back to Territories")
        back.set_valign(Gtk.Align.CENTER)
        back.connect("clicked", self._show_main)
        header.append(back)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        titles.set_hexpand(True)
        titles.set_valign(Gtk.Align.CENTER)
        t = Gtk.Label(label="Readiness", xalign=0)
        t.add_css_class("pane-header-title")
        titles.append(t)
        self._ready_page_sub = Gtk.Label(
            label="Pass/fail for this door",
            xalign=0,
        )
        self._ready_page_sub.add_css_class("pane-header-sub")
        titles.append(self._ready_page_sub)
        header.append(titles)
        page.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        body.add_css_class("ready-page-body")
        body.set_halign(Gtk.Align.CENTER)
        body.set_hexpand(True)

        # Hero status
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        hero.add_css_class("ready-hero")
        hero.set_halign(Gtk.Align.CENTER)

        self._ready_hero_icon = Gtk.Label(label="…")
        self._ready_hero_icon.add_css_class("ready-hero-icon")
        self._ready_hero_icon.set_halign(Gtk.Align.CENTER)
        hero.append(self._ready_hero_icon)

        self._ready_hero_title = Gtk.Label(label="Checking…", xalign=0.5)
        self._ready_hero_title.add_css_class("ready-hero-title")
        self._ready_hero_title.set_halign(Gtk.Align.CENTER)
        hero.append(self._ready_hero_title)

        self._ready_hero_detail = Gtk.Label(label="", xalign=0.5, wrap=True)
        self._ready_hero_detail.add_css_class("ready-hero-detail")
        self._ready_hero_detail.set_halign(Gtk.Align.CENTER)
        self._ready_hero_detail.set_max_width_chars(42)
        self._ready_hero_detail.set_justify(Gtk.Justification.CENTER)
        hero.append(self._ready_hero_detail)

        self._ready_progress = Gtk.ProgressBar()
        self._ready_progress.add_css_class("ready-progress")
        self._ready_progress.set_hexpand(True)
        self._ready_progress.set_size_request(280, -1)
        hero.append(self._ready_progress)

        self._ready_count = Gtk.Label(label="", xalign=0.5)
        self._ready_count.add_css_class("ready-count")
        self._ready_count.set_halign(Gtk.Align.CENTER)
        hero.append(self._ready_count)

        body.append(hero)

        # Checklist
        list_lab = Gtk.Label(label="Checklist", xalign=0)
        list_lab.add_css_class("section-label")
        list_lab.set_halign(Gtk.Align.START)
        body.append(list_lab)

        self._ready_page_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._ready_page_list.add_css_class("ready-check-list")
        self._ready_page_list.set_hexpand(True)
        body.append(self._ready_page_list)

        # Actions on readiness page
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        actions.set_halign(Gtk.Align.CENTER)
        actions.set_margin_top(8)

        back2 = Gtk.Button(label="Back to setup")
        back2.add_css_class("flat")
        back2.connect("clicked", self._show_main)
        actions.append(back2)

        self._ready_page_save = Gtk.Button(label="Save when ready")
        self._ready_page_save.add_css_class("suggested-action")
        self._ready_page_save.connect("clicked", self._on_save)
        actions.append(self._ready_page_save)
        body.append(actions)

        page.append(scroll_body(body, margin=24))
        return page

    def _ready_check_row(self, ok: bool, text: str) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("ready-check-row")
        row.add_css_class("ready-check-ok" if ok else "ready-check-pending")
        row.set_hexpand(True)

        badge = Gtk.Label(label="✓" if ok else "!")
        badge.add_css_class("ready-check-badge")
        badge.add_css_class("ready-check-badge-ok" if ok else "ready-check-badge-pending")
        badge.set_valign(Gtk.Align.CENTER)
        row.append(badge)

        lab = Gtk.Label(label=text, xalign=0, wrap=True)
        lab.add_css_class("ready-check-text")
        lab.set_hexpand(True)
        lab.set_valign(Gtk.Align.CENTER)
        row.append(lab)
        return row

    # ── Saved profiles ────────────────────────────────────────────────────

    def _group_saved(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._saved_head = Gtk.Label(label="Saved Reach profiles", xalign=0)
        self._saved_head.add_css_class("china-section-title")
        outer.append(self._saved_head)

        self._saved_empty = Gtk.Label(
            label="No saved profiles yet",
            xalign=0,
            wrap=True,
        )
        self._saved_empty.add_css_class("muted")
        outer.append(self._saved_empty)

        self._saved_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._saved_list.add_css_class("china-saved-list")
        outer.append(self._saved_list)
        return outer

    # ── Actions (config only — Connect lives on Home) ─────────────────────

    def _group_actions(self) -> Adw.PreferencesGroup:
        """Territory-unique actions only. Session control is Home / tray."""
        g = Adw.PreferencesGroup()
        g.set_title("")
        g.set_description("")

        save_row = Adw.ActionRow(
            title="Save path",
            subtitle="Writes recipe · Connect from Home",
        )
        self._save_btn = Gtk.Button(label="Save")
        self._save_btn.add_css_class("suggested-action")
        self._save_btn.set_valign(Gtk.Align.CENTER)
        self._save_btn.connect("clicked", self._on_save)
        save_row.add_suffix(self._save_btn)
        g.add(save_row)

        home_row = Adw.ActionRow(
            title="Home",
            subtitle="Connect / Disconnect",
        )
        home_row.set_activatable(True)
        home_row.connect("activated", lambda *_: self._nav("home"))
        try:
            ic = Gtk.Image.new_from_icon_name("go-home-symbolic")
        except Exception:
            ic = Gtk.Image.new_from_icon_name("go-next-symbolic")
        ic.set_pixel_size(16)
        home_row.add_suffix(ic)
        g.add(home_row)

        return g

    def _group_docs(self) -> Adw.PreferencesGroup:
        """Study link only — depth lives on anguish, not in the desk."""
        g = Adw.PreferencesGroup()
        g.set_title("")
        g.set_description("")

        self._study_row = Adw.ActionRow(
            title="Learn more",
            subtitle="Research study",
        )
        self._study_row.set_activatable(True)
        self._study_row.connect("activated", self._on_open_study)
        try:
            ic = Gtk.Image.new_from_icon_name("web-browser-symbolic")
        except Exception:
            ic = Gtk.Image.new_from_icon_name("go-next-symbolic")
        ic.set_pixel_size(16)
        self._study_row.add_suffix(ic)
        g.add(self._study_row)
        return g

    # ── Events ────────────────────────────────────────────────────────────

    def _nav(self, page_id: str) -> None:
        if self._on_navigate:
            self._on_navigate(page_id)

    def _toast(self, msg: str) -> None:
        if self._on_toast:
            self._on_toast(msg)

    def _on_topo_changed(self) -> None:
        idx = int(self._topo.get_selected())
        if idx == _TOPO_MULTIHOP:
            self._stack.set_visible_child_name("multihop")
            self._path_caption.set_text("You → VPN → hops → host")
            self._set_path_active(multi=True)
        elif idx == _TOPO_REVERSE:
            self._stack.set_visible_child_name("reverse")
            self._step_hop.set_text("Accept")
            self._step_land.set_text("Peer")
            self._path_caption.set_text("Peer → accept · VPN → SOCKS")
            self._set_path_active(reverse=True)
            self._reload_vpn_combo()
        else:
            self._stack.set_visible_child_name("direct")
            self._step_hop.set_text("Cover")
            self._step_land.set_text("Host")
            self._path_caption.set_text("You → VPN → cover → host")
            self._set_path_active(direct=True)
        self._refresh_readiness()
        self._update_action_sensitivity()

    def _set_path_active(
        self, *, direct: bool = False, multi: bool = False, reverse: bool = False
    ) -> None:
        chips = (
            self._step_client,
            self._step_vpn,
            self._step_hop,
            self._step_land,
        )
        active = direct or multi or reverse
        for chip in chips:
            if active:
                chip.add_css_class("china-path-chip-active")
            else:
                chip.remove_css_class("china-path-chip-active")

    def _on_cover_kind(self) -> None:
        # Sync hidden combo from choice cards when present
        cards = getattr(self, "_cover_cards", None)
        if cards is not None:
            self._cover_kind.set_selected(1 if cards.selected_id == "proxy" else 0)
        proxy = self._use_proxy()
        cover = getattr(self, "_cover_stack", None)
        if cover is not None:
            cover.set_visible_child_name("proxy" if proxy else "reality")
        else:
            self._reality_group.set_visible(not proxy)
            self._proxy_group.set_visible(proxy)
        self._on_fields_changed()

    def _on_fields_changed(self) -> None:
        self._refresh_reality_summary()
        self._refresh_readiness()
        self._update_action_sensitivity()

    def _is_direct_v1(self) -> bool:
        return int(self._topo.get_selected()) == _TOPO_DIRECT

    def _is_reverse(self) -> bool:
        return int(self._topo.get_selected()) == _TOPO_REVERSE

    def _is_wireable(self) -> bool:
        return self._is_direct_v1() or self._is_reverse()

    def _use_proxy(self) -> bool:
        cards = getattr(self, "_cover_cards", None)
        if cards is not None:
            return cards.selected_id == "proxy"
        return int(self._cover_kind.get_selected()) == 1

    def _selected_vpn_backend(self) -> Backend | None:
        if self._is_reverse():
            ids = getattr(self, "_rev_vpn_backend_ids", None) or []
            row = getattr(self, "_rev_vpn_row", None)
            if not ids or row is None:
                return None
            idx = int(row.get_selected())
            if idx < 0 or idx >= len(ids):
                return None
            bid = ids[idx]
            return self._services.backends.get(bid) if bid else None
        if not getattr(self, "_vpn_backend_ids", None):
            return None
        idx = int(self._vpn_row.get_selected())
        if idx < 0 or idx >= len(self._vpn_backend_ids):
            return None
        bid = self._vpn_backend_ids[idx]
        if not bid:
            return None
        return self._services.backends.get(bid)

    def _reload_vpn_combo(self) -> None:
        """Refresh VPN underlay dropdown(s) from BackendStore."""
        prev_direct = None
        if getattr(self, "_vpn_backend_ids", None):
            b = None
            if not self._is_reverse():
                idx = int(self._vpn_row.get_selected())
                if 0 <= idx < len(self._vpn_backend_ids):
                    bid = self._vpn_backend_ids[idx]
                    b = self._services.backends.get(bid) if bid else None
            if b is not None:
                prev_direct = b.id

        prev_rev = None
        if getattr(self, "_rev_vpn_backend_ids", None) and hasattr(
            self, "_rev_vpn_row"
        ):
            idx = int(self._rev_vpn_row.get_selected())
            if 0 <= idx < len(self._rev_vpn_backend_ids):
                bid = self._rev_vpn_backend_ids[idx]
                bb = self._services.backends.get(bid) if bid else None
                if bb is not None:
                    prev_rev = bb.id

        options: list[str] = []
        ids: list[str] = []
        for b in self._services.backends.list():
            if not is_vpn_underlay(b):
                continue
            label = b.label()
            if b.kind == "VPN":
                if not b.is_configured():
                    label = f"{label} (incomplete)"
                elif not b.enabled:
                    label = f"{label} (disabled)"
            elif is_mullvad_app_socks(b):
                label = f"{label} · Mullvad app"
                if not b.enabled:
                    label = f"{label} (disabled)"
            options.append(label)
            ids.append(b.id)

        if not options:
            options = ["(none — add VPN or Mullvad SOCKS under Backends)"]
            ids = [""]

        def _apply(row: Adw.ComboRow, id_list: list[str], prev: str | None) -> None:
            row.set_model(Gtk.StringList.new(options))
            if prev and prev in id_list:
                row.set_selected(id_list.index(prev))
            else:
                sel = 0
                for i, bid in enumerate(id_list):
                    bb = self._services.backends.get(bid) if bid else None
                    if bb is not None and bb.enabled and bb.is_configured():
                        sel = i
                        break
                row.set_selected(sel)

        self._vpn_backend_ids = ids
        _apply(self._vpn_row, ids, prev_direct)
        if hasattr(self, "_rev_vpn_row"):
            self._rev_vpn_backend_ids = list(ids)
            _apply(self._rev_vpn_row, ids, prev_rev)

    def _collect_checks(self) -> list[tuple[bool, str]]:
        if int(self._topo.get_selected()) == _TOPO_MULTIHOP:
            return [
                (False, "Multi-hop not wired — use Host there or Peer dials out"),
                (True, "Multi-hop is preview-only"),
            ]
        if self._is_reverse():
            return self._collect_checks_reverse()
        return self._collect_checks_inbound()

    def _collect_checks_inbound(self) -> list[tuple[bool, str]]:
        checks: list[tuple[bool, str]] = []
        t = self._territory()
        vpn = self._selected_vpn_backend()
        if vpn is None:
            checks.append((False, "VPN underlay selected"))
        else:
            checks.append((True, f"VPN underlay: {vpn.label()}"))
            checks.append((vpn.enabled, "VPN underlay enabled"))
            checks.append(
                (
                    vpn.is_configured(),
                    "VPN underlay complete",
                )
            )

        pname = (self._profile_name.get_text() or "").strip()
        bname = (self._backend_name.get_text() or "").strip()
        checks.append((bool(pname), "Profile name set"))
        checks.append((bool(bname), "Landing backend name set"))

        if self._use_proxy():
            host = (self._p_host.get_text() or "").strip()
            port = int(self._p_port.get_value())
            checks.append((bool(host), "Proxy host set"))
            checks.append((port > 0, f"Proxy port {port}"))
            checks.append(
                (True, "You confirm hop cover is not pure high-entropy"),
            )
        else:
            server = (self._r_server.get_text() or "").strip()
            uuid_s = (self._r_uuid.get_text() or "").strip()
            pk = (self._r_pk.get_text() or "").strip()
            sni = (self._r_sni.get_text() or "").strip()
            port = int(self._r_port.get_value())
            checks.append((bool(server), "REALITY server set"))
            checks.append((port > 0, f"Hop port {port}"))
            checks.append((bool(uuid_s), "UUID set"))
            checks.append((bool(pk), "Public key set"))
            checks.append((bool(sni), "SNI set"))
        checks.append((True, f"Never dial {t.short_name} from clearnet"))
        checks.append((True, f"{t.short_name} host is operator-owned"))
        return checks

    def _collect_checks_reverse(self) -> list[tuple[bool, str]]:
        checks: list[tuple[bool, str]] = []
        vpn = self._selected_vpn_backend()
        if vpn is None:
            checks.append((False, "VPN underlay selected"))
        else:
            checks.append((True, f"VPN underlay: {vpn.label()}"))
            checks.append((vpn.enabled, "VPN underlay enabled"))
            checks.append((vpn.is_configured(), "VPN underlay complete"))

        host = (self._rev_accept_host.get_text() or "").strip()
        sni = (self._rev_sni.get_text() or "").strip()
        pub = (self._rev_pub.get_text() or "").strip()
        uuid_s = (self._rev_uuid.get_text() or "").strip()
        map_host = (self._rev_map_host.get_text() or "").strip()
        map_port = int(self._rev_map_port.get_value())
        pname = (self._rev_profile_name.get_text() or "").strip()
        bname = (self._rev_backend_name.get_text() or "").strip()

        checks.append((bool(host), "Accept host set"))
        checks.append((int(self._rev_accept_port.get_value()) > 0, "Accept port set"))
        checks.append((bool(sni), "REALITY dest SNI set"))
        checks.append(
            (
                bool(pub) or bool((self._rev_priv.get_text() or "").strip()),
                "REALITY keys set",
            ),
        )
        checks.append((bool(uuid_s), "UUID set"))
        checks.append((bool(map_host), "SOCKS map host set"))
        checks.append((map_port > 0, f"SOCKS map port {map_port}"))
        checks.append((bool(pname), "Profile name set"))
        checks.append((bool(bname), "Map backend name set"))
        checks.append((True, "Agent uses TCP REALITY dial-out"))
        checks.append((True, "Foothold is willing peer"))
        return checks

    def _readiness_ok(self) -> bool:
        if not self._is_wireable():
            return False
        return all(
            ok
            for ok, label in self._collect_checks()
            if not label.startswith("You confirm")
        )

    def _refresh_readiness(self) -> None:
        checks = self._collect_checks()
        total = max(1, len(checks))
        passed = sum(1 for ok, _ in checks if ok)
        frac = passed / total
        all_ok = self._is_wireable() and self._readiness_ok()

        # Compact card on main view
        if hasattr(self, "_ready_badge"):
            self._ready_badge.set_text("✓" if all_ok else f"{passed}/{total}")
            for cls in ("ready-summary-badge-ok", "ready-summary-badge-pending"):
                self._ready_badge.remove_css_class(cls)
            self._ready_badge.add_css_class(
                "ready-summary-badge-ok" if all_ok else "ready-summary-badge-pending"
            )
        if hasattr(self, "_ready_card_sub"):
            if not self._is_wireable():
                self._ready_card_sub.set_text("Choose Host there or Peer dials out")
            elif all_ok:
                self._ready_card_sub.set_text("All checks passed · tap for details")
            else:
                self._ready_card_sub.set_text(
                    f"{passed} of {total} checks · tap to review"
                )

        # Full readiness sub-page
        if hasattr(self, "_ready_page_list"):
            clear_box(self._ready_page_list)
            for ok, text in checks:
                self._ready_page_list.append(self._ready_check_row(ok, text))
        if hasattr(self, "_ready_progress"):
            self._ready_progress.set_fraction(frac)
        if hasattr(self, "_ready_count"):
            self._ready_count.set_text(f"{passed} of {total} checks passed")
        if hasattr(self, "_ready_hero_icon"):
            self._ready_hero_icon.set_text("✓" if all_ok else "…")
            for cls in ("ready-hero-icon-ok", "ready-hero-icon-pending"):
                self._ready_hero_icon.remove_css_class(cls)
            self._ready_hero_icon.add_css_class(
                "ready-hero-icon-ok" if all_ok else "ready-hero-icon-pending"
            )
        if hasattr(self, "_ready_hero_title"):
            if not self._is_wireable():
                self._ready_hero_title.set_text("Mode not ready")
            elif all_ok:
                self._ready_hero_title.set_text("Ready to save")
            else:
                self._ready_hero_title.set_text("Almost there")
        if hasattr(self, "_ready_hero_detail"):
            if not self._is_wireable():
                self._ready_hero_detail.set_text(
                    "Pick Host there or Peer dials out"
                )
            elif all_ok:
                if self._is_reverse():
                    self._ready_hero_detail.set_text(
                        "Save · export client · run accept + agent · Home → Connect"
                    )
                else:
                    self._ready_hero_detail.set_text(
                        "Save path · then Connect on Home"
                    )
            else:
                self._ready_hero_detail.set_text(
                    "Finish checks below · then Save"
                )
        if hasattr(self, "_ready_page_sub"):
            mode = "Peer dials out" if self._is_reverse() else "Host there"
            t = self._territory()
            self._ready_page_sub.set_text(f"{t.short_name} · {mode}")

        # Legacy summary label alias (if something still references styles)
        if hasattr(self, "_ready_summary") and self._ready_summary is not None:
            for cls in ("china-ready-ok", "china-ready-pending"):
                self._ready_summary.remove_css_class(cls)
            self._ready_summary.add_css_class(
                "china-ready-ok" if all_ok else "china-ready-pending"
            )

        self._update_action_sensitivity()

    def _update_action_sensitivity(self) -> None:
        wire = self._is_wireable()
        ok = self._readiness_ok()
        if hasattr(self, "_save_btn"):
            self._save_btn.set_sensitive(wire and ok)
        if hasattr(self, "_ready_page_save"):
            self._ready_page_save.set_sensitive(wire and ok)
            self._ready_page_save.set_label(
                "Save path" if (wire and ok) else "Save when ready"
            )

    def _reverse_pairing(self) -> ReversePairing:
        sni = (self._rev_sni.get_text() or "").strip() or "www.microsoft.com"
        return ReversePairing(
            accept_host=(self._rev_accept_host.get_text() or "").strip(),
            accept_port=int(self._rev_accept_port.get_value()),
            uuid=(self._rev_uuid.get_text() or "").strip() or new_uuid(),
            public_key=(self._rev_pub.get_text() or "").strip(),
            private_key=(self._rev_priv.get_text() or "").strip(),
            short_id=(self._rev_sid.get_text() or "").strip(),
            dest_sni=sni,
            dest_addr=f"{sni}:443",
            map_socks_host=(self._rev_map_host.get_text() or "").strip() or "127.0.0.1",
            map_socks_port=int(self._rev_map_port.get_value()),
            pairing_token=(self._rev_token.get_text() or "").strip(),
        )

    def _on_rev_regen(self, *_a) -> None:
        self._rev_uuid.set_text(new_uuid())
        self._rev_sid.set_text(new_short_id())
        self._rev_token.set_text(new_pairing_token())
        self._toast("Regenerated UUID, shortId, token")
        self._refresh_readiness()

    def _export_reverse_package(self, p: ReversePairing) -> Path:
        """Write Inverse Snowflake package + Xray JSON + accept into user data."""
        out_dir = Path(user_data_dir()) / "reverse"
        out_dir.mkdir(parents=True, exist_ok=True)
        # pages/ -> src/ -> project root
        scripts_src = Path(__file__).resolve().parents[2] / "scripts"
        if not scripts_src.is_dir():
            scripts_src = Path(__file__).resolve().parents[1] / "scripts"
        scripts_note = str(scripts_src)
        token = p.token()
        accept = f"{p.accept_host}:{p.accept_port}"

        (out_dir / "outside-accept.json").write_text(
            dumps_config(outside_accept_xray_config(p)), encoding="utf-8"
        )
        (out_dir / "china-agent.json").write_text(
            dumps_config(china_agent_xray_config(p)), encoding="utf-8"
        )
        (out_dir / "RUNBOOK.md").write_text(
            agent_runbook_markdown(p, scripts_dir=scripts_note), encoding="utf-8"
        )
        (out_dir / "INVERSE_SNOWFLAKE.md").write_text(
            inverse_snowflake_readme(p), encoding="utf-8"
        )
        pairing = {
            "role": "inverse_snowflake",
            "composition": "III",
            "token": token,
            "pairing_token": token,
            "accept": accept,
            "accept_host": p.accept_host,
            "accept_port": p.accept_port,
            "map_socks": f"{p.map_socks_host}:{p.map_socks_port}",
            "agent_id": "",
            "note": "Empty agent_id → ephemeral isf-* at runtime",
        }
        (out_dir / "pairing.json").write_text(
            json.dumps(pairing, indent=2) + "\n", encoding="utf-8"
        )
        (out_dir / "TOKEN").write_text(token + "\n", encoding="utf-8")

        for name in (
            "spectre-reverse-accept.py",
            "spectre-reverse-agent.py",
            "spectre-inverse-snowflake.py",
        ):
            src = scripts_src / name
            if src.is_file():
                (out_dir / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        (out_dir / "run-accept.sh").write_text(
            "#!/usr/bin/env bash\n"
            f'cd "$(dirname "$0")"\n'
            f'exec python3 ./spectre-reverse-accept.py --token {token!r} '
            f"--listen 0.0.0.0:{p.accept_port} "
            f"--socks {p.map_socks_host}:{p.map_socks_port} "
            f"--data-port-min 18500 --data-port-max 18599\n",
            encoding="utf-8",
        )
        (out_dir / "run-agent.sh").write_text(
            "#!/usr/bin/env bash\n"
            f'cd "$(dirname "$0")"\n'
            f'exec python3 ./spectre-inverse-snowflake.py --config ./pairing.json "$@"\n',
            encoding="utf-8",
        )
        (out_dir / "run-inverse-snowflake.sh").write_text(
            "#!/usr/bin/env bash\n"
            f'cd "$(dirname "$0")"\n'
            f'exec python3 ./spectre-inverse-snowflake.py --config ./pairing.json "$@"\n',
            encoding="utf-8",
        )
        try:
            for sh in (
                "run-accept.sh",
                "run-agent.sh",
                "run-inverse-snowflake.sh",
            ):
                (out_dir / sh).chmod(0o755)
            (out_dir / "TOKEN").chmod(0o600)
        except OSError:
            pass
        return out_dir

    def _on_rev_export(self, *_a) -> None:
        p = self._reverse_pairing()
        if not p.accept_host:
            self._toast("Set accept host first")
            return
        try:
            out_dir = self._export_reverse_package(p)
        except OSError as exc:
            self._toast(f"Export failed: {exc}")
            return
        self._toast(f"Exported Inverse Snowflake package → {out_dir}")

    def _on_import_vless(self, *_a) -> None:
        raw = (self._vless_entry.get_text() or "").strip()
        if not raw:
            self._toast("Paste a vless:// share link first")
            return
        try:
            data = parse_vless_uri(raw)
        except ValueError as exc:
            self._toast(str(exc))
            return
        self._cover_kind.set_selected(0)
        if hasattr(self, "_cover_cards"):
            self._cover_cards.set_selected("reality")
        self._on_cover_kind()
        self._reality_editor.set_values(
            server=data.get("reality_server") or "",
            port=int(data.get("reality_port") or 443),
            uuid=data.get("reality_uuid") or "",
            public_key=data.get("reality_public_key") or "",
            short_id=data.get("reality_short_id") or "",
            sni=data.get("reality_sni") or "",
            fingerprint=data.get("reality_fingerprint") or "chrome",
            flow=data.get("reality_flow") or "xtls-rprx-vision",
            spider_x=data.get("reality_spider_x") or "",
        )
        name = (data.get("name") or "").strip()
        if name:
            self._backend_name.set_text(name)
            cur = (self._profile_name.get_text() or "").strip()
            if not cur or cur.startswith("Reach China") or cur.startswith("Reach ·"):
                self._profile_name.set_text(f"Reach · {self._territory().short_name} · {name}")
        self._toast("Imported REALITY parameters onto the diagram")
        self._refresh_reality_summary()
        self._refresh_readiness()

    def _on_save(self, *_a) -> None:
        if not self._readiness_ok():
            self._toast("Fill required fields first")
            return
        try:
            if self._is_reverse():
                backend, profile = self._persist_reverse()
            else:
                backend, profile = self._persist()
        except ValueError as exc:
            self._toast(str(exc))
            return
        self._bound_backend_id = backend.id
        self._bound_profile_id = profile.id
        self._services.config.last_profile_id = profile.id
        try:
            self._services.save_config()
        except Exception:
            pass
        kind = "reverse" if self._is_reverse() else "inbound"
        self._toast(f"Saved “{profile.name}” ({kind}) · Connect on Home")
        if self._on_changed:
            self._on_changed()
        self._reload_saved()
        self._update_action_sensitivity()

    def _persist_reverse(self) -> tuple[Backend, Profile]:
        vpn = self._selected_vpn_backend()
        if vpn is None or not is_vpn_underlay(vpn):
            raise ValueError("Select a VPN underlay (WireGuard or Mullvad) first")
        if not vpn.enabled or not vpn.is_configured():
            raise ValueError(
                f"VPN underlay “{vpn.label()}” is incomplete or disabled — fix under Backends"
            )
        pairing = self._reverse_pairing()
        if not pairing.accept_host:
            raise ValueError("Set outside accept host")
        t = self._territory()
        bname = (self._rev_backend_name.get_text() or "").strip() or "Reverse SOCKS map"
        pname = (
            (self._rev_profile_name.get_text() or "").strip()
            or f"Reach · {t.short_name} · Inverse Snowflake"
        )
        note_bits = [
            f"path_intent={PATH_INTENT_REVERSE} · composition=reverse · "
            f"territory={t.code} · "
            f"accept={pairing.accept_host}:{pairing.accept_port} · "
            "Inverse Snowflake dial-out · VPN underlay required"
        ]
        extra = {
            "proxy_protocol": "SOCKS5",
            "proxy_host": pairing.map_socks_host,
            "proxy_port": pairing.map_socks_port,
            "proxy_username": "",
            "proxy_password": "",
            "notes": " · ".join(note_bits),
            "enabled": True,
        }
        if self._bound_backend_id and self._services.backends.get(
            self._bound_backend_id
        ):
            backend = self._services.backends.update(
                self._bound_backend_id, kind="Proxy", name=bname, **extra
            )
            assert backend is not None
        else:
            backend = self._services.backends.create(
                kind="Proxy", name=bname, **extra
            )

        hops = [
            Hop(kind=vpn.kind if vpn.kind == "VPN" else "Proxy", backend_id=vpn.id),
            Hop(kind="Proxy", backend_id=backend.id),
        ]
        summary = (
            f"Inverse Snowflake · {t.short_name} · {vpn.label()} → map "
            f"{pairing.map_socks_host}:{pairing.map_socks_port}"
        )
        info = (
            f"Reach · {t.short_name} Inverse Snowflake (Composition III): peer client "
            "dials out to your outside accept; Spectre uses VPN underlay then the "
            "SOCKS map. Export Inverse Snowflake package for M. Outside vantage only. "
            "Never use the map from clearnet."
        )
        hop_dicts = [{"kind": h.kind, "backend_id": h.backend_id} for h in hops]
        path_intent = PATH_INTENT_REVERSE
        notes_s = " · ".join(note_bits)
        if self._bound_profile_id and self._services.profiles.get(self._bound_profile_id):
            profile = self._services.profiles.update(
                self._bound_profile_id,
                name=pname,
                summary=summary,
                hops=hop_dicts,
                notes=notes_s,
                info=info,
                path_intent=path_intent,
                favorite=True,
            )
            assert profile is not None
        else:
            profile = self._services.profiles.create(
                name=pname,
                summary=summary,
                hops=hops,
                notes=notes_s,
                info=info,
                path_intent=path_intent,
                favorite=True,
            )
        # Best-effort auto-export so agent package exists after Save
        try:
            self._export_reverse_package(pairing)
        except OSError:
            pass
        return backend, profile

    def _persist(self) -> tuple[Backend, Profile]:
        vpn = self._selected_vpn_backend()
        if vpn is None or not is_vpn_underlay(vpn):
            raise ValueError("Select a VPN underlay (WireGuard or Mullvad) first")
        if not vpn.enabled or not vpn.is_configured():
            raise ValueError(
                f"VPN underlay “{vpn.label()}” is incomplete or disabled — fix under Backends"
            )

        t = self._territory()
        bname = (self._backend_name.get_text() or "").strip() or f"{t.short_name} landing"
        pname = (self._profile_name.get_text() or "").strip() or f"Reach · {t.short_name}"
        note_bits = [
            f"path_intent={PATH_INTENT_INBOUND} · composition=inbound · "
            f"territory={t.code} · VPN underlay required · "
            f"operator-owned {t.side_label} host"
        ]

        if self._use_proxy():
            proto_idx = int(self._p_proto.get_selected())
            proto = (
                PROXY_PROTOCOLS[proto_idx]
                if 0 <= proto_idx < len(PROXY_PROTOCOLS)
                else "SOCKS5"
            )
            extra = {
                "proxy_protocol": proto,
                "proxy_host": (self._p_host.get_text() or "").strip(),
                "proxy_port": int(self._p_port.get_value()),
                "proxy_username": (self._p_user.get_text() or "").strip(),
                "proxy_password": (self._p_pass.get_text() or "").strip(),
                "notes": " · ".join(note_bits),
                "enabled": True,
            }
            if self._bound_backend_id and self._services.backends.get(
                self._bound_backend_id
            ):
                backend = self._services.backends.update(
                    self._bound_backend_id, kind="Proxy", name=bname, **extra
                )
                assert backend is not None
            else:
                backend = self._services.backends.create(
                    kind="Proxy", name=bname, **extra
                )
            hop_kind = "Proxy"
        else:
            extra = {
                "reality_server": (self._r_server.get_text() or "").strip(),
                "reality_port": int(self._r_port.get_value()),
                "reality_uuid": (self._r_uuid.get_text() or "").strip(),
                "reality_public_key": (self._r_pk.get_text() or "").strip(),
                "reality_short_id": (self._r_sid.get_text() or "").strip(),
                "reality_sni": (self._r_sni.get_text() or "").strip(),
                "reality_fingerprint": (self._r_fp.get_text() or "").strip()
                or "chrome",
                "reality_flow": (self._r_flow.get_text() or "").strip()
                or "xtls-rprx-vision",
                "reality_spider_x": (self._r_spx.get_text() or "").strip(),
                "notes": " · ".join(note_bits),
                "enabled": True,
            }
            if self._bound_backend_id and self._services.backends.get(
                self._bound_backend_id
            ):
                backend = self._services.backends.update(
                    self._bound_backend_id, kind="REALITY", name=bname, **extra
                )
                assert backend is not None
            else:
                backend = self._services.backends.create(
                    kind="REALITY", name=bname, **extra
                )
            hop_kind = "REALITY"

        # Hop 0 = VPN underlay, hop 1 = China endpoint (never reverse order)
        hops = [
            Hop(kind=vpn.kind if vpn.kind == "VPN" else "Proxy", backend_id=vpn.id),
            Hop(kind=hop_kind, backend_id=backend.id),
        ]
        summary = (
            f"Ingress · {t.short_name} · {vpn.label()} → {backend.status_line()}"
        )
        info = (
            f"Reach · {t.short_name} (ingress): VPN underlay required, then TLS-shaped "
            f"hop to an operator-owned {t.side_label} host. Outside vantage only — not a "
            f"claim about users inside the territory. Never dial from clearnet."
        )
        hop_dicts = [{"kind": h.kind, "backend_id": h.backend_id} for h in hops]
        path_intent = PATH_INTENT_INBOUND
        if self._bound_profile_id and self._services.profiles.get(self._bound_profile_id):
            profile = self._services.profiles.update(
                self._bound_profile_id,
                name=pname,
                summary=summary,
                hops=hop_dicts,
                notes=" · ".join(note_bits),
                info=info,
                path_intent=path_intent,
                favorite=True,
            )
            assert profile is not None
        else:
            profile = self._services.profiles.create(
                name=pname,
                summary=summary,
                hops=hops,
                notes=" · ".join(note_bits),
                info=info,
                path_intent=path_intent,
                favorite=True,
            )
        return backend, profile

    def _reload_saved(self) -> None:
        if not hasattr(self, "_saved_list"):
            return
        clear_box(self._saved_list)
        ingress = [
            p
            for p in self._services.profiles.list()
            if is_any_ingress_intent(p.path_intent, p.notes, p.name)
        ]
        self._saved_empty.set_visible(len(ingress) == 0)
        self._saved_list.set_visible(len(ingress) > 0)
        for p in ingress:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.add_css_class("china-saved-row")
            tag = "rev" if is_reverse_intent(p.path_intent, p.notes) else "in"
            lab = Gtk.Label(label=f"[{tag}] {p.name} · {p.hops_line()}", xalign=0)
            lab.set_hexpand(True)
            lab.add_css_class("china-saved-name")
            row.append(lab)
            load = Gtk.Button(label="Load")
            load.add_css_class("flat")
            load.set_tooltip_text("Load into setup (edit / re-save)")
            load.connect("clicked", self._on_use_profile, p.id)
            row.append(load)
            self._saved_list.append(row)
        self._update_action_sensitivity()

    def _on_use_profile(self, _btn: Gtk.Button, profile_id: str) -> None:
        profile = self._services.profiles.get(profile_id)
        if profile is None:
            return
        self._bound_profile_id = profile.id
        rev = is_reverse_intent(profile.path_intent, profile.notes)
        self._topo.set_selected(_TOPO_REVERSE if rev else _TOPO_DIRECT)
        self._on_topo_changed()
        self._reload_vpn_combo()
        # Prefer: hop0 = VPN underlay, hop1 = China endpoint or map
        if len(profile.hops) >= 2:
            vpn_b = self._services.backends.get(profile.hops[0].backend_id)
            china_b = self._services.backends.get(profile.hops[1].backend_id)
            vpn_ids = (
                self._rev_vpn_backend_ids
                if rev
                else self._vpn_backend_ids
            )
            vpn_row = self._rev_vpn_row if rev else self._vpn_row
            if (
                vpn_b is not None
                and is_vpn_underlay(vpn_b)
                and vpn_b.id in vpn_ids
            ):
                vpn_row.set_selected(vpn_ids.index(vpn_b.id))
            if china_b is not None:
                self._bound_backend_id = china_b.id
                if rev and china_b.kind == "Proxy":
                    self._rev_map_host.set_text(china_b.proxy_host or "127.0.0.1")
                    self._rev_map_port.set_value(china_b.proxy_port or 10808)
                    self._rev_backend_name.set_text(china_b.name)
                    self._rev_profile_name.set_text(profile.name)
                else:
                    self._load_backend_into_form(china_b)
                    self._profile_name.set_text(profile.name)
        elif profile.hops:
            self._bound_backend_id = profile.hops[0].backend_id or None
            b = self._services.backends.get(profile.hops[0].backend_id)
            if b is not None and not is_vpn_underlay(b):
                self._load_backend_into_form(b)
            self._toast("This profile needs a VPN underlay hop — select VPN and Save again")
        if not rev:
            self._profile_name.set_text(profile.name)
        self._services.config.last_profile_id = profile.id
        try:
            self._services.save_config()
        except Exception:
            pass
        self._toast(f"Loaded “{profile.name}” into setup")
        self._refresh_readiness()
        self._update_action_sensitivity()

    def _load_backend_into_form(self, b: Backend) -> None:
        self._backend_name.set_text(b.name)
        if b.kind == "Proxy":
            self._cover_kind.set_selected(1)
            if hasattr(self, "_cover_cards"):
                self._cover_cards.set_selected("proxy")
            self._on_cover_kind()
            if b.proxy_protocol in PROXY_PROTOCOLS:
                self._p_proto.set_selected(PROXY_PROTOCOLS.index(b.proxy_protocol))
            self._p_host.set_text(b.proxy_host or "")
            self._p_port.set_value(b.proxy_port or 1080)
            self._p_user.set_text(b.proxy_username or "")
            self._p_pass.set_text(b.proxy_password or "")
        else:
            self._cover_kind.set_selected(0)
            if hasattr(self, "_cover_cards"):
                self._cover_cards.set_selected("reality")
            self._on_cover_kind()
            self._reality_editor.set_values(
                server=b.reality_server or "",
                port=b.reality_port or 443,
                uuid=b.reality_uuid or "",
                public_key=b.reality_public_key or "",
                short_id=b.reality_short_id or "",
                sni=b.reality_sni or "",
                fingerprint=b.reality_fingerprint or "chrome",
                flow=b.reality_flow or "xtls-rprx-vision",
                spider_x=getattr(b, "reality_spider_x", "") or "",
            )
            self._refresh_reality_summary()

    def reload(self) -> None:
        """Called when navigating back or data changes elsewhere."""
        self._reload_vpn_combo()
        self._reload_saved()
        self._refresh_readiness()
        self._update_action_sensitivity()

    def _on_open_study(self, *_a) -> None:
        t = self._territory()
        url = t.study_url or STUDY_URL_CN
        if not t.study_url and t.code != "CN":
            self._toast(
                f"No study pack for {t.short_name} yet — opening China pack as reference"
            )
        parent = self._parent_window
        try:
            launcher = Gtk.UriLauncher.new(url)
            launcher.launch(parent, None, None, None)
        except Exception:
            try:
                import webbrowser

                webbrowser.open(url)
            except Exception:
                self._toast("Could not open study URL")
                return
        self._toast("Opened study in browser")
