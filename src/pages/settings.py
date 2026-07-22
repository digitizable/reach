"""Settings — dashboard of section tiles; each opens a sub-page."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from gi.repository import Adw, Gtk

from app_config import APPLICATION_VERSION, GITHUB_URL
from core.client import default_socket_path
from core.updates import DEFAULT_CHECK_INTERVAL_HOURS
from services import Services
from widgets.chrome import scroll_body
from widgets.transitions import SUBPAGE_MS, slide_stack


@dataclass(frozen=True)
class _Section:
    id: str
    title: str
    subtitle: str
    icon: str
    # Optional brand asset under data/assets/ (SVG/PNG) instead of icon theme
    asset: str = ""


_SECTIONS: tuple[_Section, ...] = (
    _Section(
        "plugins",
        "Plugins",
        "Privacy · Lab · Operate posture",
        "application-x-addon-symbolic",
    ),
    _Section(
        "core",
        "Spectre core",
        "Socket · token · reconnect",
        "network-server-symbolic",
        asset="spectre.svg",
    ),
    _Section(
        "session",
        "Session",
        "Startup · tray · notifications",
        "system-run-symbolic",
    ),
    _Section(
        "network",
        "Network",
        "Routing · kill switch · DNS",
        "network-workgroup-symbolic",
    ),
    _Section(
        "privacy",
        "Privacy",
        "WebRTC · path gate · UDP policy",
        "security-high-symbolic",
    ),
    _Section(
        "mullvad",
        "Mullvad",
        "Optional underlay control",
        "network-vpn-symbolic",
        asset="mullvad.png",
    ),
    _Section(
        "updates",
        "Updates",
        "GitHub release checks",
        "software-update-available-symbolic",
    ),
    _Section(
        "logging",
        "Logging",
        "Level · file output",
        "utilities-terminal-symbolic",
    ),
    _Section(
        "advanced",
        "Advanced",
        "Bind · MTU",
        "preferences-other-symbolic",
    ),
)


class SettingsPage(Gtk.Box):
    def __init__(
        self,
        services: Services,
        *,
        parent_window: Gtk.Window | None = None,
        on_toast: Callable[[str], None] | None = None,
        on_check_updates: Callable[[], None] | None = None,
        on_plugins_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("page")
        self.add_css_class("settings-page")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._services = services
        self._parent_window = parent_window
        self._on_toast = on_toast
        self._on_check_updates = on_check_updates
        self._on_plugins_changed = on_plugins_changed
        self._cfg = services.config
        self._section_pages: dict[str, Gtk.Widget] = {}
        self._plugin_switches: dict[str, Adw.SwitchRow] = {}

        self._view = slide_stack(
            duration_ms=SUBPAGE_MS,
            left_right=True,
            hhomogeneous=True,
            vhomogeneous=True,
            css_class="settings-view-stack",
        )
        self._view.set_hexpand(True)
        self._view.set_vexpand(True)

        # Build preference groups first so Save always has live widgets
        groups = {
            "plugins": self._group_plugins(),
            "core": self._group_core(),
            "session": self._group_session(),
            "network": self._group_network(),
            "privacy": self._group_privacy(),
            "mullvad": self._group_mullvad(),
            "updates": self._group_updates(),
            "logging": self._group_logging(),
            "advanced": self._group_advanced(),
        }

        self._view.add_named(self._build_dashboard(), "main")
        for sec in _SECTIONS:
            page = self._build_section_page(sec, groups[sec.id])
            self._section_pages[sec.id] = page
            self._view.add_named(page, sec.id)

        self.append(self._view)

    # ── Dashboard ─────────────────────────────────────────────────

    def _build_dashboard(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.add_css_class("settings-dashboard")
        page.set_hexpand(True)
        page.set_vexpand(True)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("pane-header")
        header.set_hexpand(True)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        titles.set_hexpand(True)
        titles.set_valign(Gtk.Align.CENTER)
        t = Gtk.Label(label="Settings", xalign=0)
        t.add_css_class("pane-header-title")
        titles.append(t)
        sub = Gtk.Label(
            label="Pick a category",
            xalign=0,
        )
        sub.add_css_class("pane-header-sub")
        titles.append(sub)
        header.append(titles)

        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.set_valign(Gtk.Align.CENTER)
        save.connect("clicked", self._on_save)
        header.append(save)
        page.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.add_css_class("settings-dash-body")
        body.set_halign(Gtk.Align.CENTER)
        body.set_hexpand(True)

        grid = Gtk.FlowBox()
        grid.add_css_class("settings-tile-grid")
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_max_children_per_line(2)
        grid.set_min_children_per_line(1)
        grid.set_homogeneous(True)
        grid.set_column_spacing(12)
        grid.set_row_spacing(12)
        grid.set_hexpand(True)
        grid.set_valign(Gtk.Align.START)

        for sec in _SECTIONS:
            cell = Gtk.FlowBoxChild()
            cell.set_child(self._make_tile(sec))
            grid.append(cell)
        body.append(grid)

        page.append(scroll_body(body, margin=20))
        return page

    def _icon_badge(
        self,
        icon_name: str,
        *,
        size: int = 28,
        privacy: bool = False,
        section: bool = False,
        asset: str = "",
    ) -> Gtk.Widget:
        """Square badge with the icon centered on both axes."""
        wrap = Gtk.CenterBox()
        wrap.add_css_class(
            "settings-section-icon-wrap" if section else "settings-tile-icon-wrap"
        )
        if privacy:
            wrap.add_css_class("settings-tile-icon-privacy")
        side = 40 if section else 48
        wrap.set_size_request(side, side)
        wrap.set_halign(Gtk.Align.CENTER)
        wrap.set_valign(Gtk.Align.CENTER)

        ic = self._load_icon_image(icon_name, size=size, asset=asset)
        wrap.set_center_widget(ic)
        return wrap

    def _load_icon_image(
        self, icon_name: str, *, size: int, asset: str = ""
    ) -> Gtk.Image:
        """Theme icon, or brand SVG/PNG from data/assets (e.g. Spectre, Mullvad)."""
        if asset:
            from pathlib import Path

            from gi.repository import Gdk, GdkPixbuf, GLib

            from app_config import project_root

            assets_dir = project_root() / "data" / "assets"
            candidates: list[Path] = []
            primary = assets_dir / asset
            candidates.append(primary)
            if asset.endswith(".svg"):
                candidates.append(primary.with_suffix(".png"))
            elif asset.endswith(".png"):
                candidates.append(primary.with_suffix(".svg"))

            scale = 1
            display = Gdk.Display.get_default()
            if display is not None:
                mons = display.get_monitors()
                if mons.get_n_items() > 0:
                    mon = mons.get_item(0)
                    if mon is not None:
                        scale = max(1, int(mon.get_scale_factor()))

            asset_l = asset.lower()
            for path in candidates:
                if not path.is_file():
                    continue
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_size(
                        str(path), size * scale, size * scale
                    )
                except GLib.Error:
                    continue
                # Monochrome brand marks: recolor to match symbolic icons (#c7d4ee)
                if "spectre" in asset_l or "mullvad" in asset_l:
                    pb = self._recolor_mono_pixbuf(pb, 199, 212, 238)
                texture = Gdk.Texture.new_for_pixbuf(pb)
                ic = Gtk.Image.new_from_paintable(texture)
                ic.set_pixel_size(size)
                ic.set_size_request(size, size)
                ic.add_css_class("settings-tile-icon")
                if "mullvad" in asset_l:
                    # Same mark as path Mullvad SOCKS; themed for Settings
                    ic.add_css_class("settings-tile-icon-mullvad")
                    ic.add_css_class("path-node-icon-mullvad")
                elif "spectre" in asset_l:
                    ic.add_css_class("settings-tile-icon-spectre")
                else:
                    ic.add_css_class("settings-tile-icon-asset")
                ic.set_halign(Gtk.Align.CENTER)
                ic.set_valign(Gtk.Align.CENTER)
                return ic

        ic = Gtk.Image.new_from_icon_name(icon_name)
        ic.set_pixel_size(size)
        ic.add_css_class("settings-tile-icon")
        ic.set_halign(Gtk.Align.CENTER)
        ic.set_valign(Gtk.Align.CENTER)
        return ic

    @staticmethod
    def _recolor_mono_pixbuf(pb, r: int, g: int, b: int):
        """Tint opaque pixels of a monochrome mark to (r,g,b), keep alpha."""
        from gi.repository import GdkPixbuf

        # Work on RGBA
        if pb.get_n_channels() < 4 or not pb.get_has_alpha():
            pb = pb.add_alpha(False, 0, 0, 0)
        pb = pb.copy()
        w, h = pb.get_width(), pb.get_height()
        n = pb.get_n_channels()
        rowstride = pb.get_rowstride()
        pixels = pb.get_pixels()
        # get_pixels() may be read-only memoryview — write via bytearray
        buf = bytearray(pixels)
        for y in range(h):
            row = y * rowstride
            for x in range(w):
                i = row + x * n
                a = buf[i + 3] if n >= 4 else 255
                if a < 8:
                    continue
                # Preserve anti-aliasing: scale theme color by source luminance
                src = buf[i]
                # source is white-ish glyph; use alpha as coverage
                buf[i] = r
                buf[i + 1] = g
                buf[i + 2] = b
                # keep original alpha (src unused but available for future)
                _ = src
        # Build new pixbuf from buffer
        return GdkPixbuf.Pixbuf.new_from_data(
            bytes(buf),
            GdkPixbuf.Colorspace.RGB,
            True,
            8,
            w,
            h,
            rowstride,
        )

    def _make_tile(self, sec: _Section) -> Gtk.Widget:
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.add_css_class("settings-tile")
        btn.set_hexpand(True)
        btn.connect("clicked", lambda *_: self._show_section(sec.id))

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.set_halign(Gtk.Align.FILL)

        icon_wrap = self._icon_badge(
            sec.icon,
            size=28,
            privacy=sec.id == "privacy",
            asset=sec.asset,
        )
        icon_wrap.set_valign(Gtk.Align.CENTER)
        row.append(icon_wrap)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        col.set_hexpand(True)
        col.set_valign(Gtk.Align.CENTER)
        title = Gtk.Label(label=sec.title, xalign=0)
        title.add_css_class("settings-tile-title")
        col.append(title)
        sub = Gtk.Label(label=sec.subtitle, xalign=0, wrap=True)
        sub.add_css_class("settings-tile-sub")
        col.append(sub)
        row.append(col)

        chev = Gtk.Image.new_from_icon_name("go-next-symbolic")
        chev.set_pixel_size(14)
        chev.add_css_class("settings-tile-chev")
        chev.set_valign(Gtk.Align.CENTER)
        row.append(chev)

        btn.set_child(row)
        return btn

    def _build_section_page(self, sec: _Section, group: Gtk.Widget) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.add_css_class("settings-section-page")
        page.set_hexpand(True)
        page.set_vexpand(True)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.add_css_class("pane-header")
        header.set_hexpand(True)

        back = Gtk.Button()
        back.add_css_class("flat")
        back.add_css_class("circular")
        back.set_icon_name("go-previous-symbolic")
        back.set_tooltip_text("Back to Settings")
        back.set_valign(Gtk.Align.CENTER)
        back.connect("clicked", self._show_main)
        header.append(back)

        icon_wrap = self._icon_badge(
            sec.icon,
            size=22,
            privacy=sec.id == "privacy",
            section=True,
            asset=sec.asset,
        )
        icon_wrap.set_valign(Gtk.Align.CENTER)
        header.append(icon_wrap)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        titles.set_hexpand(True)
        titles.set_valign(Gtk.Align.CENTER)
        t = Gtk.Label(label=sec.title, xalign=0)
        t.add_css_class("pane-header-title")
        titles.append(t)
        s = Gtk.Label(label=sec.subtitle, xalign=0)
        s.add_css_class("pane-header-sub")
        titles.append(s)
        header.append(titles)

        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.set_valign(Gtk.Align.CENTER)
        save.connect("clicked", self._on_save)
        header.append(save)
        page.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.add_css_class("settings-section-body")
        body.set_halign(Gtk.Align.CENTER)
        body.set_hexpand(True)
        body.set_size_request(480, -1)
        body.append(group)

        done_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        done_row.set_halign(Gtk.Align.CENTER)
        done_row.set_margin_top(8)
        back2 = Gtk.Button(label="Back to dashboard")
        back2.add_css_class("flat")
        back2.connect("clicked", self._show_main)
        done_row.append(back2)
        body.append(done_row)

        page.append(scroll_body(body, margin=20))
        return page

    def _show_main(self, *_a) -> None:
        self._view.set_visible_child_name("main")

    def show_section(self, section_id: str) -> None:
        """Open a settings category (e.g. ``plugins``) or dashboard (``main``)."""
        sid = (section_id or "main").strip()
        if sid in ("", "main", "hub", "home", "dashboard"):
            self._view.set_visible_child_name("main")
        elif sid in self._section_pages:
            self._view.set_visible_child_name(sid)
        else:
            self._view.set_visible_child_name("main")

    def _show_section(self, section_id: str) -> None:
        if self._view.get_child_by_name(section_id) is not None:
            self._view.set_visible_child_name(section_id)

    # ── Preference groups (unchanged field model) ─────────────────

    def _group_core(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup()
        g.set_title("Spectre core")
        g.set_description(
            "How Reach talks to the path engine (spectred). "
            f"Default socket: {default_socket_path()}"
        )

        self._socket = Adw.EntryRow(title="Socket path (empty = default)")
        self._socket.set_text(self._cfg.core_socket)
        if hasattr(self._socket, "set_show_apply_button"):
            self._socket.set_show_apply_button(False)
        g.add(self._socket)

        self._token = Adw.PasswordEntryRow(title="API token")
        self._token.set_text(self._cfg.api_token)
        g.add(self._token)

        self._timeout = Adw.SpinRow(
            title="Request timeout",
            subtitle="Seconds to wait for core replies",
            adjustment=Gtk.Adjustment(
                value=self._cfg.core_timeout_sec,
                lower=1,
                upper=120,
                step_increment=1,
                page_increment=5,
            ),
        )
        g.add(self._timeout)

        self._reconnect = Adw.SwitchRow(
            title="Auto-reconnect",
            subtitle="Retry the path if the core drops the session",
        )
        self._reconnect.set_active(self._cfg.reconnect_auto)
        g.add(self._reconnect)

        self._reconnect_delay = Adw.SpinRow(
            title="Reconnect delay",
            subtitle="Seconds between reconnect attempts",
            adjustment=Gtk.Adjustment(
                value=self._cfg.reconnect_delay_sec,
                lower=1,
                upper=60,
                step_increment=1,
                page_increment=5,
            ),
        )
        g.add(self._reconnect_delay)
        return g

    def _group_session(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup()
        g.set_title("Session")
        g.set_description(
            "Startup and path selection. Path policy is applied when you Connect."
        )

        self._auto_connect = Adw.SwitchRow(
            title="Connect on launch",
            subtitle="Bring up the last profile when the app starts (core required)",
        )
        self._auto_connect.set_active(self._cfg.auto_connect)
        g.add(self._auto_connect)

        self._start_min = Adw.SwitchRow(
            title="Start minimized",
            subtitle="Open in the background / tray when available",
        )
        self._start_min.set_active(self._cfg.start_minimized)
        g.add(self._start_min)

        self._tray = Adw.SwitchRow(
            title="Show tray icon",
            subtitle="Panel applet for status and quick Connect / Disconnect",
        )
        self._tray.set_active(self._cfg.tray_enabled)
        g.add(self._tray)

        self._close_tray = Adw.SwitchRow(
            title="Close to tray",
            subtitle="Window close hides Spectre; quit from the tray menu",
        )
        self._close_tray.set_active(self._cfg.close_to_tray)
        g.add(self._close_tray)

        self._notify = Adw.SwitchRow(
            title="Notify on disconnect",
            subtitle="Desktop notification when the path goes down",
        )
        self._notify.set_active(self._cfg.notify_on_disconnect)
        g.add(self._notify)
        return g

    def _group_updates(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup()
        g.set_title("Updates")
        g.set_description(
            f"Check GitHub Releases for Reach "
            f"({GITHUB_URL}). Current version: {APPLICATION_VERSION}"
        )

        self._check_updates = Adw.SwitchRow(
            title="Check for updates automatically",
            subtitle="Query GitHub in the background on a schedule (no auto-install)",
        )
        self._check_updates.set_active(self._cfg.check_for_updates)
        g.add(self._check_updates)

        interval = self._cfg.update_check_interval_hours or DEFAULT_CHECK_INTERVAL_HOURS
        self._update_interval = Adw.SpinRow(
            title="Check interval",
            subtitle="Hours between automatic checks",
            adjustment=Gtk.Adjustment(
                value=max(1, int(interval)),
                lower=1,
                upper=168,
                step_increment=1,
                page_increment=24,
            ),
        )
        g.add(self._update_interval)

        check_row = Adw.ActionRow(title="Check now")
        last = (self._cfg.last_update_check or "").strip()
        check_row.set_subtitle(
            f"Last check: {last}" if last else "Not checked yet this install"
        )
        self._last_check_row = check_row
        btn = Gtk.Button(label="Check")
        btn.set_valign(Gtk.Align.CENTER)
        btn.add_css_class("suggested-action")
        btn.connect("clicked", self._on_check_now)
        check_row.add_suffix(btn)
        check_row.set_activatable_widget(btn)
        g.add(check_row)
        return g

    def _on_check_now(self, *_a) -> None:
        self._apply_update_fields()
        self._services.save_config()
        if self._on_check_updates:
            self._on_check_updates()
        elif self._on_toast:
            self._on_toast("Update check unavailable")

    def refresh_update_meta(self) -> None:
        """Refresh last-check subtitle after an update check completes."""
        last = (self._services.config.last_update_check or "").strip()
        if hasattr(self, "_last_check_row"):
            self._last_check_row.set_subtitle(
                f"Last check: {last}" if last else "Not checked yet this install"
            )

    def _apply_update_fields(self) -> None:
        cfg = self._services.config
        cfg.check_for_updates = self._check_updates.get_active()
        cfg.update_check_interval_hours = max(
            1, int(self._update_interval.get_value())
        )

    def _group_mullvad(self) -> Adw.PreferencesGroup:
        from core import mullvad as mv

        g = Adw.PreferencesGroup()
        g.set_title("Optional: Mullvad")
        g.set_description(
            "Only relevant if a path hop uses Mullvad’s in-tunnel SOCKS "
            "(10.64.0.1). Other providers use a normal VPN/Proxy backend — "
            "you can ignore this section."
        )

        st = mv.probe()
        if not st.available:
            status_sub = "CLI not installed — integration inactive"
        elif st.connected:
            status_sub = st.summary
        else:
            status_sub = "CLI installed · idle (not used unless a hop needs it)"
        self._mv_status = Adw.ActionRow(
            title="CLI status",
            subtitle=status_sub,
        )
        g.add(self._mv_status)

        self._mv_auto = Adw.SwitchRow(
            title="Auto-manage when path needs it",
            subtitle="If the active path uses Mullvad SOCKS: connect the "
            "Mullvad app before Spectre Connect, and disconnect it with "
            "Spectre Disconnect. No effect for other paths.",
        )
        self._mv_auto.set_active(self._cfg.mullvad_auto_connect)
        g.add(self._mv_auto)

        row = Adw.ActionRow(title="Manual tunnel control")
        row.set_subtitle("Requires the mullvad CLI in PATH")
        btn_on = Gtk.Button(label="Connect")
        btn_on.set_valign(Gtk.Align.CENTER)
        btn_on.add_css_class("suggested-action")
        btn_on.set_sensitive(st.available)
        btn_on.connect("clicked", self._on_mullvad_connect)
        row.add_suffix(btn_on)
        btn_off = Gtk.Button(label="Disconnect")
        btn_off.set_valign(Gtk.Align.CENTER)
        btn_off.add_css_class("flat")
        btn_off.set_sensitive(st.available)
        btn_off.connect("clicked", self._on_mullvad_disconnect)
        row.add_suffix(btn_off)
        g.add(row)
        return g

    def _mv_status_subtitle(self, st) -> str:
        if not st.available:
            return "CLI not installed — integration inactive"
        if st.connected:
            return st.summary
        return "CLI installed · idle (not used unless a hop needs it)"

    def _on_mullvad_connect(self, *_a) -> None:
        import threading

        from gi.repository import GLib

        from core import mullvad as mv

        if self._on_toast:
            self._on_toast("Connecting Mullvad…")

        def worker() -> None:
            st = mv.ensure_connected(timeout_sec=45.0)

            def done() -> bool:
                if hasattr(self, "_mv_status"):
                    self._mv_status.set_subtitle(self._mv_status_subtitle(st))
                if self._on_toast:
                    self._on_toast(
                        st.summary
                        if st.ready_for_socks_hop
                        else (st.error or st.summary)
                    )
                return False

            GLib.idle_add(done)

        threading.Thread(
            target=worker, name="spectre-mullvad-connect", daemon=True
        ).start()

    def _on_mullvad_disconnect(self, *_a) -> None:
        import threading

        from gi.repository import GLib

        from core import mullvad as mv

        def worker() -> None:
            ok, msg = mv.disconnect()
            st = mv.probe()

            def done() -> bool:
                if hasattr(self, "_mv_status"):
                    self._mv_status.set_subtitle(self._mv_status_subtitle(st))
                if self._on_toast:
                    self._on_toast(msg if ok else (st.error or msg))
                return False

            GLib.idle_add(done)

        threading.Thread(
            target=worker, name="spectre-mullvad-disconnect", daemon=True
        ).start()

    def _group_network(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup()
        g.set_title("Network")
        g.set_description(
            "How traffic uses the path when connected. "
            "Changes apply after you disconnect and Connect again. "
            "If the network freezes: spectre unlock"
        )

        self._routing = Adw.ComboRow(title="Routing mode")
        self._routing.set_model(
            Gtk.StringList.new(
                [
                    "Entire system (default)",
                    "Selected apps only (SOCKS)",
                ]
            )
        )
        mode = (self._cfg.routing_mode or "system").lower()
        self._routing.set_selected(1 if mode == "apps" else 0)
        self._routing.set_subtitle(
            "Entire system (recommended): all traffic via Spectre after Connect; "
            "use Exclude apps to carve out clearnet (clearnet netns / exclude "
            "marks). Selected apps only: no system redirect — only manual SOCKS "
            "clients use Spectre; kill switch is not applied."
        )
        g.add(self._routing)

        self._kill = Adw.SwitchRow(
            title="Kill switch",
            subtitle="System mode only: block clearnet bypass (needs spectre setup-killswitch once)",
        )
        self._kill.set_active(self._cfg.kill_switch)
        g.add(self._kill)

        self._ipv6 = Adw.SwitchRow(
            title="Block IPv6",
            subtitle="Avoid IPv6 leaks outside the path",
        )
        self._ipv6.set_active(self._cfg.block_ipv6)
        g.add(self._ipv6)

        self._lan = Adw.SwitchRow(
            title="Allow LAN",
            subtitle="Permit local network access while the path is up",
        )
        self._lan.set_active(self._cfg.allow_lan)
        g.add(self._lan)

        self._dns_mode = Adw.ComboRow(title="DNS mode")
        self._dns_mode.set_model(
            Gtk.StringList.new(["System", "Remote (path)", "Custom"])
        )
        modes = {"system": 0, "remote": 1, "custom": 2}
        self._dns_mode.set_selected(
            modes.get(self._cfg.dns_mode.lower(), 1)
        )
        self._dns_mode.set_subtitle(
            "Remote/Custom: system DNS via the path. For Mullvad, use "
            "10.64.0.1 only (tunnel DNS). Public resolvers (1.1.1.1, …) make "
            "mullvad.net/check report a DNS “leak” even when traffic is tunneled."
        )
        g.add(self._dns_mode)

        self._dns_servers = Adw.EntryRow(title="DNS servers (remote / custom)")
        self._dns_servers.set_text(self._cfg.dns_servers)
        if hasattr(self._dns_servers, "set_show_apply_button"):
            self._dns_servers.set_show_apply_button(False)
        g.add(self._dns_servers)

        self._leak = Adw.SwitchRow(
            title="Leak guard",
            subtitle="Extra checks against DNS / IP leaks",
        )
        self._leak.set_active(self._cfg.leak_guard)
        g.add(self._leak)
        return g

    def _group_privacy(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup()
        g.set_title("Privacy")
        g.set_description(
            "Desktop policy for adapters, browsers, and sensitive Operate work"
        )

        self._webrtc = Adw.SwitchRow(
            title="Discourage WebRTC leaks",
            subtitle="Prefer configs that keep WebRTC off-path",
        )
        self._webrtc.set_active(self._cfg.block_webrtc)
        g.add(self._webrtc)

        self._udp = Adw.SwitchRow(
            title="Block non-tunnel UDP",
            subtitle="Aggressive: may break some apps until core supports it",
        )
        self._udp.set_active(self._cfg.block_udp_non_tunnel)
        g.add(self._udp)

        # Default ON for path requirement ⇒ switch shows "allow without path" OFF
        self._allow_sensitive_no_path = Adw.SwitchRow(
            title="Allow sensitive operations without a path",
            subtitle=(
                "Off (recommended): Operate plugins, agents, and marketplace "
                "require an active Connect (VPN/privacy path). "
                "On: permit that work on clearnet — confirmation required."
            ),
        )
        self._allow_sensitive_no_path.set_active(
            bool(getattr(self._cfg, "allow_sensitive_without_path", False))
        )
        self._allow_sensitive_no_path.set_tooltip_text(
            "Sensitive work (C2 agents, operator plugins) can leak identity "
            "without a path. Keep this off unless you accept that risk."
        )
        self._sensitive_gate_guard = False
        self._allow_sensitive_no_path.connect(
            "notify::active", self._on_allow_sensitive_no_path_toggled
        )
        g.add(self._allow_sensitive_no_path)
        return g

    def _on_allow_sensitive_no_path_toggled(self, row: Adw.SwitchRow, *_a) -> None:
        """Require explicit confirmation before enabling clearnet-sensitive mode."""
        if getattr(self, "_sensitive_gate_guard", False):
            return
        want = bool(row.get_active())
        # Turning off is always allowed without a prompt
        if not want:
            return

        # Revert until the user confirms
        self._sensitive_gate_guard = True
        row.set_active(False)
        self._sensitive_gate_guard = False

        root = self.get_root()
        parent = root if isinstance(root, Gtk.Window) else None
        dialog = Adw.MessageDialog(
            transient_for=parent,
            heading="Allow sensitive work without a path?",
            body=(
                "Operate tools (including agent control and marketplace plugins) "
                "will be usable while disconnected from any VPN or privacy path.\n\n"
                "Traffic may leave this machine on clearnet and can expose your "
                "identity or location. Only continue if you accept that risk."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("enable", "Enable anyway")
        dialog.set_response_appearance(
            "enable", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d: Adw.MessageDialog, response: str) -> None:
            if response != "enable":
                return
            self._sensitive_gate_guard = True
            row.set_active(True)
            self._sensitive_gate_guard = False

        dialog.connect("response", on_response)
        dialog.present()

    def _group_logging(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup()
        g.set_title("Logging")
        g.set_description("Diagnostics for shell and core")

        self._log_level = Adw.ComboRow(title="Log level")
        self._log_level.set_model(
            Gtk.StringList.new(["Error", "Warn", "Info", "Debug"])
        )
        levels = {"error": 0, "warn": 1, "info": 2, "debug": 3}
        self._log_level.set_selected(
            levels.get(self._cfg.log_level.lower(), 2)
        )
        g.add(self._log_level)

        self._log_file = Adw.SwitchRow(
            title="Log to file",
            subtitle="Write logs under the user data directory",
        )
        self._log_file.set_active(self._cfg.log_to_file)
        g.add(self._log_file)
        return g

    def _group_advanced(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup()
        g.set_title("Advanced")
        g.set_description("Low-level knobs passed through to the core when available")

        self._bind = Adw.EntryRow(title="Local bind address")
        self._bind.set_text(self._cfg.bind_address)
        g.add(self._bind)

        self._mtu = Adw.SpinRow(
            title="MTU",
            subtitle="Path MTU hint (common: 1280–1500)",
            adjustment=Gtk.Adjustment(
                value=self._cfg.mtu,
                lower=576,
                upper=9000,
                step_increment=10,
                page_increment=50,
            ),
        )
        g.add(self._mtu)
        return g

    def _group_plugins(self) -> Gtk.Widget:
        """Posture presets + built-in packs + Operate suite switch."""
        from core.plugins import (
            PLUGINS,
            enabled_summary,
            normalize_enabled,
            preset_lab,
            preset_operate,
            preset_privacy,
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.add_css_class("settings-plugins")

        intro = Gtk.Label(
            label=(
                "Path console by default. Lab packs add measurement. "
                "Operate unlocks marketplace tools (Hogwarts C2) on the rail."
            ),
            wrap=True,
            xalign=0,
        )
        intro.add_css_class("muted")
        intro.add_css_class("settings-plugins-intro")
        box.append(intro)

        # Active configuration (emphasized) — not the preset buttons
        active_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        active_lab = Gtk.Label(label="Active configuration", xalign=0)
        active_lab.add_css_class("section-label")
        active_row.append(active_lab)
        self._plugins_summary = Gtk.Label(xalign=0, wrap=True)
        self._plugins_summary.add_css_class("settings-plugins-summary")
        active_row.append(self._plugins_summary)
        box.append(active_row)

        # Presets — plain actions (no permanent "suggested" highlight)
        preset_lab_w = Gtk.Label(label="Apply preset", xalign=0)
        preset_lab_w.add_css_class("section-label")
        box.append(preset_lab_w)

        preset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        preset_row.set_halign(Gtk.Align.START)
        preset_row.add_css_class("settings-plugins-presets")
        self._preset_btns: dict[str, Gtk.Button] = {}
        for label, factory, operate in (
            ("Privacy", preset_privacy, False),
            ("Lab", preset_lab, False),
            ("Operate", preset_operate, True),
        ):
            btn = Gtk.Button(label=label)
            btn.add_css_class("flat")
            btn.add_css_class("pill")
            btn.add_css_class("settings-plugins-preset-btn")
            btn.set_tooltip_text(
                {
                    "Privacy": "Core path console only — no packs, no Operate rail",
                    "Lab": "Path fingerprint + lab companions (still no C2 rail)",
                    "Operate": "Lab packs + Operate rail (marketplace, Hogwarts, …)",
                }[label]
            )
            btn.connect(
                "clicked",
                lambda _b, fn=factory, op=operate: self._apply_plugin_preset(
                    fn(), operate=op
                ),
            )
            self._preset_btns[label] = btn
            preset_row.append(btn)
        box.append(preset_row)

        # Operate suite master switch
        op_g = Adw.PreferencesGroup()
        op_g.set_title("Operate suite")
        op_g.set_description(
            "When on: Plugins marketplace and C2 tools appear under Operate on the "
            "left rail. Path pages stay primary either way."
        )
        self._operate_switch = Adw.SwitchRow(
            title="Enable Operate",
            subtitle="Marketplace · Hogwarts · future operator plugins",
        )
        self._operate_switch.set_active(
            bool(getattr(self._cfg, "operate_enabled", False))
        )
        self._operate_switch.set_tooltip_text(
            "Off = path console (Privacy/Lab). On = unlock operator tools on the rail."
        )
        self._operate_switch.connect(
            "notify::active",
            lambda *_a: self._refresh_plugins_summary(),
        )
        op_g.add(self._operate_switch)
        box.append(op_g)

        # Catalog
        g = Adw.PreferencesGroup()
        g.set_title("Built-in packs")
        g.set_description(
            "Lab measurement packs. Toggle, then Save. "
            "Marketplace plugins install from the Operate → Plugins page."
        )
        enabled = set(normalize_enabled(self._cfg.plugins_enabled))
        self._plugin_switches.clear()
        for p in PLUGINS:
            row = Adw.SwitchRow(
                title=p.title,
                subtitle=f"Lab · {p.tagline}",
            )
            row.set_active(p.id in enabled)
            row.set_tooltip_text(p.description)
            row.connect(
                "notify::active",
                lambda *_a: self._refresh_plugins_summary(),
            )
            self._plugin_switches[p.id] = row
            g.add(row)
        box.append(g)

        self._refresh_plugins_summary()
        return box

    def _apply_plugin_preset(self, ids: list[str], *, operate: bool = False) -> None:
        want = set(ids)
        for pid, row in self._plugin_switches.items():
            row.set_active(pid in want)
        if getattr(self, "_operate_switch", None) is not None:
            self._operate_switch.set_active(bool(operate))
        self._refresh_plugins_summary()
        if self._on_toast:
            from core.plugins import enabled_summary

            self._on_toast(
                f"Preset · {enabled_summary(ids, operate_enabled=operate)} (Save to apply)"
            )

    def _collect_plugins_enabled(self) -> list[str]:
        from core.plugins import normalize_enabled

        return normalize_enabled(
            pid for pid, row in self._plugin_switches.items() if row.get_active()
        )

    def _collect_operate_enabled(self) -> bool:
        sw = getattr(self, "_operate_switch", None)
        if sw is not None:
            return bool(sw.get_active())
        return bool(getattr(self._cfg, "operate_enabled", False))

    def _refresh_plugins_summary(self) -> None:
        from core.plugins import (
            enabled_summary,
            normalize_enabled,
            preset_lab,
            preset_operate,
            preset_privacy,
        )

        if getattr(self, "_plugins_summary", None) is None:
            return
        # Prefer live switches when built
        if self._plugin_switches:
            ids = self._collect_plugins_enabled()
        else:
            ids = list(self._cfg.plugins_enabled or [])
        operate = self._collect_operate_enabled()
        self._plugins_summary.set_text(
            enabled_summary(ids, operate_enabled=operate)
        )

        # Mark which preset matches current switches + operate flag
        cur = set(normalize_enabled(ids))
        match = None
        if not operate and cur == set(preset_privacy()):
            match = "Privacy"
        elif not operate and cur == set(preset_lab()):
            match = "Lab"
        elif operate and cur == set(preset_operate()):
            match = "Operate"
        # Partial Operate (operate on but different packs) still highlight Operate
        elif operate:
            match = "Operate"
        for name, btn in getattr(self, "_preset_btns", {}).items():
            if name == match:
                btn.add_css_class("settings-plugins-preset-active")
            else:
                btn.remove_css_class("settings-plugins-preset-active")

    def _on_save(self, *_a) -> None:
        cfg = self._services.config
        cfg.core_socket = self._socket.get_text().strip()
        cfg.api_token = self._token.get_text().strip()
        cfg.core_timeout_sec = int(self._timeout.get_value())
        cfg.reconnect_auto = self._reconnect.get_active()
        cfg.reconnect_delay_sec = int(self._reconnect_delay.get_value())
        cfg.auto_connect = self._auto_connect.get_active()
        cfg.start_minimized = self._start_min.get_active()
        cfg.tray_enabled = self._tray.get_active()
        cfg.close_to_tray = self._close_tray.get_active()
        cfg.notify_on_disconnect = self._notify.get_active()
        cfg.mullvad_auto_connect = self._mv_auto.get_active()
        cfg.routing_mode = (
            "apps" if int(self._routing.get_selected()) == 1 else "system"
        )
        cfg.kill_switch = self._kill.get_active()
        cfg.block_ipv6 = self._ipv6.get_active()
        cfg.allow_lan = self._lan.get_active()
        dns_idx = int(self._dns_mode.get_selected())
        cfg.dns_mode = ("system", "remote", "custom")[max(0, min(2, dns_idx))]
        cfg.dns_servers = self._dns_servers.get_text().strip()
        cfg.leak_guard = self._leak.get_active()
        cfg.block_webrtc = self._webrtc.get_active()
        cfg.block_udp_non_tunnel = self._udp.get_active()
        log_idx = int(self._log_level.get_selected())
        cfg.log_level = ("error", "warn", "info", "debug")[max(0, min(3, log_idx))]
        cfg.log_to_file = self._log_file.get_active()
        self._apply_update_fields()
        cfg.bind_address = self._bind.get_text().strip() or "127.0.0.1"
        cfg.mtu = int(self._mtu.get_value())
        prev_plugins = list(cfg.plugins_enabled or [])
        prev_operate = bool(getattr(cfg, "operate_enabled", False))
        prev_sensitive = bool(
            getattr(cfg, "allow_sensitive_without_path", False)
        )
        # Privacy section may not be built yet if user never opened it
        if getattr(self, "_allow_sensitive_no_path", None) is not None:
            cfg.allow_sensitive_without_path = bool(
                self._allow_sensitive_no_path.get_active()
            )
        cfg.plugins_enabled = self._collect_plugins_enabled()
        cfg.operate_enabled = self._collect_operate_enabled()
        self._refresh_plugins_summary()

        self._services.save_config()
        app = None
        try:
            win = self.get_root() if hasattr(self, "get_root") else None
            if win is not None and hasattr(win, "get_application"):
                app = win.get_application()
        except Exception:
            app = None
        if app is not None and hasattr(app, "apply_tray_settings"):
            try:
                app.apply_tray_settings()
            except Exception:
                pass
        new_sensitive = bool(getattr(cfg, "allow_sensitive_without_path", False))
        posture_changed = (
            prev_plugins != cfg.plugins_enabled
            or prev_operate != cfg.operate_enabled
            or prev_sensitive != new_sensitive
        )
        if posture_changed and self._on_plugins_changed:
            try:
                self._on_plugins_changed()
            except Exception:
                pass
        if self._on_toast:
            msg = self._services.with_reconnect_hint("Settings saved")
            if not self._services.config.tray_enabled:
                msg = "Settings saved · tray icon removed"
            elif self._services.config.tray_enabled:
                msg = self._services.with_reconnect_hint("Settings saved · tray on")
            if posture_changed:
                from core.plugins import enabled_summary

                msg = (
                    "Settings saved · "
                    + enabled_summary(
                        cfg.plugins_enabled,
                        operate_enabled=bool(cfg.operate_enabled),
                    )
                )
                if new_sensitive:
                    msg += " · sensitive ops allowed without path"
                elif prev_sensitive and not new_sensitive:
                    msg += " · path required for sensitive ops"
            self._on_toast(msg)
