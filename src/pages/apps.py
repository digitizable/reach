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

from collections.abc import Callable

from gi.repository import Adw, Gtk, Pango

from core.apps import RoutedApp
from core.client import CoreState
from core.launcher import launch_app, probe_exclude_tooling
from services import Services
from widgets.chrome import clear_box, page_header, scroll_body


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

        add_btn = Gtk.Button()
        add_btn.set_icon_name("list-add-symbolic")
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add custom command")
        add_btn.connect("clicked", self._on_add_command)
        self.append(page_header("Exclude from path", end=add_btn))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.add_css_class("page-body")
        body.set_valign(Gtk.Align.START)

        self._hint = Gtk.Label(label="", wrap=True, xalign=0)
        self._hint.add_css_class("muted")
        body.append(self._hint)

        self._callout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._callout.add_css_class("apps-callout")
        self._callout.set_visible(False)

        self._callout_label = Gtk.Label(label="", wrap=True, xalign=0)
        self._callout_label.add_css_class("apps-callout-text")
        self._callout.append(self._callout_label)

        callout_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        callout_actions.set_halign(Gtk.Align.START)

        self._switch_system_btn = Gtk.Button(label="Use system routing")
        self._switch_system_btn.add_css_class("suggested-action")
        self._switch_system_btn.set_tooltip_text(
            "Switch Settings → Routing mode to Entire system "
            "(exclude list applies after next Connect)"
        )
        self._switch_system_btn.connect("clicked", self._on_switch_system)
        callout_actions.append(self._switch_system_btn)

        self._settings_btn = Gtk.Button(label="Open Settings")
        self._settings_btn.add_css_class("flat")
        self._settings_btn.connect("clicked", self._on_open_settings)
        callout_actions.append(self._settings_btn)

        self._callout.append(callout_actions)
        body.append(self._callout)

        self._status = Gtk.Label(label="", xalign=0, wrap=True)
        self._status.add_css_class("muted")
        body.append(self._status)

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
        body.append(search_row)

        self._empty = Gtk.Label(
            label="No applications found.",
            justify=Gtk.Justification.CENTER,
        )
        self._empty.add_css_class("muted")
        self._empty.set_margin_top(16)
        self._empty.set_halign(Gtk.Align.CENTER)
        body.append(self._empty)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._list.add_css_class("profile-list")
        self._list_scroll = Gtk.ScrolledWindow()
        self._list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._list_scroll.set_hexpand(True)
        self._list_scroll.set_vexpand(False)
        self._list_scroll.set_propagate_natural_height(True)
        self._list_scroll.set_child(self._list)
        body.append(self._list_scroll)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions.set_halign(Gtk.Align.END)
        actions.set_margin_top(4)

        self._launch_btn = Gtk.Button(label="Exclude (clearnet)")
        self._launch_btn.add_css_class("suggested-action")
        self._launch_btn.set_sensitive(False)
        self._launch_btn.set_tooltip_text(
            "Launch this app outside Spectre (clearnet netns or exclude marks)"
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
        body.append(actions)

        self.append(scroll_body(body, margin=12))
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

    def _routing_mode(self) -> str:
        mode = (self._services.config.routing_mode or "system").strip().lower()
        return "apps" if mode == "apps" else "system"

    def _tooling(self):
        if self._tooling_cache is None:
            # check_sudo can take ~1s once; cache for the page session
            self._tooling_cache = probe_exclude_tooling(check_sudo=True)
        return self._tooling_cache

    def _refresh_hint(self) -> None:
        base = (
            "Exclude list: open an app here to run it on clearnet while the rest "
            "of the machine stays on Spectre (system routing). Preferred path is "
            "the clearnet network namespace (clearnet-run); fallback is "
            "mullvad-exclude marks that Spectre already honors."
        )
        if self._routing_mode() == "system":
            extra = " Routing is Entire system — exclude is the split-tunnel carve-out."
        else:
            extra = (
                " Routing is Selected apps only — the machine is already clearnet "
                "from Spectre; exclude still launches clearnet isolation but is "
                "usually unnecessary."
            )
        self._hint.set_text(base + extra)

    def _refresh_callout(self) -> None:
        tools = self._tooling()
        mode = self._routing_mode()

        if not tools.any_ready:
            self._callout.set_visible(True)
            self._callout_label.set_text(
                "No working exclude helper. "
                + tools.summary()
                + ". One-time (from the Spectre core install): spectre setup-clearnet "
                "— installs clearnet netns, clearnet-run, sudoers, and boot unit. "
                "Never teardown while agents use the netns. Fallback: mullvad-exclude."
            )
            self._switch_system_btn.set_visible(False)
            self._settings_btn.set_visible(True)
            return

        if mode == "apps":
            self._callout.set_visible(True)
            self._callout_label.set_text(
                "Selected apps only is on — Spectre does not system-redirect "
                "traffic. Exclude still works for isolation, but the usual "
                "model is Entire system + exclude carve-outs. Helper: "
                + tools.summary()
                + "."
            )
            self._switch_system_btn.set_visible(True)
            self._settings_btn.set_visible(True)
            return

        # system mode + tools OK — only warn if using fallback or partial setup
        if tools.can_clearnet_run:
            self._callout.set_visible(False)
            self._switch_system_btn.set_visible(False)
            return

        # Fallback-only
        self._callout.set_visible(True)
        self._callout_label.set_text(
            "Using mark-based exclude ("
            + tools.summary()
            + "). For full netns isolation, set up clearnet-run + passwordless "
            "sudo. Spectre already skips Mullvad exclusion marks and cn-host."
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
        names = self._services.launch_session.names()
        mode = (
            "system routing (exclude list)"
            if self._routing_mode() == "system"
            else "apps only"
        )
        base = f"{n_sys} installed"
        if n_custom:
            base += f" · {n_custom} custom"
        base += f" · {mode}"
        base += f" · {tools.summary()}"
        if n_open:
            shown = ", ".join(names[:3])
            if n_open > 3:
                shown += f" +{n_open - 3}"
            base += f" · {n_open} excluded ({shown})"
        if st.state == CoreState.CONNECTED and st.local_proxy:
            self._status.set_text(f"{base} · path up · SOCKS {st.local_proxy}")
        elif st.state == CoreState.CONNECTED:
            self._status.set_text(f"{base} · path up")
        else:
            self._status.set_text(f"{base} · path down")
        self._update_action_sensitivity()

    def reload(self) -> None:
        apps = self._services.apps.list(
            enabled_only=not self._show_disabled,
            include_system=True,
            query=self._filter,
        )
        self._empty.set_visible(len(apps) == 0)
        self._list_scroll.set_visible(len(apps) > 0)
        self._list.set_visible(len(apps) > 0)
        clear_box(self._list)
        self._row_buttons.clear()

        self.refresh_status_line()

        if not apps:
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

    def _update_list_viewport(self, *, has_selection: bool) -> None:
        _SELECTED_MAX = 220
        if has_selection:
            self._list_scroll.set_max_content_height(_SELECTED_MAX)
            self._list_scroll.set_size_request(-1, _SELECTED_MAX)
            self._list_scroll.set_vexpand(False)
        else:
            self._list_scroll.set_max_content_height(-1)
            self._list_scroll.set_size_request(-1, -1)
            self._list_scroll.set_vexpand(True)

    def _update_action_sensitivity(self) -> None:
        has = self._selected_id is not None and self._selected_id in self._row_buttons
        tools = self._tooling()
        can_open = bool(has) and tools.any_ready
        self._update_list_viewport(has_selection=has)
        self._launch_btn.set_sensitive(can_open)
        if can_open:
            self._launch_btn.add_css_class("suggested-action")
            if tools.can_clearnet_run:
                self._launch_btn.set_tooltip_text(
                    f"Launch outside Spectre via clearnet-run (netns {tools.netns_name})"
                )
            else:
                self._launch_btn.set_tooltip_text(
                    "Launch outside Spectre via mullvad-exclude (mark-based clearnet)"
                )
        else:
            self._launch_btn.remove_css_class("suggested-action")
            if not has:
                self._launch_btn.set_tooltip_text("Select an app to exclude from the path")
            else:
                self._launch_btn.set_tooltip_text(
                    "No exclude helper ready — see the notice above"
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
        # Fresh probe at launch so sudo/netns state is current
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
        else:
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
