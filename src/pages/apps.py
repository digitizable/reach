"""Apps — launch installed/custom apps through the active Spectre path.

This page is a *launcher*, not a split-tunnel membership list:
- Entire system routing: machine already uses Spectre after Connect; Launch
  still injects SOCKS so the process prefers the path.
- Launched apps / SOCKS only: only what you Launch (or point at local SOCKS)
  uses the path; the rest of the OS stays clearnet.
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

        add_btn = Gtk.Button()
        add_btn.set_icon_name("list-add-symbolic")
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add custom command")
        add_btn.connect("clicked", self._on_add_command)
        self.append(page_header("Apps", end=add_btn))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.add_css_class("page-body")
        body.set_valign(Gtk.Align.START)

        # Mode-aware: this page is a launcher, not an include/exclude membership list.
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

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._list.add_css_class("profile-list")
        body.append(self._list)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions.set_halign(Gtk.Align.END)

        self._launch_btn = Gtk.Button(label="Launch via Spectre")
        self._launch_btn.add_css_class("suggested-action")
        self._launch_btn.set_sensitive(False)
        self._launch_btn.set_tooltip_text(
            "Start the selected app through the active Spectre SOCKS path"
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

    def _refresh_hint(self) -> None:
        """Explain launcher role; copy depends on Settings → Routing mode."""
        if self._routing_mode() == "apps":
            self._hint.set_text(
                "This is a launcher, not a checklist of apps on the path. "
                "Routing is “Launched apps / SOCKS only”: only apps you Launch "
                "here (or other clients pointed at Spectre SOCKS) use the path — "
                "everything else stays on clearnet. Prefer Entire system when you "
                "want the whole machine protected."
            )
        else:
            self._hint.set_text(
                "This is a launcher, not a split-tunnel exception list. "
                "Routing is Entire system: after Connect, the whole machine uses "
                "Spectre. Launch still starts an app with SOCKS set so it prefers "
                "the path — selecting an app does not exclude it from the tunnel."
            )

    def refresh_status_line(self) -> None:
        """Update hint + path/count line without rebuilding the app list."""
        self._refresh_hint()
        st = self._services.core.status()
        n_sys = self._services.apps.count_system()
        n_custom = len(self._services.apps.list(include_system=False))
        mode = "apps only" if self._routing_mode() == "apps" else "system routing"
        base = f"{n_sys} installed"
        if n_custom:
            base += f" · {n_custom} custom"
        base += f" · {mode}"
        if st.state == CoreState.CONNECTED and st.local_proxy:
            self._status.set_text(f"{base} · path up · SOCKS {st.local_proxy}")
        elif st.state == CoreState.CONNECTED:
            self._status.set_text(f"{base} · path up · waiting for local SOCKS")
        else:
            self._status.set_text(f"{base} · connect on Home before launching")

    def reload(self) -> None:
        # Default list hides disabled/hidden apps unless searching by name empty
        # and we want to show only enabled. Allow filter to still find them when
        # query matches after including disabled? Keep simple: show enabled only.
        apps = self._services.apps.list(
            enabled_only=not self._show_disabled,
            include_system=True,
            query=self._filter,
        )
        self._empty.set_visible(len(apps) == 0)
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

        group: Gtk.CheckButton | None = None
        for app in apps:
            btn = self._make_row(app, group)
            if group is None:
                group = btn
            else:
                btn.set_group(group)
            if self._selected_id == app.id:
                btn.set_active(True)
            self._row_buttons[app.id] = btn
            self._list.append(btn)

        has = self._selected_id is not None and self._selected_id in self._row_buttons
        if not has:
            self._selected_id = None
        self._update_action_sensitivity()

    def _update_action_sensitivity(self) -> None:
        has = self._selected_id is not None and self._selected_id in self._row_buttons
        self._launch_btn.set_sensitive(has)
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

    def _make_row(self, app: RoutedApp, group: Gtk.CheckButton | None) -> Gtk.CheckButton:
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

    def _on_toggled(self, button: Gtk.CheckButton, app_id: str) -> None:
        if not button.get_active():
            return
        self._selected_id = app_id
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
                "Launch via Spectre starts them through the active path SOCKS."
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
        result = launch_app(app, self._services.core)
        self._toast(result.message)
        if not result.ok and "Connect" in result.message and self._on_navigate:
            self._on_navigate("home")
        if result.ok:
            self._services.log(f"Launched app {app.name} pid={result.pid}")

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
        # If we hide a system app, clear selection when it leaves the list
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
