"""Apps — open programs via Spectre’s local SOCKS.

Simple contract:
  - Open an app here anytime (even before Connect) — it is pointed at Spectre SOCKS.
  - While connected, cooperative apps use the path.
  - While disconnected, those apps keep running but network via SOCKS fails
    (until you Connect again). They are not killed.

This is not a split-tunnel membership list. Apps opened from the normal
menu/dock are unchanged.
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk, Pango

from core.apps import RoutedApp
from core.client import CoreState
from core.launcher import launch_app
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

        add_btn = Gtk.Button()
        add_btn.set_icon_name("list-add-symbolic")
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add custom command")
        add_btn.connect("clicked", self._on_add_command)
        self.append(page_header("Open via Spectre", end=add_btn))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.add_css_class("page-body")
        body.set_valign(Gtk.Align.START)

        self._hint = Gtk.Label(label="", wrap=True, xalign=0)
        self._hint.add_css_class("muted")
        body.append(self._hint)

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

        # List scrolls on its own. When an app is selected, cap height so the
        # Open / actions row below stays on screen (no trek past 100 apps).
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

        self._launch_btn = Gtk.Button(label="Open on path")
        self._launch_btn.add_css_class("suggested-action")
        self._launch_btn.set_sensitive(False)
        self._launch_btn.set_tooltip_text(
            "Start this app via Spectre SOCKS. Works while connected; "
            "while disconnected the app stays open but has no path network."
        )
        self._launch_btn.connect("clicked", self._on_launch)
        actions.append(self._launch_btn)

        self._mode_btn = Gtk.Button(label="Use proxychains")
        self._mode_btn.add_css_class("flat")
        self._mode_btn.set_sensitive(False)
        self._mode_btn.set_tooltip_text(
            "Toggle env proxy vs proxychains (for apps that ignore proxy env)"
        )
        self._mode_btn.connect("clicked", self._on_toggle_mode)
        actions.append(self._mode_btn)

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
        self.reload()
        n = self._services.apps.count_system()
        self._toast(f"Found {n} installed application{'s' if n != 1 else ''}")

    def _routing_mode(self) -> str:
        mode = (self._services.config.routing_mode or "system").strip().lower()
        return "apps" if mode == "apps" else "system"

    def _path_up(self) -> bool:
        """True when Spectre is fully connected with a local SOCKS address."""
        st = self._services.core.status()
        if st.state != CoreState.CONNECTED:
            return False
        return bool((st.local_proxy or "").strip())

    def _refresh_hint(self) -> None:
        """One contract, short mode note."""
        base = (
            "Open an app anytime — it is pointed at Spectre SOCKS (you can open "
            "before Connect). While the path is up, it uses Spectre. If you "
            "Disconnect, the app stays open but network via SOCKS stops until "
            "you Connect again. Apps started from the normal menu are unaffected."
        )
        if self._routing_mode() == "apps":
            extra = (
                " Routing is “Launched apps / SOCKS only”: the rest of the machine "
                "stays on clearnet unless something else uses Spectre SOCKS."
            )
        else:
            extra = (
                " Routing is Entire system: after Connect the whole machine uses "
                "Spectre; this page still aims specific apps at SOCKS explicitly."
            )
        self._hint.set_text(base + extra)

    def refresh_status_line(self) -> None:
        """Update hint + path/count line without rebuilding the app list."""
        self._refresh_hint()
        st = self._services.core.status()
        n_sys = self._services.apps.count_system()
        n_custom = len(self._services.apps.list(include_system=False))
        n_open = self._services.launch_session.active_count()
        mode = "apps only" if self._routing_mode() == "apps" else "system routing"
        base = f"{n_sys} installed"
        if n_custom:
            base += f" · {n_custom} custom"
        base += f" · {mode}"
        if n_open:
            base += f" · {n_open} open on path"
        if st.state == CoreState.CONNECTED and st.local_proxy:
            self._status.set_text(f"{base} · path up · SOCKS {st.local_proxy}")
        elif st.state == CoreState.CONNECTED:
            self._status.set_text(f"{base} · path up · waiting for local SOCKS")
        else:
            bind = (self._services.config.bind_address or "127.0.0.1").strip()
            self._status.set_text(
                f"{base} · path down · open apps now (SOCKS {bind}:10808) or Connect first"
            )
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
            # Independent toggles (not a radio group) so click-again deselects.
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
        """When something is selected, shrink the list so actions stay visible."""
        # ~6–7 rows; enough to see context, short enough that Open stays on screen.
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
        path_up = self._path_up()
        # Allow open while disconnected (SOCKS env points at Spectre in advance).
        can_open = bool(has)
        self._update_list_viewport(has_selection=has)
        self._launch_btn.set_sensitive(can_open)
        if can_open:
            self._launch_btn.add_css_class("suggested-action")
            if path_up:
                self._launch_btn.set_tooltip_text(
                    "Start this app via Spectre SOCKS (path is up)."
                )
            else:
                self._launch_btn.set_tooltip_text(
                    "Start via Spectre SOCKS now — network works after you Connect. "
                    "While disconnected, the app has no path network."
                )
        else:
            self._launch_btn.remove_css_class("suggested-action")
            self._launch_btn.set_tooltip_text("Select an app to open on the path")
        self._mode_btn.set_sensitive(has)
        self._toggle_btn.set_sensitive(has)
        app = self._services.apps.get(self._selected_id) if self._selected_id else None
        if app is None:
            self._del_btn.set_sensitive(False)
            return
        self._del_btn.set_sensitive(app.is_custom)
        self._mode_btn.set_label(
            "Use env proxy" if app.mode == "proxychains" else "Use proxychains"
        )
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
        mode = "proxychains" if app.mode == "proxychains" else "env proxy"
        origin = "custom" if app.is_custom else "installed"
        state = "on" if app.enabled else "off"
        meta = f"{app.command} · {mode} · {origin}"
        if not app.enabled:
            meta += f" · {state}"
        detail = Gtk.Label(label=meta, xalign=0)
        detail.add_css_class("profile-row-hops")
        detail.set_ellipsize(Pango.EllipsizeMode.END)
        content.append(detail)
        row.append(content)

        btn.set_child(row)
        btn.connect("toggled", self._on_toggled, app.id)
        return btn

    def _scroll_selected_into_view(self) -> None:
        """Keep the selected row visible inside the capped list viewport."""
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
        """Set active without re-entering _on_toggled."""
        self._selection_guard = True
        try:
            btn.set_active(active)
        finally:
            self._selection_guard = False

    def _on_toggled(self, button: Gtk.CheckButton, app_id: str) -> None:
        if self._selection_guard:
            return
        if button.get_active():
            # Single selection: clear any other active row.
            for other_id, other in self._row_buttons.items():
                if other_id != app_id and other.get_active():
                    self._set_row_active(other, False)
            self._selected_id = app_id
            self._update_action_sensitivity()
            self._scroll_selected_into_view()
            return
        # Click again on the selected app → deselect.
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
                "Opened via Spectre SOCKS (even before Connect)."
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
        mode = Gtk.CheckButton(label="Use proxychains (for apps that ignore proxy env)")
        box.append(mode)
        dialog.set_extra_child(box)

        def on_response(_d: Adw.MessageDialog, response: str) -> None:
            if response != "add":
                return
            try:
                app = self._services.apps.create(
                    name=name.get_text().strip() or cmd.get_text().strip().split()[0],
                    command=cmd.get_text().strip(),
                    mode="proxychains" if mode.get_active() else "env",
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
        result = launch_app(
            app,
            self._services.core,
            session=self._services.launch_session,
            bind_address=self._services.config.bind_address or "127.0.0.1",
        )
        self._toast(result.message)
        if result.ok:
            self._services.log(f"Launched app {app.name} pid={result.pid}")
            self.refresh_status_line()

    def _on_toggle_mode(self, *_a) -> None:
        if not self._selected_id:
            return
        app = self._services.apps.get(self._selected_id)
        if app is None:
            return
        new_mode = "env" if app.mode == "proxychains" else "proxychains"
        self._services.apps.update(app.id, mode=new_mode)
        self.reload()
        label = "proxychains" if new_mode == "proxychains" else "env proxy"
        self._toast(f"{label} · {app.name}")

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
