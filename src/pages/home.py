"""Home — status, path map, connect/disconnect only."""

from __future__ import annotations

import threading
from collections.abc import Callable

from gi.repository import Adw, GLib, Gtk

from core.client import CoreState, CoreStatus
from core.path_explain import explain_live, explain_profile
from core.path_info import path_info_text
from core.readiness import Readiness, profile_uses_mullvad_app_socks
from services import Services
from widgets.chrome import clear_box, fit_body
from widgets.path_graph import path_graph
from widgets.state import kind_from_core


class HomePage(Gtk.Box):
    def __init__(
        self,
        services: Services,
        *,
        on_toast: Callable[[str], None] | None = None,
        on_state_changed: Callable[[], None] | None = None,
        on_navigate: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("page")
        self.add_css_class("home-page")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._services = services
        self._on_toast = on_toast
        self._on_state_changed = on_state_changed
        self._on_navigate = on_navigate
        self._action_busy = False

        # Centered mission control on a soft stage (not edge-to-edge clutter)
        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        stage.add_css_class("home-stage")
        stage.set_hexpand(True)
        stage.set_vexpand(True)
        stage.set_halign(Gtk.Align.CENTER)
        stage.set_valign(Gtk.Align.CENTER)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.add_css_class("home-card")
        card.set_halign(Gtk.Align.CENTER)
        card.set_hexpand(True)
        card.set_size_request(480, -1)

        # Status hero
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        hero.add_css_class("home-hero")
        hero.set_halign(Gtk.Align.CENTER)

        self._dot = Gtk.Box()
        self._dot.add_css_class("state-dot")
        self._dot.add_css_class("home-status-dot")
        self._dot.set_halign(Gtk.Align.CENTER)
        hero.append(self._dot)

        self._title = Gtk.Label(label="—")
        self._title.add_css_class("home-status-title")
        self._title.set_halign(Gtk.Align.CENTER)
        self._title.set_justify(Gtk.Justification.CENTER)
        hero.append(self._title)

        self._detail = Gtk.Label(label="", wrap=True)
        self._detail.add_css_class("home-status-detail")
        self._detail.set_halign(Gtk.Align.CENTER)
        self._detail.set_justify(Gtk.Justification.CENTER)
        self._detail.set_max_width_chars(48)
        hero.append(self._detail)
        card.append(hero)

        # World map (Mullvad relay cities) — real land outlines
        self._mv_map = None
        try:
            from widgets.mullvad_map import MullvadMap

            self._mv_map = MullvadMap(
                height=240,
                interactive=True,
                on_toast=self._on_toast,
                on_location=self._on_map_location,
            )
            self._mv_map.add_css_class("home-map")
            card.append(self._mv_map)
        except Exception:
            self._mv_map = None

        # Path diagram in its own well (graph centered in the well)
        path_well = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        path_well.add_css_class("home-path-well")
        path_well.set_halign(Gtk.Align.FILL)
        path_well.set_hexpand(True)

        path_center = Gtk.CenterBox()
        path_center.add_css_class("home-path-center")
        path_center.set_halign(Gtk.Align.FILL)
        path_center.set_hexpand(True)

        self._path_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._path_host.add_css_class("home-path-host")
        self._path_host.set_halign(Gtk.Align.CENTER)
        self._path_host.set_valign(Gtk.Align.CENTER)
        self._path_host.set_hexpand(False)
        path_center.set_center_widget(self._path_host)
        path_well.append(path_center)
        card.append(path_well)

        # Path picker
        picker = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        picker.add_css_class("home-picker")
        picker.set_halign(Gtk.Align.FILL)

        pick_lab = Gtk.Label(label="Active path", xalign=0.5)
        pick_lab.add_css_class("home-picker-label")
        pick_lab.set_halign(Gtk.Align.CENTER)
        picker.append(pick_lab)

        profile_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        profile_row.add_css_class("home-profile-row")
        profile_row.set_halign(Gtk.Align.CENTER)

        self._profile_dd = Gtk.DropDown()
        self._profile_dd.add_css_class("home-profile-dd")
        self._profile_dd.set_size_request(260, -1)
        self._profile_dd.set_tooltip_text("Active path")
        self._profile_dd.connect("notify::selected", self._on_profile_picked)
        self._profile_ids: list[str] = []
        self._profile_dd_block = False
        profile_row.append(self._profile_dd)

        self._info_btn = Gtk.Button()
        self._info_btn.add_css_class("flat")
        self._info_btn.add_css_class("circular")
        self._info_btn.add_css_class("home-info-btn")
        self._info_btn.set_icon_name("help-about-symbolic")
        self._info_btn.set_tooltip_text("What does this path do?")
        self._info_btn.set_valign(Gtk.Align.CENTER)
        self._info_btn.connect("clicked", self._on_info)
        profile_row.append(self._info_btn)
        picker.append(profile_row)
        card.append(picker)

        # Mullvad server picker (GPL-3 open-source client via CLI)
        self._mv_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._mv_box.add_css_class("home-mullvad")
        self._mv_box.set_halign(Gtk.Align.FILL)
        self._mv_box.set_visible(False)

        mv_lab = Gtk.Label(label="Mullvad VPN server", xalign=0.5)
        mv_lab.add_css_class("home-picker-label")
        mv_lab.set_halign(Gtk.Align.CENTER)
        self._mv_box.append(mv_lab)

        mv_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        mv_row.set_halign(Gtk.Align.CENTER)
        mv_row.set_hexpand(True)

        self._mv_country = Gtk.DropDown()
        self._mv_country.add_css_class("home-profile-dd")
        self._mv_country.set_size_request(280, -1)
        self._mv_country.set_tooltip_text("Country (or any)")
        self._mv_country.connect("notify::selected", self._on_mv_country)
        mv_row.append(self._mv_country)

        self._mv_city = Gtk.DropDown()
        self._mv_city.add_css_class("home-profile-dd")
        self._mv_city.set_size_request(280, -1)
        self._mv_city.set_tooltip_text("City (or any in country)")
        self._mv_city.connect("notify::selected", self._on_mv_city)
        mv_row.append(self._mv_city)

        self._mv_host = Gtk.DropDown()
        self._mv_host.add_css_class("home-profile-dd")
        self._mv_host.set_size_request(280, -1)
        self._mv_host.set_tooltip_text("Specific server (or any in city)")
        self._mv_host.connect("notify::selected", self._on_mv_host)
        mv_row.append(self._mv_host)

        self._mv_status = Gtk.Label(label="", xalign=0.5, wrap=True)
        self._mv_status.add_css_class("home-mullvad-status")
        self._mv_status.set_halign(Gtk.Align.CENTER)
        self._mv_status.set_max_width_chars(40)
        mv_row.append(self._mv_status)

        self._mv_box.append(mv_row)
        card.append(self._mv_box)

        self._mv_block = False
        self._mv_country_codes: list[str] = []
        self._mv_city_codes: list[str] = []
        self._mv_hosts: list[str] = []
        self._mv_ready = False
        GLib.idle_add(self._init_mullvad_picker)

        # Next-step + primary CTA
        foot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        foot.add_css_class("home-foot")
        foot.set_halign(Gtk.Align.CENTER)

        self._next = Gtk.Button()
        self._next.add_css_class("flat")
        self._next.add_css_class("home-next")
        self._next.set_halign(Gtk.Align.CENTER)
        self._next.set_visible(False)
        self._next.connect("clicked", self._on_next)
        self._next_target: str | None = None
        foot.append(self._next)

        self._primary = Gtk.Button(label="Connect")
        self._primary.add_css_class("suggested-action")
        self._primary.add_css_class("home-cta")
        self._primary.set_size_request(220, 42)
        self._primary.set_halign(Gtk.Align.CENTER)
        self._primary.connect("clicked", self._on_primary)
        foot.append(self._primary)
        card.append(foot)

        stage.append(card)
        self.append(fit_body(stage, margin=24))
        self.refresh()

    def _nav(self, page_id: str) -> None:
        if self._on_navigate is not None:
            self._on_navigate(page_id)

    def _root_window(self) -> Gtk.Window | None:
        w = self.get_root()
        return w if isinstance(w, Gtk.Window) else None

    # ── Mullvad server picker (open-source CLI) ───────────────────

    def _init_mullvad_picker(self) -> bool:
        from core import mullvad as mv

        if not mv.cli_path():
            self._mv_box.set_visible(False)
            return False
        try:
            cat = mv.load_catalog()
        except Exception:
            self._mv_box.set_visible(False)
            return False
        if not cat.countries:
            self._mv_box.set_visible(False)
            return False

        self._mv_block = True
        try:
            labels = ["Any country"]
            codes = ["any"]
            for code, name in cat.countries:
                labels.append(f"{name} ({code})")
                codes.append(code)
            self._mv_country_codes = codes
            self._mv_country.set_model(Gtk.StringList.new(labels))

            country, city, host = mv.get_location_constraint()
            # Select country
            idx = 0
            if country and country in codes:
                idx = codes.index(country)
            self._mv_country.set_selected(idx)
            self._reload_mv_cities(select_city=city, select_host=host)
            st = mv.probe()
            self._mv_status.set_text(st.summary or "")
            self._mv_box.set_visible(True)
            self._mv_ready = True
            if self._mv_map is not None:
                self._mv_map.set_active(country, city)
        finally:
            self._mv_block = False
        return False

    def _on_map_location(self, country: str, city: str, city_name: str) -> None:
        """Map click — refresh pickers to match Mullvad selection."""
        if not self._mv_ready:
            return
        self._mv_block = True
        try:
            if country in self._mv_country_codes:
                self._mv_country.set_selected(self._mv_country_codes.index(country))
            self._reload_mv_cities(select_city=city, select_host="")
            from core import mullvad as mv

            st = mv.probe()
            self._mv_status.set_text(
                st.summary or f"{city_name} ({country}/{city})"
            )
        finally:
            self._mv_block = False

    def _reload_mv_cities(
        self, *, select_city: str = "", select_host: str = ""
    ) -> None:
        from core import mullvad as mv

        cat = mv.load_catalog()
        cidx = int(self._mv_country.get_selected())
        country = (
            self._mv_country_codes[cidx]
            if 0 <= cidx < len(self._mv_country_codes)
            else "any"
        )
        labels = ["Any city"]
        codes = ["any"]
        if country and country != "any":
            for code, name in cat.cities.get(country, []):
                labels.append(f"{name} ({code})")
                codes.append(code)
        self._mv_city_codes = codes
        self._mv_block = True
        try:
            self._mv_city.set_model(Gtk.StringList.new(labels))
            csel = 0
            if select_city and select_city in codes:
                csel = codes.index(select_city)
            self._mv_city.set_selected(csel)
            self._reload_mv_hosts(select_host=select_host)
        finally:
            self._mv_block = False

    def _reload_mv_hosts(self, *, select_host: str = "") -> None:
        from core import mullvad as mv

        cat = mv.load_catalog()
        cidx = int(self._mv_country.get_selected())
        city_i = int(self._mv_city.get_selected())
        country = (
            self._mv_country_codes[cidx]
            if 0 <= cidx < len(self._mv_country_codes)
            else "any"
        )
        city = (
            self._mv_city_codes[city_i]
            if 0 <= city_i < len(self._mv_city_codes)
            else "any"
        )
        labels = ["Any server"]
        hosts = ["any"]
        if country != "any" and city != "any":
            for h in cat.hosts.get((country, city), []):
                labels.append(h)
                hosts.append(h)
        self._mv_hosts = hosts
        self._mv_block = True
        try:
            self._mv_host.set_model(Gtk.StringList.new(labels))
            hsel = 0
            if select_host and select_host in hosts:
                hsel = hosts.index(select_host)
            self._mv_host.set_selected(hsel)
        finally:
            self._mv_block = False

    def _on_mv_country(self, *_a) -> None:
        if self._mv_block or not self._mv_ready:
            return
        self._reload_mv_cities()
        self._apply_mv_location()

    def _on_mv_city(self, *_a) -> None:
        if self._mv_block or not self._mv_ready:
            return
        self._reload_mv_hosts()
        self._apply_mv_location()

    def _on_mv_host(self, *_a) -> None:
        if self._mv_block or not self._mv_ready:
            return
        self._apply_mv_location()

    def _apply_mv_location(self) -> None:
        from core import mullvad as mv

        cidx = int(self._mv_country.get_selected())
        city_i = int(self._mv_city.get_selected())
        hidx = int(self._mv_host.get_selected())
        country = (
            self._mv_country_codes[cidx]
            if 0 <= cidx < len(self._mv_country_codes)
            else "any"
        )
        city = (
            self._mv_city_codes[city_i]
            if 0 <= city_i < len(self._mv_city_codes)
            else "any"
        )
        host = self._mv_hosts[hidx] if 0 <= hidx < len(self._mv_hosts) else "any"

        def worker() -> None:
            ok, msg = mv.set_location(
                country,
                None if city in ("", "any") else city,
                None if host in ("", "any") else host,
            )
            st = mv.probe()

            def done() -> bool:
                if ok:
                    self._mv_status.set_text(st.summary or msg)
                    if self._mv_map is not None:
                        self._mv_map.set_active(
                            country if country != "any" else "",
                            city if city not in ("", "any") else "",
                        )
                    if self._on_toast:
                        self._on_toast(msg if len(msg) < 80 else st.summary)
                else:
                    self._mv_status.set_text(msg)
                    if self._on_toast:
                        self._on_toast(msg)
                return False

            GLib.idle_add(done)

        threading.Thread(target=worker, name="mullvad-set-location", daemon=True).start()

    def _on_profile_picked(self, *_a) -> None:
        if self._profile_dd_block:
            return
        idx = int(self._profile_dd.get_selected())
        if idx < 0 or idx >= len(self._profile_ids):
            return
        pid = self._profile_ids[idx]
        if pid == "__none__":
            return
        if self._services.config.last_profile_id == pid:
            return
        self._services.config.last_profile_id = pid
        try:
            self._services.save_config()
        except Exception:
            pass
        self.refresh(force_core=False)
        if self._on_state_changed:
            self._on_state_changed()

    def _on_next(self, *_a) -> None:
        if self._next_target:
            self._nav(self._next_target)

    def _sync_profile_dropdown(self, profile_id: str | None) -> None:
        profiles = list(self._services.profiles.list())
        ids = [p.id for p in profiles]
        labels = [p.name for p in profiles]
        if not labels:
            ids = ["__none__"]
            labels = ["No paths yet"]
        # Rebuild model only when membership or names change
        if ids != self._profile_ids:
            self._profile_ids = ids
            self._profile_dd_block = True
            try:
                self._profile_dd.set_model(Gtk.StringList.new(labels))
            finally:
                self._profile_dd_block = False
        pick = 0
        if profile_id and profile_id in self._profile_ids:
            pick = self._profile_ids.index(profile_id)
        self._profile_dd_block = True
        try:
            self._profile_dd.set_selected(pick)
            self._profile_dd.set_sensitive(bool(profiles))
        finally:
            self._profile_dd_block = False

    def _update_next_chip(
        self,
        *,
        active: bool,
        ready: Readiness,
        profile,
        st: CoreStatus,
    ) -> None:
        """Show a single actionable next step when Connect is not the right verb."""
        self._next_target = None
        if active or st.state == CoreState.CONNECTING or self._action_busy:
            self._next.set_visible(False)
            return
        if profile is None:
            self._next.set_label("Create a path →")
            self._next_target = "profiles"
            self._next.set_visible(True)
            return
        if not ready.ok:
            low = ready.summary.lower()
            if "incomplete" in low or "backend" in low or "adapter" in low:
                label = "Fix adapters →"
                target = "backends"
            elif "no backend" in low or "unbound" in low or "hop" in low:
                label = "Bind hops on path →"
                target = "profiles"
            else:
                label = f"{ready.summary[:42]}{'…' if len(ready.summary) > 42 else ''} →"
                target = "profiles"
            self._next.set_label(label)
            self._next_target = target
            self._next.set_visible(True)
            return
        self._next.set_sensitive(True)
        self._next.set_visible(False)

    def _on_info(self, *_a) -> None:
        st = self._services.core.status()
        profile = self._services.active_profile()
        routing = getattr(self._services.config, "routing_mode", "system") or "system"
        heading, body = path_info_text(
            profile,
            self._services.backends,
            routing_mode=str(routing),
            connected=st.state == CoreState.CONNECTED,
        )
        parent = self._root_window()
        dialog = Adw.MessageDialog(
            transient_for=parent,
            heading=heading,
            body=body,
        )
        dialog.add_response("ok", "Got it")
        if profile is not None:
            dialog.add_response("edit", "Edit…")
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")

        def on_response(_d: Adw.MessageDialog, response: str) -> None:
            if response == "edit" and profile is not None:
                self._edit_profile_info(profile.id)

        dialog.connect("response", on_response)
        dialog.present()

    def _edit_profile_info(self, profile_id: str) -> None:
        from core.path_info import resolve_profile_info

        profile = self._services.profiles.get(profile_id)
        if profile is None:
            return
        parent = self._root_window()
        dialog = Adw.MessageDialog(
            transient_for=parent,
            heading=f"Edit info — {profile.name}",
            body=(
                "This text appears when you press ⓘ on the dashboard. "
                "Clear it to restore the built-in default (seed profiles) "
                "or “Custom configuration.” (your own profiles)."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(160)
        scrolled.set_min_content_width(320)
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        buf = Gtk.TextBuffer()
        # Show effective text for editing (including defaults resolved in).
        buf.set_text(resolve_profile_info(profile))
        view = Gtk.TextView(buffer=buf)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_top_margin(6)
        view.set_bottom_margin(6)
        view.set_left_margin(6)
        view.set_right_margin(6)
        scrolled.set_child(view)
        box.append(scrolled)
        dialog.set_extra_child(box)

        def on_response(_d: Adw.MessageDialog, response: str) -> None:
            if response != "save":
                return
            start = buf.get_start_iter()
            end = buf.get_end_iter()
            text = buf.get_text(start, end, False)
            try:
                self._services.profiles.update(profile_id, info=text)
            except ValueError as exc:
                if self._on_toast:
                    self._on_toast(str(exc))
                return
            if self._on_toast:
                self._on_toast("Path info saved")
            # Re-open info so the user sees the result
            self._on_info()

        dialog.connect("response", on_response)
        dialog.present()

    def _on_primary(self, *_a) -> None:
        # Connect/Disconnect can block for tens of seconds (Mullvad ensure,
        # live readiness, spectred + nft). Never run that on the GTK thread.
        if self._action_busy:
            return
        st = self._services.core.status()
        disconnecting = st.state in (CoreState.CONNECTED, CoreState.CONNECTING)

        self._action_busy = True
        self._primary.set_sensitive(False)
        self._primary.set_label("Disconnecting…" if disconnecting else "Connecting…")
        if self._on_toast:
            self._on_toast(
                "Disconnecting…" if disconnecting else "Connecting…"
            )

        def worker() -> None:
            err: str | None = None
            status: CoreStatus | None = None
            ready: Readiness | None = None
            toast = ""
            try:
                if disconnecting:
                    status, toast = self._services.disconnect()
                else:
                    status, ready = self._services.connect_active()
            except Exception as exc:
                err = str(exc) or repr(exc)

            def done() -> bool:
                self._finish_primary(
                    disconnecting=disconnecting,
                    status=status,
                    ready=ready,
                    toast=toast,
                    err=err,
                )
                return False

            GLib.idle_add(done)

        threading.Thread(
            target=worker,
            name="spectre-home-connect",
            daemon=True,
        ).start()

    def _finish_primary(
        self,
        *,
        disconnecting: bool,
        status: CoreStatus | None,
        ready: Readiness | None,
        toast: str,
        err: str | None,
    ) -> None:
        self._action_busy = False
        self.refresh(force_core=True)
        if self._on_state_changed:
            self._on_state_changed()

        if err is not None:
            if self._on_toast:
                self._on_toast(err)
            return

        if disconnecting:
            if self._on_toast:
                self._on_toast(toast or "Disconnected")
            return

        if ready is not None and not ready.ok:
            if self._on_toast:
                self._on_toast(ready.summary)
            # Guide user to fix bindings / backends / external deps
            low = ready.summary.lower()
            if "no profile" in low:
                self._nav("profiles")
            elif "incomplete" in low or "backend" in low or "hop" in low:
                if "no backend" in low or "unbound" in low:
                    self._nav("profiles")
                else:
                    self._nav("backends")
            elif "profile" in low:
                self._nav("profiles")
            return

        if status is None:
            return
        if self._on_toast:
            if status.state == CoreState.CONNECTED:
                proxy = status.local_proxy
                if proxy:
                    self._on_toast(f"Connected · SOCKS {proxy}")
                else:
                    name = (
                        self._services.active_profile().name
                        if self._services.active_profile()
                        else "path"
                    )
                    self._on_toast(f"Connected · {name}")
                if ready is not None and ready.warnings:
                    w0 = ready.warnings[0]
                    self._on_toast(w0[:180] + ("…" if len(w0) > 180 else ""))
            elif status.state == CoreState.UNAVAILABLE:
                self._on_toast(status.message or "Spectre core is offline")
            elif status.state == CoreState.DISCONNECTED:
                self._on_toast(status.message or "Connect failed")
            else:
                self._on_toast(status.message or status.state.value)

    def refresh(self, *, live: bool = False, force_core: bool = False) -> None:
        """Update home chrome from the live core.

        force_core=True skips the CoreClient status TTL (used by the poller).
        live=True runs connect preflight probes (expensive; only on Connect).
        """
        st = self._services.core.status(force=force_core)
        kind = kind_from_core(st.state)
        profile = self._services.active_profile()
        # Structural readiness for display; live TCP/CLI probes stay off the
        # page-switch path so the sidebar stays responsive.
        active = st.state == CoreState.CONNECTED
        ready = self._services.readiness(live=live and not active)

        titles = {
            CoreState.UNAVAILABLE: "Core offline",
            CoreState.DISCONNECTED: "Not connected",
            CoreState.CONNECTING: "Connecting…",
            CoreState.CONNECTED: "Protected",
        }

        env = st.environment or {}
        whonix = bool(env.get("whonix"))
        whonix_role = str(env.get("whonix_role") or "")

        if active:
            # Prefer core path_summary so CLI/tray connect matches the UI.
            if st.path_summary and st.path_summary not in ("No path", ""):
                detail = st.path_summary
                if st.local_proxy:
                    detail = f"{detail} · SOCKS {st.local_proxy}"
            elif st.local_proxy:
                detail = f"Path up · SOCKS {st.local_proxy}"
            else:
                detail = st.message or "Traffic is on the active path."
            # Routing mode / kill switch status from core
            if getattr(st, "routing_active", None):
                detail += " · system routing"
            elif (getattr(st, "routing_mode", None) or "").lower() == "apps":
                detail += " · apps only"
            if st.kill_switch_active:
                detail += " · kill switch"
            elif st.kill_switch is True and st.kill_switch_active is False:
                if st.kill_switch_detail and "apps-only" not in (
                    st.kill_switch_detail or ""
                ):
                    detail += f" · KS off ({st.kill_switch_detail})"
            if ready.warnings:
                # Prefer the exit/rewrite warning over generic Mullvad full-tunnel.
                w0 = ready.warnings[0]
                if "Exit is" in w0 or "exit is" in w0.lower():
                    # Already covered by path caption; keep detail short.
                    pass
                elif "Mullvad" in w0:
                    detail += " · Mullvad full-tunnel"
            if whonix and whonix_role == "workstation":
                detail += " · Whonix"
        elif st.state == CoreState.DISCONNECTED and st.message and st.message not in (
            "Ready",
            "Disconnected",
        ):
            # Show last core error (e.g. connect failure) when useful
            detail = st.message if not ready.ok else st.message
            if not ready.ok:
                detail = ready.summary
        elif not ready.ok:
            detail = ready.summary
        elif st.state == CoreState.UNAVAILABLE:
            detail = "Path configured. Core will start on Connect if installed."
            if whonix:
                detail = "Whonix detected · start spectred, then Connect"
        else:
            detail = {
                CoreState.DISCONNECTED: "Local traffic until you Connect.",
                CoreState.CONNECTING: "Building the path…",
            }.get(st.state, st.message)
            if whonix and whonix_role == "workstation" and st.state == CoreState.DISCONNECTED:
                socks = env.get("tor_socks_host")
                port = env.get("tor_socks_port")
                if socks and port:
                    detail = f"Whonix-Workstation · Gateway Tor {socks}:{port}"

        backends = self._services.backends
        hop_details = getattr(st, "hop_details", None)
        if active and st.hops:
            explain = explain_live(
                list(st.hops),
                hop_details=hop_details if isinstance(hop_details, list) else None,
                profile=profile,
                backends=backends,
            )
        elif profile is not None:
            explain = explain_profile(profile, backends)
        else:
            explain = explain_profile(None, backends)

        show_mv = False
        if profile_uses_mullvad_app_socks(profile, backends):
            show_mv = True
        if show_mv:
            mv = getattr(st, "mullvad", None)
            if isinstance(mv, dict) and mv.get("available") and mv.get("summary"):
                summary = str(mv.get("summary") or "")
                if summary and summary not in detail:
                    detail = f"{detail} · {summary}" if detail else summary
        for k in ("offline", "idle", "busy", "live", "unknown", "bad"):
            self._dot.remove_css_class(f"state-{k}")
        self._dot.add_css_class(f"state-{kind.value}")
        self._title.set_text(titles.get(st.state, st.state.value))
        self._detail.set_text(detail)

        clear_box(self._path_host)
        self._path_host.append(
            path_graph(
                explain.kinds if explain.hops else [],
                live=active,
                empty="Pick or create a path",
                labels=explain.labels or None,
                roles=explain.roles or None,
                sublabels=explain.sublabels or None,
                caption=explain.caption,
            )
        )

        active_id = None
        if active and (st.profile_id or "").strip():
            active_id = (st.profile_id or "").strip()
        elif profile is not None:
            active_id = profile.id
        self._sync_profile_dropdown(active_id)
        # While connected, lock path switch (change path after disconnect).
        self._profile_dd.set_sensitive(
            bool(self._services.profiles.list()) and not active and not self._action_busy
        )

        self._info_btn.set_sensitive(True)
        self._info_btn.set_tooltip_text(
            f"What does “{profile.name}” do?"
            if profile is not None
            else "What does this path do?"
        )

        self._update_next_chip(
            active=active, ready=ready, profile=profile, st=st
        )

        if self._action_busy:
            self._primary.set_sensitive(False)
            return

        if active or st.state == CoreState.CONNECTING:
            self._primary.set_label("Disconnect")
            self._primary.set_sensitive(True)
            self._primary.remove_css_class("suggested-action")
        else:
            self._primary.set_label("Connect")
            # Allow Connect even when incomplete so preflight can guide.
            self._primary.set_sensitive(True)
            self._primary.add_css_class("suggested-action")
