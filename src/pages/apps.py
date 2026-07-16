"""Apps — choose which applications launch through Spectre."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from gi.repository import Adw, Gio, Gtk

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

        add_btn = Gtk.Button()
        add_btn.set_icon_name("list-add-symbolic")
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add application")
        add_btn.connect("clicked", self._on_add_menu)
        self.append(page_header("Apps", end=add_btn))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.add_css_class("page-body")
        body.set_valign(Gtk.Align.START)

        self._hint = Gtk.Label(
            label="Apps you add here launch through the active Spectre path "
            "(SOCKS). Connect a profile first, then Launch.",
            wrap=True,
            xalign=0,
        )
        self._hint.add_css_class("muted")
        body.append(self._hint)

        self._status = Gtk.Label(label="", xalign=0, wrap=True)
        self._status.add_css_class("muted")
        body.append(self._status)

        self._empty = Gtk.Label(
            label="No routed apps yet.\nAdd a command or a .desktop launcher.",
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
        self._launch_btn = Gtk.Button(label="Launch")
        self._launch_btn.add_css_class("suggested-action")
        self._launch_btn.set_sensitive(False)
        self._launch_btn.connect("clicked", self._on_launch)
        actions.append(self._launch_btn)
        self._toggle_btn = Gtk.Button(label="Disable")
        self._toggle_btn.add_css_class("flat")
        self._toggle_btn.set_sensitive(False)
        self._toggle_btn.connect("clicked", self._on_toggle)
        actions.append(self._toggle_btn)
        self._del_btn = Gtk.Button(label="Delete")
        self._del_btn.add_css_class("flat")
        self._del_btn.set_sensitive(False)
        self._del_btn.connect("clicked", self._on_delete)
        actions.append(self._del_btn)
        body.append(actions)

        self.append(scroll_body(body, margin=12))
        self.reload()

    def reload(self) -> None:
        apps = self._services.apps.list()
        self._empty.set_visible(len(apps) == 0)
        self._list.set_visible(len(apps) > 0)
        clear_box(self._list)
        self._row_buttons.clear()

        st = self._services.core.status()
        if st.state == CoreState.CONNECTED and st.local_proxy:
            self._status.set_text(f"Path up · apps use SOCKS {st.local_proxy}")
        elif st.state == CoreState.CONNECTED:
            self._status.set_text("Path up · waiting for local SOCKS address")
        else:
            self._status.set_text("Not connected · Connect on Home before launching apps")

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
        self._launch_btn.set_sensitive(has)
        self._toggle_btn.set_sensitive(has)
        self._del_btn.set_sensitive(has)
        if has and self._selected_id:
            app = self._services.apps.get(self._selected_id)
            if app:
                self._toggle_btn.set_label("Disable" if app.enabled else "Enable")

    def _make_row(self, app: RoutedApp, group: Gtk.CheckButton | None) -> Gtk.CheckButton:
        btn = Gtk.CheckButton()
        btn.add_css_class("profile-row")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        content.set_hexpand(True)
        title = Gtk.Label(label=app.name, xalign=0)
        title.add_css_class("profile-row-name")
        content.append(title)
        mode = "proxychains" if app.mode == "proxychains" else "env proxy"
        state = "on" if app.enabled else "off"
        meta = f"{app.command} · {mode} · {state}"
        detail = Gtk.Label(label=meta, xalign=0)
        detail.add_css_class("profile-row-hops")
        try:
            from gi.repository import Pango

            detail.set_ellipsize(Pango.EllipsizeMode.END)
        except Exception:
            pass
        content.append(detail)
        btn.set_child(content)
        btn.connect("toggled", self._on_toggled, app.id)
        return btn

    def _on_toggled(self, button: Gtk.CheckButton, app_id: str) -> None:
        if not button.get_active():
            return
        self._selected_id = app_id
        self._launch_btn.set_sensitive(True)
        self._toggle_btn.set_sensitive(True)
        self._del_btn.set_sensitive(True)
        app = self._services.apps.get(app_id)
        if app:
            self._toggle_btn.set_label("Disable" if app.enabled else "Enable")

    def _toast(self, msg: str) -> None:
        if self._on_toast:
            self._on_toast(msg)

    def _on_add_menu(self, btn: Gtk.Button) -> None:
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        for label, handler in (
            ("Add command…", self._on_add_command),
            ("Add .desktop file…", self._on_add_desktop),
        ):
            b = Gtk.Button(label=label)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, h=handler, p=pop: (p.popdown(), h()))
            box.append(b)
        pop.set_child(box)
        pop.set_parent(btn)
        pop.popup()

    def _on_add_command(self, *_a) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self._parent_window,
            heading="Add application",
            body="Command runs through Spectre when you Launch (path must be connected).",
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
        cmd.set_placeholder_text("Command (e.g. firefox  or  curl https://…)")
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

    def _on_add_desktop(self, *_a) -> None:
        dialog = Gtk.FileDialog(title="Application launcher")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        desk = Gtk.FileFilter()
        desk.set_name("Desktop entries")
        desk.add_pattern("*.desktop")
        filters.append(desk)
        dialog.set_filters(filters)
        dialog.set_default_filter(desk)
        # Common locations
        for d in (
            Path.home() / ".local/share/applications",
            Path("/usr/share/applications"),
        ):
            if d.is_dir():
                try:
                    dialog.set_initial_folder(Gio.File.new_for_path(str(d)))
                except Exception:
                    pass
                break

        def on_open(dlg: Gtk.FileDialog, result) -> None:
            try:
                file = dlg.open_finish(result)
            except Exception:
                return
            if file is None:
                return
            path = file.get_path()
            if not path:
                return
            try:
                app = self._services.apps.create_from_desktop(Path(path))
            except ValueError as exc:
                self._toast(str(exc))
                return
            self._selected_id = app.id
            self.reload()
            self._toast(f"Added “{app.name}”")

        dialog.open(self._parent_window, None, on_open)

    def _on_launch(self, *_a) -> None:
        if not self._selected_id:
            return
        app = self._services.apps.get(self._selected_id)
        if app is None:
            return
        result = launch_app(app, self._services.core)
        self._toast(result.message)
        if not result.ok and "Connect" in result.message and self._on_navigate:
            self._on_navigate("home")
        if result.ok:
            self._services.log(f"Launched app {app.name} pid={result.pid}")

    def _on_toggle(self, *_a) -> None:
        if not self._selected_id:
            return
        app = self._services.apps.get(self._selected_id)
        if app is None:
            return
        self._services.apps.update(app.id, enabled=not app.enabled)
        self.reload()
        self._toast("Enabled" if not app.enabled else "Disabled")

    def _on_delete(self, *_a) -> None:
        if not self._selected_id:
            return
        app = self._services.apps.get(self._selected_id)
        name = app.name if app else "app"
        if not self._services.apps.delete(self._selected_id):
            return
        self._selected_id = None
        self.reload()
        self._toast(f"Deleted “{name}”")
