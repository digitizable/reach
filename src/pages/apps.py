"""Apps — exclude-list split tunnel (clearnet netns / marks).

Contract (with Routing mode → Entire system):
  - Whole machine uses Spectre after Connect.
  - Apps opened here are *excluded* — clearnet via clearnet-run netns
    (preferred) or mullvad-exclude marks (fallback).
  - Spectre KS/sysroute already skip those marks and allow cn-host.

With Selected apps only, the machine is already clearnet from Spectre’s
point of view; exclude is still available but usually unnecessary.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from gi.repository import Adw, GLib, Gtk, Pango

from core.apps import RoutedApp
from core.client import CoreState
from core.launcher import launch_app, probe_exclude_tooling
from services import Services
from widgets.chrome import clear_box, page_header


class AppsPage(Gtk.Box):
    def __init__(
        self,
        services: Services,
        *,
        parent_window: Gtk.Window | None = None,
        on_toast: Callable[[str], None] | None = None,
        on_navigate: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("page")
        self.add_css_class("apps-page")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._services = services
        self._parent_window = parent_window
        self._on_toast = on_toast
        self._on_navigate = on_navigate
        self._selected_id: str | None = None
        self._row_buttons: dict[str, Gtk.CheckButton] = {}
        self._filter = ""
        self._show_disabled = False
        self._selection_guard = False
        self._tooling_cache = None  # set on refresh
        self._clearnet_busy = False

        add_btn = Gtk.Button()
        add_btn.set_icon_name("list-add-symbolic")
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add custom command")
        add_btn.connect("clicked", self._on_add_command)
        self.append(page_header("Exclude from path", end=add_btn))

        # Compact top chrome — long copy was eating the 600px window.
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        top.add_css_class("apps-top")
        top.set_margin_start(12)
        top.set_margin_end(12)
        top.set_margin_top(8)
        top.set_vexpand(False)

        self._hint = Gtk.Label(label="", wrap=True, xalign=0)
        self._hint.add_css_class("muted")
        self._hint.set_ellipsize(Pango.EllipsizeMode.END)
        self._hint.set_lines(2)
        self._hint.set_tooltip_text(
            "Launch apps outside Spectre (clearnet). Preferred: clearnet-run "
            "netns. Fallback: mullvad-exclude. Setup once: spectre setup-clearnet. "
            "Firefox: first exclude copies your default profile into a Spectre-only "
            "profile (under this user account); menu Firefox keeps the real default. "
            "Spotify: first exclude copies ~/.config/spotify (login) into a "
            "Spectre-only config so the clearnet instance stays signed in."
        )
        top.append(self._hint)

        self._callout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._callout.add_css_class("apps-callout")
        self._callout.set_visible(False)

        self._callout_label = Gtk.Label(label="", wrap=True, xalign=0)
        self._callout_label.add_css_class("apps-callout-text")
        self._callout_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._callout_label.set_lines(3)
        self._callout.append(self._callout_label)

        callout_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        callout_actions.set_halign(Gtk.Align.START)

        self._switch_system_btn = Gtk.Button(label="Use system routing")
        self._switch_system_btn.add_css_class("suggested-action")
        self._switch_system_btn.set_tooltip_text(
            "Switch Settings → Routing mode to Entire system"
        )
        self._switch_system_btn.connect("clicked", self._on_switch_system)
        callout_actions.append(self._switch_system_btn)

        self._settings_btn = Gtk.Button(label="Settings")
        self._settings_btn.add_css_class("flat")
        self._settings_btn.connect("clicked", self._on_open_settings)
        callout_actions.append(self._settings_btn)

        self._callout.append(callout_actions)
        top.append(self._callout)

        self._status = Gtk.Label(label="", xalign=0)
        self._status.add_css_class("muted")
        self._status.set_ellipsize(Pango.EllipsizeMode.END)
        self._status.set_single_line_mode(True)
        top.append(self._status)

        # Clearnet path health (veth/NAT used by Exclude)
        clearnet_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        clearnet_row.set_hexpand(True)
        self._clearnet_label = Gtk.Label(label="Clearnet: not checked", xalign=0)
        self._clearnet_label.add_css_class("muted")
        self._clearnet_label.set_hexpand(True)
        self._clearnet_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._clearnet_label.set_tooltip_text(
            "Health of the clearnet netns path used by Exclude "
            "(veth + host NAT to Wi‑Fi/Ethernet, not Mullvad)."
        )
        clearnet_row.append(self._clearnet_label)

        self._clearnet_check_btn = Gtk.Button(label="Check")
        self._clearnet_check_btn.add_css_class("flat")
        self._clearnet_check_btn.set_tooltip_text(
            "Probe clearnet netns: health, ping, DNS, short speed sample"
        )
        self._clearnet_check_btn.connect("clicked", self._on_clearnet_check)
        clearnet_row.append(self._clearnet_check_btn)

        self._clearnet_repair_btn = Gtk.Button(label="Repair")
        self._clearnet_repair_btn.add_css_class("flat")
        self._clearnet_repair_btn.set_tooltip_text(
            "Refresh clearnet nft + DNS (safe when healthy — no process kill). "
            "Needs passwordless clearnet-netns from spectre setup-clearnet."
        )
        self._clearnet_repair_btn.connect("clicked", self._on_clearnet_repair)
        clearnet_row.append(self._clearnet_repair_btn)
        top.append(clearnet_row)

        # Custom linear bar (not Gtk.ProgressBar — libadwaita/themes often
        # render that as a circular activity spinner). Hidden until Check/Repair.
        self._progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self._progress_box.set_hexpand(True)
        self._progress_box.add_css_class("apps-progress")
        self._progress_box.set_visible(False)
        self._progress_label = Gtk.Label(label="", xalign=0)
        self._progress_label.add_css_class("apps-progress-label")
        self._progress_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._progress_box.append(self._progress_label)

        self._progress_trough = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._progress_trough.add_css_class("apps-progress-trough")
        self._progress_trough.set_hexpand(True)
        self._progress_trough.set_size_request(-1, 14)
        self._progress_trough.set_overflow(Gtk.Overflow.HIDDEN)

        self._progress_fill = Gtk.Box()
        self._progress_fill.add_css_class("apps-progress-fill")
        self._progress_fill.set_size_request(0, 14)
        self._progress_fill.set_halign(Gtk.Align.START)
        self._progress_fill.set_valign(Gtk.Align.FILL)
        self._progress_trough.append(self._progress_fill)
        # Keep fill width in sync when the window resizes.
        self._progress_trough.connect("notify::width-request", self._on_progress_trough_size)
        self._progress_trough.connect("map", self._on_progress_trough_size)
        self._progress_box.append(self._progress_trough)
        self._progress_fraction = 0.0
        self._progress_hide_id: int | None = None
        top.append(self._progress_box)

        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._search = Gtk.SearchEntry()
        self._search.set_hexpand(True)
        self._search.set_placeholder_text("Search apps…")
        self._search.connect("search-changed", self._on_search)
        search_row.append(self._search)

        refresh = Gtk.Button()
        refresh.set_icon_name("view-refresh-symbolic")
        refresh.add_css_class("flat")
        refresh.set_tooltip_text("Rescan installed applications")
        refresh.connect("clicked", self._on_refresh)
        search_row.append(refresh)
        top.append(search_row)
        self.append(top)

        # List + action bar in one overlay so buttons always sit on the list.
        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._list.add_css_class("profile-list")

        self._list_scroll = Gtk.ScrolledWindow()
        self._list_scroll.add_css_class("apps-list-scroll")
        self._list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._list_scroll.set_hexpand(True)
        self._list_scroll.set_vexpand(True)
        self._list_scroll.set_propagate_natural_height(False)
        self._list_scroll.set_propagate_natural_width(False)
        self._list_scroll.set_child(self._list)

        self._empty = Gtk.Label(
            label="No applications found.",
            justify=Gtk.Justification.CENTER,
        )
        self._empty.add_css_class("muted")
        self._empty.set_halign(Gtk.Align.CENTER)
        self._empty.set_valign(Gtk.Align.CENTER)
        self._empty.set_vexpand(True)

        list_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        list_area.set_hexpand(True)
        list_area.set_vexpand(True)
        list_area.set_margin_start(12)
        list_area.set_margin_end(12)
        list_area.append(self._empty)
        list_area.append(self._list_scroll)

        # Bottom action bar — always visible under the list (not overlaid on rows).
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions.add_css_class("apps-actions")
        actions.set_halign(Gtk.Align.FILL)
        actions.set_hexpand(True)
        actions.set_vexpand(False)
        actions.set_margin_start(12)
        actions.set_margin_end(12)
        actions.set_margin_top(6)
        actions.set_margin_bottom(10)

        # Spacer pushes buttons to the end while keeping the bar full-width.
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        actions.append(spacer)

        self._launch_btn = Gtk.Button(label="Exclude (clearnet)")
        self._launch_btn.add_css_class("suggested-action")
        self._launch_btn.set_sensitive(False)
        self._launch_btn.set_tooltip_text(
            "Start a separate clearnet instance (does not take over the "
            "normal/system-routed window)"
        )
        self._launch_btn.connect("clicked", self._on_launch)
        actions.append(self._launch_btn)

        self._toggle_btn = Gtk.Button(label="Hide")
        self._toggle_btn.add_css_class("flat")
        self._toggle_btn.set_sensitive(False)
        self._toggle_btn.set_tooltip_text("Hide from list (system) or disable (custom)")
        self._toggle_btn.connect("clicked", self._on_toggle)
        actions.append(self._toggle_btn)

        self._del_btn = Gtk.Button(label="Remove")
        self._del_btn.add_css_class("flat")
        self._del_btn.set_sensitive(False)
        self._del_btn.set_tooltip_text("Remove custom command")
        self._del_btn.connect("clicked", self._on_delete)
        actions.append(self._del_btn)

        self.append(list_area)
        self.append(actions)

        self.reload()

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        self._filter = entry.get_text() or ""
        self.reload()

    def _on_refresh(self, *_a) -> None:
        self._services.apps.invalidate_system_cache()
        self._tooling_cache = None
        self.reload()
        n = self._services.apps.count_system()
        self._toast(f"Found {n} installed application{'s' if n != 1 else ''}")

    def _on_progress_trough_size(self, *_a) -> None:
        self._apply_progress_fill_width()

    def _apply_progress_fill_width(self) -> None:
        """Set fill width from fraction × trough allocation (true horizontal bar)."""
        w = 0
        try:
            w = int(self._progress_trough.get_width() or 0)
        except Exception:
            w = 0
        if w <= 1:
            try:
                w = max(w, int(self._progress_trough.get_allocated_width() or 0))
            except Exception:
                pass
        if w <= 1:
            try:
                alloc = self._progress_trough.get_allocation()
                w = max(w, int(getattr(alloc, "width", 0) or 0))
            except Exception:
                pass
        if w <= 1:
            w = 300  # fallback before first allocate
        if self._progress_fraction <= 0:
            fill_w = 0
        else:
            fill_w = max(3, int(w * self._progress_fraction))
        self._progress_fill.set_size_request(fill_w, 14)
        try:
            self._progress_fill.queue_resize()
            self._progress_trough.queue_draw()
        except Exception:
            pass

    def _cancel_progress_hide(self) -> None:
        if self._progress_hide_id is not None:
            try:
                GLib.source_remove(self._progress_hide_id)
            except Exception:
                pass
            self._progress_hide_id = None

    def _hide_progress_bar(self) -> bool:
        """GLib timeout: collapse the bar after a finished run."""
        self._progress_hide_id = None
        if self._clearnet_busy:
            return False
        self._progress_fraction = 0.0
        self._progress_label.set_text("")
        self._apply_progress_fill_width()
        self._progress_box.set_visible(False)
        return False

    def _set_progress(
        self,
        fraction: float,
        *,
        text: str = "",
        busy: bool | None = None,
    ) -> None:
        """Linear horizontal progress (0..1). Visible only while active/done flash."""
        if busy is not None:
            self._clearnet_busy = busy
            self._clearnet_check_btn.set_sensitive(not busy)
            if busy:
                self._clearnet_repair_btn.set_sensitive(False)
                self._cancel_progress_hide()
                self._progress_box.set_visible(True)
        self._progress_fraction = max(0.0, min(1.0, float(fraction)))
        if text:
            self._progress_label.set_text(text)
        self._apply_progress_fill_width()
        # After finish (or fail), flash the final state briefly then hide.
        if busy is False:
            self._cancel_progress_hide()
            delay_ms = 700 if self._progress_fraction >= 1.0 else 400
            self._progress_hide_id = GLib.timeout_add(delay_ms, self._hide_progress_bar)

    def _progress_from_worker(self, fraction: float, label: str) -> None:
        """Marshal progress updates from a background thread to GTK."""

        def _apply() -> bool:
            self._set_progress(fraction, text=label, busy=True)
            return False

        GLib.idle_add(_apply)

    def _on_clearnet_check(self, *_a) -> None:
        if self._clearnet_busy:
            return
        self._clearnet_label.set_text("Clearnet: checking…")
        self._set_progress(0.0, text="Starting…", busy=True)

        def worker() -> None:
            from core.clearnet_health import check_clearnet

            err: str | None = None
            h = None
            try:
                h = check_clearnet(
                    try_helper=True,
                    on_progress=self._progress_from_worker,
                )
            except Exception as exc:
                err = str(exc) or repr(exc)

            def done() -> bool:
                if err is not None:
                    self._set_progress(0.0, text="Failed", busy=False)
                    self._clearnet_label.set_text(f"Clearnet: error · {err}")
                    from core.clearnet_health import find_clearnet_netns

                    self._clearnet_repair_btn.set_sensitive(
                        bool(find_clearnet_netns())
                    )
                    self._toast(err)
                    return False
                assert h is not None
                self._set_progress(1.0, text="Done", busy=False)
                self._clearnet_label.set_text(f"Clearnet: {h.summary}")
                self._clearnet_label.set_tooltip_text(
                    "\n".join(h.detail_lines) if h.detail_lines else h.summary
                )
                self._clearnet_repair_btn.set_sensitive(h.can_repair)
                self._toast(h.summary)
                return False

            GLib.idle_add(done)

        threading.Thread(target=worker, name="spectre-clearnet-check", daemon=True).start()

    def _on_clearnet_repair(self, *_a) -> None:
        if self._clearnet_busy:
            return
        self._clearnet_label.set_text("Clearnet: repairing…")
        self._set_progress(0.0, text="Starting repair…", busy=True)

        def worker() -> None:
            from core.clearnet_health import repair_clearnet

            err: str | None = None
            msg = ""
            try:
                _ok, msg = repair_clearnet(on_progress=self._progress_from_worker)
            except Exception as exc:
                err = str(exc) or repr(exc)

            def done() -> bool:
                if err is not None:
                    self._set_progress(0.0, text="Failed", busy=False)
                    self._clearnet_label.set_text(f"Clearnet: error · {err}")
                    self._clearnet_repair_btn.set_sensitive(True)
                    self._toast(err)
                    return False
                self._set_progress(1.0, text="Done", busy=False)
                self._clearnet_label.set_text(f"Clearnet: {msg}")
                self._toast(msg)
                # Follow with a full check (also linear progress)
                self._on_clearnet_check()
                return False

            GLib.idle_add(done)

        threading.Thread(target=worker, name="spectre-clearnet-repair", daemon=True).start()

    def _routing_mode(self) -> str:
        mode = (self._services.config.routing_mode or "system").strip().lower()
        return "apps" if mode == "apps" else "system"

    def _tooling(self):
        if self._tooling_cache is None:
            self._tooling_cache = probe_exclude_tooling(check_sudo=True)
        return self._tooling_cache

    def _refresh_hint(self) -> None:
        if self._routing_mode() == "system":
            self._hint.set_text(
                "Starts a separate clearnet instance — keep using the normal app "
                "on Spectre from the menu."
            )
        else:
            self._hint.set_text(
                "Selected apps only is on — machine is already clearnet from Spectre."
            )

    def _refresh_callout(self) -> None:
        tools = self._tooling()
        mode = self._routing_mode()

        if not tools.any_ready:
            self._callout.set_visible(True)
            self._callout_label.set_text(
                "No exclude helper ready. Run: spectre setup-clearnet  "
                f"({tools.summary()})"
            )
            self._switch_system_btn.set_visible(False)
            self._settings_btn.set_visible(True)
            return

        if mode == "apps":
            self._callout.set_visible(True)
            self._callout_label.set_text(
                "Prefer Entire system routing + exclude carve-outs. "
                f"{tools.summary()}"
            )
            self._switch_system_btn.set_visible(True)
            self._settings_btn.set_visible(True)
            return

        if tools.can_clearnet_run:
            self._callout.set_visible(False)
            self._switch_system_btn.set_visible(False)
            return

        self._callout.set_visible(True)
        self._callout_label.set_text(
            f"Fallback exclude only ({tools.summary()}). "
            "For netns: spectre setup-clearnet"
        )
        self._switch_system_btn.set_visible(False)
        self._settings_btn.set_visible(True)

    def _on_switch_system(self, *_a) -> None:
        cfg = self._services.config
        if (cfg.routing_mode or "").strip().lower() != "apps":
            self._toast("Already on Entire system routing")
            return
        cfg.routing_mode = "system"
        self._services.save_config()
        st = self._services.core.status()
        if st.state == CoreState.CONNECTED:
            self._toast(
                "Routing set to Entire system — Disconnect and Connect again "
                "for it to apply"
            )
        else:
            self._toast("Routing set to Entire system — applies on Connect")
        self.refresh_status_line()

    def _on_open_settings(self, *_a) -> None:
        if self._on_navigate:
            self._on_navigate("settings")

    def refresh_status_line(self) -> None:
        """Update hint + callout + status without rebuilding the app list."""
        self._refresh_hint()
        self._refresh_callout()
        st = self._services.core.status()
        tools = self._tooling()
        n_sys = self._services.apps.count_system()
        n_custom = len(self._services.apps.list(include_system=False))
        n_open = self._services.launch_session.active_count()
        mode = "system" if self._routing_mode() == "system" else "apps-only"
        helper = "netns" if tools.can_clearnet_run else (
            "marks" if tools.can_mullvad_exclude else "none"
        )
        parts = [f"{n_sys} apps", mode, helper]
        if n_custom:
            parts.insert(1, f"{n_custom} custom")
        if n_open:
            parts.append(f"{n_open} open")
        if st.state == CoreState.CONNECTED and st.local_proxy:
            parts.append(f"up · {st.local_proxy}")
        elif st.state == CoreState.CONNECTED:
            parts.append("path up")
        else:
            parts.append("path down")
        self._status.set_text(" · ".join(parts))
        self._status.set_tooltip_text(tools.summary())
        self._update_action_sensitivity()

    def reload(self) -> None:
        apps = self._services.apps.list(
            enabled_only=not self._show_disabled,
            include_system=True,
            query=self._filter,
        )
        empty = len(apps) == 0
        self._empty.set_visible(empty)
        self._list_scroll.set_visible(not empty)
        clear_box(self._list)
        self._row_buttons.clear()

        self.refresh_status_line()

        if empty:
            if self._filter.strip():
                self._empty.set_text(f"No apps match “{self._filter.strip()}”.")
            else:
                self._empty.set_text(
                    "No applications detected.\n"
                    "Install desktop apps or add a custom command with +."
                )

        for app in apps:
            btn = self._make_row(app)
            if self._selected_id == app.id:
                self._set_row_active(btn, True)
            self._row_buttons[app.id] = btn
            self._list.append(btn)

        has = self._selected_id is not None and self._selected_id in self._row_buttons
        if not has:
            self._selected_id = None
        self._update_action_sensitivity()
        if has and self._selected_id:
            self._scroll_selected_into_view()

    def _update_action_sensitivity(self) -> None:
        has = self._selected_id is not None and self._selected_id in self._row_buttons
        tools = self._tooling()
        can_open = bool(has) and tools.any_ready
        self._launch_btn.set_sensitive(can_open)
        if can_open:
            self._launch_btn.add_css_class("suggested-action")
            if tools.can_clearnet_run:
                self._launch_btn.set_tooltip_text(
                    f"New clearnet instance via clearnet-run (netns {tools.netns_name}). "
                    "Your normal menu launch stays on Spectre."
                )
            else:
                self._launch_btn.set_tooltip_text(
                    "New clearnet instance via mullvad-exclude. "
                    "Your normal menu launch stays on Spectre."
                )
        else:
            self._launch_btn.remove_css_class("suggested-action")
            if not has:
                self._launch_btn.set_tooltip_text("Select an app to exclude from the path")
            else:
                self._launch_btn.set_tooltip_text(
                    "No exclude helper ready — run spectre setup-clearnet"
                )
        self._toggle_btn.set_sensitive(has)
        app = self._services.apps.get(self._selected_id) if self._selected_id else None
        if app is None:
            self._del_btn.set_sensitive(False)
            return
        self._del_btn.set_sensitive(app.is_custom)
        if app.is_system:
            self._toggle_btn.set_label("Hide" if app.enabled else "Show")
        else:
            self._toggle_btn.set_label("Disable" if app.enabled else "Enable")

    def _make_row(self, app: RoutedApp) -> Gtk.CheckButton:
        btn = Gtk.CheckButton()
        btn.add_css_class("profile-row")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_hexpand(True)

        icon = Gtk.Image()
        icon.set_pixel_size(24)
        icon_name = app.icon_name or "application-x-executable-symbolic"
        if icon_name.startswith("/"):
            try:
                icon.set_from_file(icon_name)
            except Exception:
                icon.set_from_icon_name("application-x-executable-symbolic")
        else:
            icon.set_from_icon_name(icon_name)
        icon.set_valign(Gtk.Align.CENTER)
        row.append(icon)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        content.set_hexpand(True)
        title = Gtk.Label(label=app.name, xalign=0)
        title.add_css_class("profile-row-name")
        content.append(title)
        origin = "custom" if app.is_custom else "installed"
        meta = f"{app.command} · exclude · {origin}"
        if not app.enabled:
            meta += " · off"
        detail = Gtk.Label(label=meta, xalign=0)
        detail.add_css_class("profile-row-hops")
        detail.set_ellipsize(Pango.EllipsizeMode.END)
        content.append(detail)
        row.append(content)

        btn.set_child(row)
        btn.connect("toggled", self._on_toggled, app.id)
        return btn

    def _scroll_selected_into_view(self) -> None:
        if not self._selected_id:
            return
        btn = self._row_buttons.get(self._selected_id)
        if btn is None:
            return
        try:
            btn.grab_focus()
        except Exception:
            pass

    def _set_row_active(self, btn: Gtk.CheckButton, active: bool) -> None:
        self._selection_guard = True
        try:
            btn.set_active(active)
        finally:
            self._selection_guard = False

    def _on_toggled(self, button: Gtk.CheckButton, app_id: str) -> None:
        if self._selection_guard:
            return
        if button.get_active():
            for other_id, other in self._row_buttons.items():
                if other_id != app_id and other.get_active():
                    self._set_row_active(other, False)
            self._selected_id = app_id
            self._update_action_sensitivity()
            self._scroll_selected_into_view()
            return
        if self._selected_id == app_id:
            self._selected_id = None
            self._update_action_sensitivity()

    def _toast(self, msg: str) -> None:
        if self._on_toast:
            self._on_toast(msg)

    def _on_add_command(self, *_a) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self._parent_window,
            heading="Add custom command",
            body=(
                "For tools without a desktop entry. "
                "Launched excluded from Spectre (clearnet)."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8)
        name = Gtk.Entry()
        name.set_placeholder_text("Display name")
        box.append(name)
        cmd = Gtk.Entry()
        cmd.set_placeholder_text("Command (e.g. curl https://example.com)")
        box.append(cmd)
        dialog.set_extra_child(box)

        def on_response(_d: Adw.MessageDialog, response: str) -> None:
            if response != "add":
                return
            try:
                app = self._services.apps.create(
                    name=name.get_text().strip() or cmd.get_text().strip().split()[0],
                    command=cmd.get_text().strip(),
                    mode="env",
                )
            except (ValueError, IndexError) as exc:
                self._toast(str(exc) if str(exc) else "Invalid command")
                return
            self._selected_id = app.id
            self.reload()
            self._toast(f"Added “{app.name}”")

        dialog.connect("response", on_response)
        dialog.present()

    def _on_launch(self, *_a) -> None:
        if not self._selected_id:
            return
        app = self._services.apps.get(self._selected_id)
        if app is None:
            return
        if not app.enabled:
            self._toast("App is hidden/disabled — enable it first")
            return
        self._tooling_cache = None
        tools = self._tooling()
        result = launch_app(
            app,
            self._services.core,
            session=self._services.launch_session,
            tooling=tools,
        )
        self._toast(result.message)
        if result.ok:
            self._services.log(
                f"Excluded app {app.name} method={result.method} pid={result.pid}"
            )
        self.refresh_status_line()

    def _on_toggle(self, *_a) -> None:
        if not self._selected_id:
            return
        app = self._services.apps.get(self._selected_id)
        if app is None:
            return
        was_enabled = app.enabled
        self._services.apps.update(app.id, enabled=not was_enabled)
        if was_enabled and app.is_system:
            self._selected_id = None
        self.reload()
        if app.is_system:
            self._toast("Hidden" if was_enabled else "Shown")
        else:
            self._toast("Disabled" if was_enabled else "Enabled")

    def _on_delete(self, *_a) -> None:
        if not self._selected_id:
            return
        app = self._services.apps.get(self._selected_id)
        if app is None:
            return
        if not app.is_custom:
            self._toast("Installed apps can’t be removed — use Hide instead")
            return
        name = app.name
        if not self._services.apps.delete(self._selected_id):
            return
        self._selected_id = None
        self.reload()
        self._toast(f"Removed “{name}”")
