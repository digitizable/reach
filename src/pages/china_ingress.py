"""Reach — territory ingress UI (outside → target-side host).

China is the default territory (deep research pack). Same doors everywhere:
Composition I inbound, Composition III Inverse Snowflake dial-out.
Plan: docs/CHINA_INGRESS.md (CN-specific notes); model generalizes to any territory.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Callable
from pathlib import Path

from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Gtk

from app_config import project_root, user_data_dir
from core.backends import PROXY_PROTOCOLS, Backend
from core.client import CoreState
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
from widgets.chrome import clear_box, page_header, scroll_body

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
        self._action_busy = False
        self._probe_busy = False
        self._territory_code = DEFAULT_TERRITORY_CODE

        self._page_title = Gtk.Label(label="Doors", xalign=0)
        self._page_title.add_css_class("pane-header-title")
        self._page_title.set_hexpand(True)
        self._page_title.set_valign(Gtk.Align.CENTER)
        self._page_sub = Gtk.Label(
            label="Reach into a territory from outside",
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
        self.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.add_css_class("page-body")
        body.set_valign(Gtk.Align.START)

        # Ordered for scan: where → which door → how → ready → act
        body.append(self._group_territory())
        body.append(self._group_doors())
        body.append(self._group_topology())
        body.append(self._path_diagram())
        body.append(self._group_stack())
        body.append(self._group_readiness())
        body.append(self._group_saved())
        body.append(self._group_actions())
        body.append(self._group_docs())

        self.append(scroll_body(body, margin=12))
        self._reload_vpn_combo()
        self._on_topo_changed()
        self._apply_territory()
        self._refresh_readiness()
        self._reload_saved()

    def _territory(self) -> Territory:
        return get_territory(self._territory_code)

    def _group_territory(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup()
        g.set_title("1 · Territory")
        g.set_description("Where you are reaching into (outside vantage).")
        self._territory_row = Adw.ComboRow(title="Target")
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
        self._page_title.set_text(f"Doors · {t.short_name}")
        self._page_sub.set_text(
            f"Outside → {t.side_label} host (inbound) or dial-out peer"
        )
        if hasattr(self, "_banner_title"):
            self._banner_title.set_text(
                f"Outside → {t.side_label} host you operate (or peer dial-out)"
            )
        if hasattr(self, "_banner_text"):
            self._banner_text.set_text(
                f"{t.blurb} Never dial {t.short_name} endpoints from clearnet."
            )
        self._doors_group.set_description(
            f"Pick one door. Inbound needs a {t.side_label} host; "
            "dial-out needs a willing peer (export client)."
        )
        self._door_inbound.set_title(f"Inbound — I have a {t.side_label} host")
        self._door_inbound.set_subtitle(
            "VPN underlay → REALITY/Proxy to that host"
        )
        self._door_reverse.set_title("Dial-out — peer dials to me")
        self._door_reverse.set_subtitle(
            "Inverse Snowflake — export client; maps SOCKS for you"
        )
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
            self._hop_group.set_title(f"{t.side_label} endpoint (after VPN)")
        if hasattr(self, "_study_row"):
            if t.study_url:
                self._study_row.set_subtitle(t.study_url)
                self._study_row.set_sensitive(True)
            else:
                self._study_row.set_subtitle(
                    "No territory study pack — same engineering model as China"
                )
        # Accept port suggestion for reverse
        if hasattr(self, "_rev_accept_port") and t.default_accept_port:
            # only nudge if still at a common default
            v = int(self._rev_accept_port.get_value())
            if v in (443, 8443, 18443):
                self._rev_accept_port.set_value(float(t.default_accept_port))
        self._set_territory_map_asset(t.silhouette_asset())

    # ── Banner ────────────────────────────────────────────────────────────

    def _banner(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        outer.add_css_class("china-ingress-banner")
        outer.set_hexpand(True)

        # Territory silhouette (CN map reused; other maps from mapsicon)
        self._territory_map = Gtk.Image()
        self._territory_map.add_css_class("reach-territory-map")
        self._territory_map.set_pixel_size(72)
        self._territory_map.set_size_request(72, 72)
        self._territory_map.set_valign(Gtk.Align.START)
        self._territory_map.set_halign(Gtk.Align.CENTER)
        outer.append(self._territory_map)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_hexpand(True)

        self._banner_title = Gtk.Label(label="", xalign=0)
        self._banner_title.add_css_class("china-ingress-banner-title")
        self._banner_title.set_wrap(True)
        box.append(self._banner_title)

        self._banner_text = Gtk.Label(label="", xalign=0, wrap=True)
        self._banner_text.add_css_class("china-ingress-banner-text")
        self._banner_text.set_max_width_chars(40)
        box.append(self._banner_text)
        outer.append(box)
        return outer

    def _set_territory_map_asset(self, filename: str | None) -> None:
        """Load data/assets silhouette into banner map image."""
        if not hasattr(self, "_territory_map"):
            return
        name = (filename or "globe.svg").strip()
        path = project_root() / "data" / "assets" / name
        if not path.is_file():
            path = project_root() / "data" / "assets" / "globe.svg"
        try:
            # 2× for HiDPI; fits 72px box
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), 144, 144)
            texture = Gdk.Texture.new_for_pixbuf(pb)
            self._territory_map.set_from_paintable(texture)
            self._territory_map.set_pixel_size(72)
        except Exception:
            try:
                self._territory_map.set_from_file(str(path))
            except Exception:
                self._territory_map.set_from_icon_name("mark-location-symbolic")

    # ── Empty-state doors ─────────────────────────────────────────────────

    def _group_doors(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup()
        self._doors_group = g
        g.set_title("2 · Door")
        g.set_description("")
        self._door_inbound = Adw.ActionRow(
            title="I already have a target-side host",
            subtitle="Composition I — VPN underlay → REALITY/Proxy to that host",
        )
        self._door_inbound.set_activatable(True)
        self._door_inbound.connect(
            "activated",
            lambda *_: self._pick_door(_TOPO_DIRECT),
        )
        try:
            self._door_inbound.add_suffix(
                Gtk.Image.new_from_icon_name("go-next-symbolic")
            )
        except Exception:
            pass
        g.add(self._door_inbound)

        self._door_reverse = Adw.ActionRow(
            title="Inverse Snowflake (peer dials out)",
            subtitle="Composition III — export client; volunteer maps SOCKS for you",
        )
        self._door_reverse.set_activatable(True)
        self._door_reverse.connect(
            "activated",
            lambda *_: self._pick_door(_TOPO_REVERSE),
        )
        try:
            self._door_reverse.add_suffix(
                Gtk.Image.new_from_icon_name("go-next-symbolic")
            )
        except Exception:
            pass
        g.add(self._door_reverse)

        pkg = Adw.ActionRow(
            title="Open Inverse Snowflake package folder",
            subtitle="run-inverse-snowflake.sh · pairing.json · INVERSE_SNOWFLAKE.md",
        )
        pkg.set_activatable(True)
        pkg.connect("activated", self._on_open_reverse_dir)
        g.add(pkg)
        return g

    def _pick_door(self, topo: int) -> None:
        self._topo.set_selected(topo)
        self._on_topo_changed()
        t = self._territory()
        if topo == _TOPO_REVERSE:
            self._toast(
                "Inverse Snowflake — set accept host, Export client, run on foothold"
            )
        else:
            self._toast(f"Inbound door — VPN then {t.short_name} REALITY/Proxy")

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
        g.set_title("3 · Topology")
        g.set_description(
            "Two open doors: inbound (China host you control) or Inverse Snowflake "
            "(peer/lab dials out — export client package). Multi-hop later."
        )

        self._topo = Adw.ComboRow(title="Composition")
        self._topo.set_model(
            Gtk.StringList.new(
                [
                    "I · Inbound — host I control in territory",
                    "II · Multi-hop machines — later",
                    "III · Inverse Snowflake — peer dials out",
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
                    "This machine (outside territory)",
                    "Research host (outside territory)",
                    "Operator host (outside territory)",
                ]
            )
        )
        self._vantage.set_selected(0)
        g.add(self._vantage)

        self._expect_probe = Adw.SwitchRow(
            title="Expect active probing",
            subtitle="Landing face should look mundane to strangers (REALITY/web cover)",
        )
        self._expect_probe.set_active(True)
        g.add(self._expect_probe)
        return g

    def _path_diagram(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.add_css_class("china-path-diagram")

        lab = Gtk.Label(label="Path sketch", xalign=0)
        lab.add_css_class("china-path-diagram-label")
        outer.append(lab)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.set_halign(Gtk.Align.CENTER)
        row.add_css_class("china-path-row")

        self._step_client = self._path_chip("You\n(outside)")
        self._step_vpn = self._path_chip("VPN\nunderlay")
        self._step_hop = self._path_chip("TLS cover\n→ CN")
        self._step_land = self._path_chip("China\nhost")

        row.append(self._step_client)
        row.append(self._path_arrow())
        row.append(self._step_vpn)
        row.append(self._path_arrow())
        row.append(self._step_hop)
        row.append(self._path_arrow())
        row.append(self._step_land)
        outer.append(row)

        self._path_caption = Gtk.Label(label="", xalign=0, wrap=True)
        self._path_caption.add_css_class("muted")
        self._path_caption.add_css_class("china-path-caption")
        outer.append(self._path_caption)
        return outer

    def _path_chip(self, text: str) -> Gtk.Label:
        lab = Gtk.Label(label=text, justify=Gtk.Justification.CENTER)
        lab.add_css_class("china-path-chip")
        lab.set_width_chars(8)
        return lab

    def _path_arrow(self) -> Gtk.Label:
        a = Gtk.Label(label="→")
        a.add_css_class("china-path-arrow")
        return a

    def _group_stack(self) -> Gtk.Widget:
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_hexpand(True)

        self._stack.add_named(self._panel_direct(), "direct")
        self._stack.add_named(self._panel_multihop(), "multihop")
        self._stack.add_named(self._panel_reverse(), "reverse")
        return self._stack

    # ── Composition I — full form ─────────────────────────────────────────

    def _panel_direct(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        # ── Required VPN underlay ─────────────────────────────────────
        vpn_g = Adw.PreferencesGroup()
        vpn_g.set_title("VPN underlay (required)")
        vpn_g.set_description(
            "A VPN must be up before any hop toward China. First hop is always "
            "WireGuard/VPN or Mullvad app SOCKS. Configure backends under Backends "
            "if the list is empty."
        )

        self._vpn_row = Adw.ComboRow(title="VPN backend")
        self._vpn_row.set_model(Gtk.StringList.new(["(none — add a VPN under Backends)"]))
        self._vpn_row.connect("notify::selected", lambda *_: self._refresh_readiness())
        vpn_g.add(self._vpn_row)

        open_vpn = Adw.ActionRow(
            title="Manage VPN backends",
            subtitle="WireGuard .conf or Mullvad SOCKS",
        )
        open_vpn.set_activatable(True)
        open_vpn.connect("activated", lambda *_: self._nav("backends"))
        vpn_g.add(open_vpn)
        box.append(vpn_g)
        self._vpn_backend_ids: list[str] = []

        hop = Adw.PreferencesGroup()
        self._hop_group = hop
        hop.set_title("Target-side endpoint (after VPN)")
        hop.set_description(
            "Second hop only — never from clearnet. Host you control in the "
            "selected territory (or peer box). Prefer REALITY. For China, see "
            "research/landing-paths (passport KYC, not HK-as-mainland)."
        )

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
        self._cover_kind.connect("notify::selected", lambda *_: self._on_cover_kind())
        hop.add(self._cover_kind)

        self._profile_name = Adw.EntryRow(title="Profile name")
        self._profile_name.set_text("Reach · China")
        self._profile_name.connect("changed", lambda *_: self._refresh_readiness())
        hop.add(self._profile_name)

        self._backend_name = Adw.EntryRow(title="Landing backend name")
        self._backend_name.set_text("Territory landing")
        self._backend_name.connect("changed", lambda *_: self._refresh_readiness())
        hop.add(self._backend_name)

        box.append(hop)

        # REALITY fields
        self._reality_group = Adw.PreferencesGroup()
        self._reality_group.set_title("REALITY parameters")
        self._reality_group.set_description(
            "Paste a vless://…reality share link or fill fields. SNI required for ingress."
        )

        import_row = Adw.ActionRow(title="Share link")
        self._vless_entry = Gtk.Entry()
        self._vless_entry.set_placeholder_text("vless://…")
        self._vless_entry.set_hexpand(True)
        self._vless_entry.set_valign(Gtk.Align.CENTER)
        import_btn = Gtk.Button(label="Import")
        import_btn.set_valign(Gtk.Align.CENTER)
        import_btn.connect("clicked", self._on_import_vless)
        import_row.add_suffix(self._vless_entry)
        import_row.add_suffix(import_btn)
        self._reality_group.add(import_row)

        self._r_server = Adw.EntryRow(title="Server (host or IP)")
        self._r_server.connect("changed", lambda *_: self._on_fields_changed())
        self._reality_group.add(self._r_server)

        self._r_port = Adw.SpinRow(
            title="Port",
            adjustment=Gtk.Adjustment(
                value=443, lower=1, upper=65535, step_increment=1, page_increment=10
            ),
        )
        self._r_port.connect("changed", lambda *_: self._refresh_readiness())
        self._reality_group.add(self._r_port)

        self._r_uuid = Adw.EntryRow(title="UUID")
        self._r_uuid.connect("changed", lambda *_: self._refresh_readiness())
        self._reality_group.add(self._r_uuid)

        self._r_pk = Adw.EntryRow(title="Public key (pbk)")
        self._r_pk.connect("changed", lambda *_: self._refresh_readiness())
        self._reality_group.add(self._r_pk)

        self._r_sid = Adw.EntryRow(title="Short ID (optional)")
        self._reality_group.add(self._r_sid)

        self._r_sni = Adw.EntryRow(title="SNI / serverName")
        self._r_sni.set_text("")
        self._r_sni.connect("changed", lambda *_: self._on_fields_changed())
        self._reality_group.add(self._r_sni)

        self._r_fp = Adw.EntryRow(title="Fingerprint")
        self._r_fp.set_text("chrome")
        self._reality_group.add(self._r_fp)

        self._r_flow = Adw.EntryRow(title="Flow")
        self._r_flow.set_text("xtls-rprx-vision")
        self._reality_group.add(self._r_flow)

        box.append(self._reality_group)

        # Proxy fields
        self._proxy_group = Adw.PreferencesGroup()
        self._proxy_group.set_title("Proxy parameters")
        self._proxy_group.set_description(
            "Only if cover is terminated outside Spectre on the landing. "
            "You must ensure the outer path is not pure high-entropy."
        )

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

        box.append(self._proxy_group)
        self._proxy_group.set_visible(False)

        notes_g = Adw.PreferencesGroup()
        notes_g.set_title("Notes")
        self._notes = Adw.EntryRow(title="Operator notes")
        notes_g.add(self._notes)
        box.append(notes_g)

        return box

    # ── Composition II / III shells ───────────────────────────────────────

    def _panel_multihop(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        banner = self._soon_banner(
            "Multi-hop (v1.1)",
            "Outside → optional outside front → middle machine(s) → China host. "
            "UI layout is fixed; connect is not wired yet.",
        )
        box.append(banner)

        g = Adw.PreferencesGroup()
        g.set_title("Hops (preview)")
        g.set_description("All machines you operate — no human broker.")

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
            "Reverse / Inverse Snowflake (Composition III)",
            "A willing host dials out to your outside accept (Snowflake inverted: "
            "volunteer capacity under CN-side routing maps SOCKS for you). "
            "Export the Inverse Snowflake client package for peers/lab/field. "
            "Spectre uses VPN → SOCKS map after the client is up.",
        )
        box.append(banner)

        # VPN underlay (shared pattern)
        vpn_g = Adw.PreferencesGroup()
        vpn_g.set_title("VPN underlay (required)")
        vpn_g.set_description(
            "Same rule as inbound: never use the reverse map from clearnet."
        )
        self._rev_vpn_row = Adw.ComboRow(title="VPN backend")
        self._rev_vpn_row.set_model(
            Gtk.StringList.new(["(none — add a VPN under Backends)"])
        )
        self._rev_vpn_row.connect(
            "notify::selected", lambda *_: self._on_fields_changed()
        )
        vpn_g.add(self._rev_vpn_row)
        open_vpn = Adw.ActionRow(
            title="Manage VPN backends",
            subtitle="WireGuard .conf or Mullvad SOCKS",
        )
        open_vpn.set_activatable(True)
        open_vpn.connect("activated", lambda *_: self._nav("backends"))
        vpn_g.add(open_vpn)
        box.append(vpn_g)
        self._rev_vpn_backend_ids: list[str] = []

        accept = Adw.PreferencesGroup()
        accept.set_title("Outside accept (agent dials here)")
        accept.set_description(
            "Host you control outside mainland. Run exported outside-accept.json "
            "with xray. Generate REALITY keys: xray x25519."
        )
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
        self._rev_sni = Adw.EntryRow(title="REALITY dest SNI (optional Xray export)")
        self._rev_sni.set_text("www.cloudflare.com")
        self._rev_sni.connect("notify::text", lambda *_: self._on_fields_changed())
        accept.add(self._rev_sni)
        self._rev_pub = Adw.EntryRow(title="REALITY public key (for agent)")
        self._rev_pub.connect("notify::text", lambda *_: self._on_fields_changed())
        accept.add(self._rev_pub)
        self._rev_priv = Adw.EntryRow(title="REALITY private key (accept only)")
        self._rev_priv.connect("notify::text", lambda *_: self._on_fields_changed())
        accept.add(self._rev_priv)
        self._rev_uuid = Adw.EntryRow(title="UUID / client id")
        self._rev_uuid.set_text(new_uuid())
        self._rev_uuid.connect("notify::text", lambda *_: self._on_fields_changed())
        accept.add(self._rev_uuid)
        self._rev_sid = Adw.EntryRow(title="Short ID (empty OK for lab REALITY)")
        self._rev_sid.set_text("")
        self._rev_sid.connect("notify::text", lambda *_: self._on_fields_changed())
        accept.add(self._rev_sid)
        self._rev_token = Adw.EntryRow(title="Pairing token")
        self._rev_token.set_text(new_pairing_token())
        self._rev_token.connect("notify::text", lambda *_: self._on_fields_changed())
        accept.add(self._rev_token)
        gen_row = Adw.ActionRow(
            title="Regenerate UUID / shortId / token",
            subtitle="Does not rotate REALITY x25519 keys — run xray x25519 for those",
        )
        gen_btn = Gtk.Button(label="Regenerate")
        gen_btn.set_valign(Gtk.Align.CENTER)
        gen_btn.connect("clicked", self._on_rev_regen)
        gen_row.add_suffix(gen_btn)
        accept.add(gen_row)
        box.append(accept)

        socks = Adw.PreferencesGroup()
        socks.set_title("SOCKS map (Spectre hop)")
        socks.set_description(
            "After agent connects, accept exposes this SOCKS. Point Spectre here "
            "(127.0.0.1 if accept is local; SSH -L if accept is a remote VPS)."
        )
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
        self._rev_notes = Adw.EntryRow(title="Operator notes")
        names.add(self._rev_notes)
        box.append(names)

        export = Adw.PreferencesGroup()
        export.set_title("Inverse Snowflake package")
        export.set_description(
            "Writes accept + Inverse Snowflake client under "
            "~/.local/share/reach/reverse/. Give the client folder to a "
            "peer/lab/field host; run accept on your outside box (or origin VPS)."
        )
        exp_row = Adw.ActionRow(
            title="Export Inverse Snowflake package",
            subtitle="pairing.json · spectre-inverse-snowflake.py · RUNBOOK · Xray optional",
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

    # ── Readiness ─────────────────────────────────────────────────────────

    def _group_readiness(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        head = Gtk.Label(label="4 · Readiness", xalign=0)
        head.add_css_class("china-section-title")
        outer.append(head)
        sub = Gtk.Label(
            label="Structural checks for Composition I (inbound) or III (Inverse Snowflake).",
            xalign=0,
            wrap=True,
        )
        sub.add_css_class("muted")
        outer.append(sub)

        self._ready_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self._ready_list.add_css_class("china-ready-list")
        outer.append(self._ready_list)

        self._ready_summary = Gtk.Label(label="", xalign=0, wrap=True)
        self._ready_summary.add_css_class("china-ready-summary")
        outer.append(self._ready_summary)
        return outer

    def _ready_item(self, ok: bool, text: str) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        mark = Gtk.Label(label="✓" if ok else "○")
        mark.add_css_class("china-ready-mark")
        mark.add_css_class("china-ready-ok" if ok else "china-ready-pending")
        lab = Gtk.Label(label=text, xalign=0, wrap=True)
        lab.add_css_class("china-ready-text")
        lab.add_css_class("china-ready-ok" if ok else "china-ready-pending")
        lab.set_hexpand(True)
        row.append(mark)
        row.append(lab)
        return row

    # ── Saved profiles ────────────────────────────────────────────────────

    def _group_saved(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._saved_head = Gtk.Label(label="Saved Reach profiles", xalign=0)
        self._saved_head.add_css_class("china-section-title")
        outer.append(self._saved_head)

        self._saved_empty = Gtk.Label(
            label="No ingress profiles yet. Save from inbound or Inverse Snowflake below.",
            xalign=0,
            wrap=True,
        )
        self._saved_empty.add_css_class("muted")
        outer.append(self._saved_empty)

        self._saved_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._saved_list.add_css_class("china-saved-list")
        outer.append(self._saved_list)
        return outer

    # ── Actions ───────────────────────────────────────────────────────────

    def _group_actions(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup()
        g.set_title("5 · Actions")
        g.set_description(
            "Save writes backend + profile with territory=XX. "
            "Inbound: path_intent=ingress_territory. "
            "Inverse Snowflake: ingress_territory_reverse + Export client. "
            "Connect uses the normal spectred handoff when readiness passes."
        )

        save_row = Adw.ActionRow(
            title="Save backend & profile",
            subtitle="Persists under Backends and Profiles",
        )
        self._save_btn = Gtk.Button(label="Save")
        self._save_btn.add_css_class("suggested-action")
        self._save_btn.set_valign(Gtk.Align.CENTER)
        self._save_btn.connect("clicked", self._on_save)
        save_row.add_suffix(self._save_btn)
        g.add(save_row)

        probe_row = Adw.ActionRow(
            title="Probe landing (TCP)",
            subtitle="Outside vantage only — does not prove inside-CN paths",
        )
        self._probe_btn = Gtk.Button(label="Probe")
        self._probe_btn.set_valign(Gtk.Align.CENTER)
        self._probe_btn.connect("clicked", self._on_probe)
        probe_row.add_suffix(self._probe_btn)
        g.add(probe_row)

        conn_row = Adw.ActionRow(
            title="Connect",
            subtitle="Selects saved profile and hands off to core",
        )
        self._connect_btn = Gtk.Button(label="Connect")
        self._connect_btn.add_css_class("suggested-action")
        self._connect_btn.set_valign(Gtk.Align.CENTER)
        self._connect_btn.connect("clicked", self._on_connect)
        conn_row.add_suffix(self._connect_btn)
        g.add(conn_row)

        disc_row = Adw.ActionRow(
            title="Disconnect",
            subtitle="Tear down active Spectre path",
        )
        self._disc_btn = Gtk.Button(label="Disconnect")
        self._disc_btn.set_valign(Gtk.Align.CENTER)
        self._disc_btn.connect("clicked", self._on_disconnect)
        disc_row.add_suffix(self._disc_btn)
        g.add(disc_row)

        return g

    def _group_docs(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup()
        g.set_title("Architecture")
        g.set_description(f"Plan: {IMPL_PLAN}")

        self._study_row = Adw.ActionRow(
            title="Territory research study",
            subtitle=STUDY_URL_CN,
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

        open_backends = Adw.ActionRow(
            title="Open Backends",
            subtitle="Edit any adapter in the full editor",
        )
        open_backends.set_activatable(True)
        open_backends.connect("activated", lambda *_: self._nav("backends"))
        g.add(open_backends)

        open_profiles = Adw.ActionRow(
            title="Open Profiles",
            subtitle="All path profiles including Reach · territory",
        )
        open_profiles.set_activatable(True)
        open_profiles.connect("activated", lambda *_: self._nav("profiles"))
        g.add(open_profiles)
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
            self._path_caption.set_text(
                "Outside → front? → middle → China host → service (v1.1)."
            )
            self._set_path_active(multi=True)
        elif idx == _TOPO_REVERSE:
            self._stack.set_visible_child_name("reverse")
            self._step_hop.set_text("Accept\n(outside)")
            self._step_land.set_text("Inverse\nSnowflake")
            self._path_caption.set_text(
                "Inverse Snowflake: peer client dials out → your accept → "
                "you use VPN → SOCKS map (Export package for the peer)."
            )
            self._set_path_active(reverse=True)
            self._reload_vpn_combo()
        else:
            self._stack.set_visible_child_name("direct")
            self._step_hop.set_text("TLS cover\n→ CN")
            self._step_land.set_text("China\nhost")
            self._path_caption.set_text(
                "Composition I: you → VPN underlay → TLS cover → China host."
            )
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
        for chip in chips:
            chip.remove_css_class("china-path-chip-active")
        if direct or multi:
            for c in chips:
                c.add_css_class("china-path-chip-active")
        elif reverse:
            for c in chips:
                c.add_css_class("china-path-chip-active")

    def _on_cover_kind(self) -> None:
        proxy = int(self._cover_kind.get_selected()) == 1
        self._reality_group.set_visible(not proxy)
        self._proxy_group.set_visible(proxy)
        self._on_fields_changed()

    def _on_fields_changed(self) -> None:
        self._refresh_readiness()
        self._update_action_sensitivity()

    def _is_direct_v1(self) -> bool:
        return int(self._topo.get_selected()) == _TOPO_DIRECT

    def _is_reverse(self) -> bool:
        return int(self._topo.get_selected()) == _TOPO_REVERSE

    def _is_wireable(self) -> bool:
        return self._is_direct_v1() or self._is_reverse()

    def _use_proxy(self) -> bool:
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
                (False, "Multi-hop is not wired yet — use inbound or reverse"),
                (True, "Composition II UI is preview-only"),
            ]
        if self._is_reverse():
            return self._collect_checks_reverse()
        return self._collect_checks_inbound()

    def _collect_checks_inbound(self) -> list[tuple[bool, str]]:
        checks: list[tuple[bool, str]] = []
        vpn = self._selected_vpn_backend()
        if vpn is None:
            checks.append((False, "VPN underlay selected"))
        else:
            checks.append((True, f"VPN underlay: {vpn.label()}"))
            checks.append((vpn.enabled, "VPN underlay enabled"))
            checks.append(
                (
                    vpn.is_configured(),
                    "VPN underlay complete (WireGuard .conf or Mullvad SOCKS)",
                )
            )

        pname = (self._profile_name.get_text() or "").strip()
        bname = (self._backend_name.get_text() or "").strip()
        checks.append((bool(pname), "Profile name set"))
        checks.append((bool(bname), "China backend name set"))

        if self._use_proxy():
            host = (self._p_host.get_text() or "").strip()
            port = int(self._p_port.get_value())
            checks.append((bool(host), "China proxy host set"))
            checks.append((port > 0, f"China proxy port {port}"))
            checks.append(
                (True, "You confirm China hop cover is not pure high-entropy"),
            )
        else:
            server = (self._r_server.get_text() or "").strip()
            uuid_s = (self._r_uuid.get_text() or "").strip()
            pk = (self._r_pk.get_text() or "").strip()
            sni = (self._r_sni.get_text() or "").strip()
            port = int(self._r_port.get_value())
            checks.append((bool(server), "China REALITY server set"))
            checks.append((port > 0, f"China hop port {port}"))
            checks.append((bool(uuid_s), "UUID set"))
            checks.append((bool(pk), "Public key set"))
            checks.append((bool(sni), "SNI set (required for ingress cover)"))
        checks.append((True, "Never dial China from clearnet (VPN first)"))
        checks.append((True, "China host is operator-owned"))
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

        checks.append((bool(host), "Outside accept host set"))
        checks.append((int(self._rev_accept_port.get_value()) > 0, "Accept port set"))
        checks.append((bool(sni), "REALITY dest SNI set (agent cover)"))
        checks.append(
            (bool(pub) or bool((self._rev_priv.get_text() or "").strip()),
             "REALITY key material (public for agent and/or private for accept)"),
        )
        checks.append((bool(uuid_s), "UUID / pairing id set"))
        checks.append((bool(map_host), "SOCKS map host set"))
        checks.append((map_port > 0, f"SOCKS map port {map_port}"))
        checks.append((bool(pname), "Profile name set"))
        checks.append((bool(bname), "Map backend name set"))
        checks.append((True, "Agent dials out with TCP REALITY (not bare frp)"))
        checks.append((True, "Foothold is willing peer/lab/field"))
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
        if not hasattr(self, "_ready_list"):
            return
        clear_box(self._ready_list)
        checks = self._collect_checks()
        for ok, text in checks:
            self._ready_list.append(self._ready_item(ok, text))
        if self._is_wireable() and self._readiness_ok():
            if self._is_reverse():
                self._ready_summary.set_text(
                    "Ready to save Inverse Snowflake profile. Export client package; "
                    "start accept + Inverse Snowflake on M before Connect."
                )
            else:
                self._ready_summary.set_text(
                    "Ready to save. Connect needs a saved profile + core."
                )
            self._ready_summary.add_css_class("china-ready-ok")
            self._ready_summary.remove_css_class("china-ready-pending")
        elif self._is_wireable():
            self._ready_summary.set_text("Fill required fields to enable Save.")
            self._ready_summary.add_css_class("china-ready-pending")
            self._ready_summary.remove_css_class("china-ready-ok")
        else:
            self._ready_summary.set_text(
                "Multi-hop later — use inbound or Inverse Snowflake for Connect."
            )
            self._ready_summary.add_css_class("china-ready-pending")
            self._ready_summary.remove_css_class("china-ready-ok")
        self._update_action_sensitivity()

    def _update_action_sensitivity(self) -> None:
        wire = self._is_wireable()
        ok = self._readiness_ok()
        if hasattr(self, "_save_btn"):
            self._save_btn.set_sensitive(wire and ok and not self._action_busy)
        if hasattr(self, "_probe_btn"):
            self._probe_btn.set_sensitive(wire and not self._probe_busy)
        if hasattr(self, "_connect_btn"):
            has_profile = bool(self._bound_profile_id) or self._has_ingress_profile()
            self._connect_btn.set_sensitive(
                wire and has_profile and not self._action_busy
            )
        st = self._services.core.status()
        if hasattr(self, "_disc_btn"):
            self._disc_btn.set_sensitive(
                st.state in (CoreState.CONNECTED, CoreState.CONNECTING)
                and not self._action_busy
            )

    def _has_ingress_profile(self) -> bool:
        return any(
            is_any_ingress_intent(p.path_intent, p.notes, p.name)
            for p in self._services.profiles.list()
        )

    def _landing_host_port(self) -> tuple[str, int] | None:
        if self._is_reverse():
            host = (self._rev_map_host.get_text() or "").strip()
            port = int(self._rev_map_port.get_value())
            if not host or port <= 0:
                return None
            return host, port
        if not self._is_direct_v1():
            return None
        if self._use_proxy():
            host = (self._p_host.get_text() or "").strip()
            port = int(self._p_port.get_value())
        else:
            host = (self._r_server.get_text() or "").strip()
            port = int(self._r_port.get_value())
        if not host or port <= 0:
            return None
        return host, port

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
        self._on_cover_kind()
        self._r_server.set_text(data.get("reality_server") or "")
        self._r_port.set_value(int(data.get("reality_port") or 443))
        self._r_uuid.set_text(data.get("reality_uuid") or "")
        self._r_pk.set_text(data.get("reality_public_key") or "")
        self._r_sid.set_text(data.get("reality_short_id") or "")
        self._r_sni.set_text(data.get("reality_sni") or "")
        self._r_fp.set_text(data.get("reality_fingerprint") or "chrome")
        self._r_flow.set_text(data.get("reality_flow") or "xtls-rprx-vision")
        name = (data.get("name") or "").strip()
        if name:
            self._backend_name.set_text(name)
            cur = (self._profile_name.get_text() or "").strip()
            if not cur or cur.startswith("Reach China") or cur.startswith("Reach ·"):
                self._profile_name.set_text(f"Reach · {self._territory().short_name} · {name}")
        self._toast("Imported REALITY parameters")
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
        self._toast(f"Saved “{profile.name}” ({kind})")
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
        notes = (self._rev_notes.get_text() or "").strip()
        note_bits = [notes] if notes else []
        note_bits.append(
            f"path_intent={PATH_INTENT_REVERSE} · composition=reverse · "
            f"territory={t.code} · "
            f"accept={pairing.accept_host}:{pairing.accept_port} · "
            "Inverse Snowflake dial-out · VPN underlay required"
        )
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
        notes = (self._notes.get_text() or "").strip()
        note_bits = [notes] if notes else []
        note_bits.append(
            f"path_intent={PATH_INTENT_INBOUND} · composition=inbound · "
            f"territory={t.code} · VPN underlay required · "
            f"operator-owned {t.side_label} host"
        )

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

    def _on_probe(self, *_a) -> None:
        hp = self._landing_host_port()
        if not hp:
            self._toast("Set server/host and port first")
            return
        host, port = hp
        if self._probe_busy:
            return
        self._probe_busy = True
        self._probe_btn.set_sensitive(False)
        self._probe_btn.set_label("Probing…")
        label = "SOCKS map" if self._is_reverse() else "landing"
        self._toast(f"Probing {label} {host}:{port} (outside vantage)…")

        def worker() -> None:
            ok = False
            err = ""
            try:
                with socket.create_connection((host, port), timeout=5.0):
                    ok = True
            except OSError as exc:
                err = str(exc) or repr(exc)

            def done() -> bool:
                self._probe_busy = False
                self._probe_btn.set_label("Probe")
                self._update_action_sensitivity()
                if ok:
                    self._toast(f"TCP open {host}:{port} (this vantage only)")
                else:
                    self._toast(f"Probe failed: {err}")
                return False

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_connect(self, *_a) -> None:
        if self._action_busy:
            return
        # Prefer bound profile matching current topology, else any ingress
        pid = self._bound_profile_id
        want_reverse = self._is_reverse()
        if not pid or self._services.profiles.get(pid) is None:
            for p in self._services.profiles.list():
                if want_reverse and is_reverse_intent(p.path_intent, p.notes):
                    pid = p.id
                    break
                if not want_reverse and is_any_ingress_intent(
                    p.path_intent, p.notes, p.name
                ) and not is_reverse_intent(p.path_intent, p.notes):
                    pid = p.id
                    break
            if not pid:
                for p in self._services.profiles.list():
                    if is_any_ingress_intent(p.path_intent, p.notes, p.name):
                        pid = p.id
                        break
        if not pid or self._services.profiles.get(pid) is None:
            self._toast("Save a Reach profile first")
            return

        self._services.config.last_profile_id = pid
        try:
            self._services.save_config()
        except Exception:
            pass
        profile = self._services.profiles.get(pid)
        if profile:
            self._services.core.set_selected_profile(profile.name)

        self._action_busy = True
        self._connect_btn.set_sensitive(False)
        self._connect_btn.set_label("Connecting…")
        mode = "reverse" if (profile and is_reverse_intent(profile.path_intent, profile.notes)) else "inbound"
        self._toast(f"Connecting ({mode} profile)…")

        def worker() -> None:
            err: str | None = None
            toast = ""
            try:
                status, ready = self._services.connect_active()
                if not ready.ok:
                    toast = ready.summary or "Not ready"
                elif status is None:
                    toast = "Connect failed"
                elif status.state == CoreState.CONNECTED:
                    toast = (
                        f"Path up (outside vantage) — Reach · "
                        f"{self._territory().short_name} ({mode})"
                    )
                else:
                    toast = status.message or status.state.value
            except Exception as exc:
                err = str(exc) or repr(exc)

            def done() -> bool:
                self._action_busy = False
                self._connect_btn.set_label("Connect")
                self._update_action_sensitivity()
                self._toast(err or toast)
                if self._on_changed:
                    self._on_changed()
                return False

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_disconnect(self, *_a) -> None:
        if self._action_busy:
            return
        self._action_busy = True
        self._disc_btn.set_sensitive(False)
        self._disc_btn.set_label("…")

        def worker() -> None:
            toast = ""
            err: str | None = None
            try:
                _st, toast = self._services.disconnect()
            except Exception as exc:
                err = str(exc) or repr(exc)

            def done() -> bool:
                self._action_busy = False
                self._disc_btn.set_label("Disconnect")
                self._update_action_sensitivity()
                self._toast(err or toast or "Disconnected")
                if self._on_changed:
                    self._on_changed()
                return False

            GLib.idle_add(done)

        threading.Thread(target=worker, daemon=True).start()

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
            use = Gtk.Button(label="Use")
            use.add_css_class("flat")
            use.connect("clicked", self._on_use_profile, p.id)
            row.append(use)
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
        self._toast(f"Using “{profile.name}”")
        self._refresh_readiness()
        self._update_action_sensitivity()

    def _load_backend_into_form(self, b: Backend) -> None:
        self._backend_name.set_text(b.name)
        if b.kind == "Proxy":
            self._cover_kind.set_selected(1)
            self._on_cover_kind()
            if b.proxy_protocol in PROXY_PROTOCOLS:
                self._p_proto.set_selected(PROXY_PROTOCOLS.index(b.proxy_protocol))
            self._p_host.set_text(b.proxy_host or "")
            self._p_port.set_value(b.proxy_port or 1080)
            self._p_user.set_text(b.proxy_username or "")
            self._p_pass.set_text(b.proxy_password or "")
        else:
            self._cover_kind.set_selected(0)
            self._on_cover_kind()
            self._r_server.set_text(b.reality_server or "")
            self._r_port.set_value(b.reality_port or 443)
            self._r_uuid.set_text(b.reality_uuid or "")
            self._r_pk.set_text(b.reality_public_key or "")
            self._r_sid.set_text(b.reality_short_id or "")
            self._r_sni.set_text(b.reality_sni or "")
            self._r_fp.set_text(b.reality_fingerprint or "chrome")
            self._r_flow.set_text(b.reality_flow or "xtls-rprx-vision")
        if b.notes:
            # strip our annotation for display
            note = b.notes.split("path_intent=")[0].strip(" ·")
            self._notes.set_text(note)

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
