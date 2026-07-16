"""Settings — comprehensive Spectre Desktop configuration."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

from app_config import APPLICATION_VERSION, GITHUB_URL
from core.client import default_socket_path
from core.updates import DEFAULT_CHECK_INTERVAL_HOURS
from services import Services
from widgets.chrome import page_header, scroll_body


class SettingsPage(Gtk.Box):
    def __init__(
        self,
        services: Services,
        *,
        parent_window: Gtk.Window | None = None,
        on_toast: Callable[[str], None] | None = None,
        on_check_updates: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("page")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._services = services
        self._parent_window = parent_window
        self._on_toast = on_toast
        self._on_check_updates = on_check_updates
        self._cfg = services.config

        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._on_save)
        self.append(page_header("Settings", end=save))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.add_css_class("page-body")
        body.set_valign(Gtk.Align.START)

        body.append(self._group_core())
        body.append(self._group_session())
        body.append(self._group_mullvad())
        body.append(self._group_network())
        body.append(self._group_privacy())
        body.append(self._group_updates())
        body.append(self._group_logging())
        body.append(self._group_advanced())

        self.append(scroll_body(body, margin=12))

    # ── Groups ────────────────────────────────────────────────────

    def _group_core(self) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup()
        g.set_title("Spectre core")
        g.set_description(
            "How this desktop shell reaches the headless core. "
            f"Default socket: {default_socket_path()}"
        )

        # Note: Adw.EntryRow has no "subtitle" on older libadwaita — only title.
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
            f"Check GitHub Releases for Spectre Desktop "
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

        # Action row: Check now
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
        # Persist toggle/interval first so the checker sees current prefs
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
        # Avoid a loud “Disconnected” when the CLI is present but unused.
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
        from core import mullvad as mv

        if self._on_toast:
            self._on_toast("Connecting Mullvad…")
        st = mv.ensure_connected(timeout_sec=45.0)
        if hasattr(self, "_mv_status"):
            self._mv_status.set_subtitle(self._mv_status_subtitle(st))
        if self._on_toast:
            self._on_toast(st.summary if st.ready_for_socks_hop else (st.error or st.summary))

    def _on_mullvad_disconnect(self, *_a) -> None:
        from core import mullvad as mv

        ok, msg = mv.disconnect()
        st = mv.probe()
        if hasattr(self, "_mv_status"):
            self._mv_status.set_subtitle(self._mv_status_subtitle(st))
        if self._on_toast:
            self._on_toast(msg if ok else (st.error or msg))

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
                    "Launched apps / SOCKS only",
                ]
            )
        )
        mode = (self._cfg.routing_mode or "system").lower()
        self._routing.set_selected(1 if mode == "apps" else 0)
        self._routing.set_subtitle(
            "Entire system (recommended): all traffic via Spectre after Connect. "
            "Launched apps only: clearnet by default; only “Open via path” and "
            "manual SOCKS clients use Spectre. Path-opened apps keep running on "
            "Disconnect but lose SOCKS network until you Connect again."
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
        g.add(self._dns_mode)

        self._dns_servers = Adw.EntryRow(title="Custom DNS servers")
        self._dns_servers.set_text(self._cfg.dns_servers)
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
        g.set_description("Desktop policy hints for adapters and browsers")

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
        return g

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

        self._services.save_config()
        # Apply tray on/off immediately so a broken icon can be removed without restart
        app = None
        try:
            from gi.repository import Gtk

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
        if self._on_toast:
            msg = self._services.with_reconnect_hint("Settings saved")
            if not self._services.config.tray_enabled:
                msg = "Settings saved · tray icon removed"
            elif self._services.config.tray_enabled:
                msg = self._services.with_reconnect_hint("Settings saved · tray on")
            self._on_toast(msg)
