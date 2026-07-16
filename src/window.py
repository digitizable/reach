"""Main window — compact rail + three pages."""

from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

from app_config import APPLICATION_ICON, APPLICATION_NAME, project_root
from core.client import CoreState
from pages.apps import AppsPage
from pages.backends import BackendsPage
from pages.home import HomePage
from pages.nav import DEFAULT_PAGE, NAV_ITEMS, NavItem
from pages.profiles import ProfilesPage
from pages.settings import SettingsPage
from services import Services


class SpectreWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, *, services: Services) -> None:
        super().__init__(application=app, title=APPLICATION_NAME)
        self.add_css_class("spectre-window")
        # Fixed shell — slightly roomier, not resizable.
        self.set_default_size(400, 600)
        self.set_size_request(400, 600)
        self.set_resizable(False)
        self.set_icon_name(APPLICATION_ICON)

        self._services = services
        self._nav_buttons: dict[str, Gtk.ToggleButton] = {}
        self._page_stack: Gtk.Stack | None = None
        self._toast_overlay: Adw.ToastOverlay
        self._home: HomePage | None = None
        self._profiles: ProfilesPage | None = None
        self._backends: BackendsPage | None = None
        self._apps: AppsPage | None = None
        self._settings: SettingsPage | None = None
        self._window_title: Adw.WindowTitle | None = None
        self._ready = False
        # Periodic core poll so CLI/tray path changes appear without clicking.
        self._status_poll_id: int | None = None
        self._last_status_sig: tuple | None = None
        self._offline_streak: int = 0

        root = Adw.ToolbarView()
        root.set_hexpand(True)
        root.set_vexpand(True)
        self.set_content(root)

        header = Adw.HeaderBar()
        header.add_css_class("top-header")
        self._window_title = Adw.WindowTitle(title=APPLICATION_NAME, subtitle="")
        header.set_title_widget(self._window_title)
        header.set_show_end_title_buttons(True)
        header.set_show_start_title_buttons(True)
        menu = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu.set_tooltip_text("Menu")
        menu.set_menu_model(self._menu_model())
        header.pack_end(menu)
        root.add_top_bar(header)

        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_hexpand(True)
        self._toast_overlay.set_vexpand(True)
        root.set_content(self._toast_overlay)

        shell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        shell.add_css_class("shell")
        shell.set_hexpand(True)
        shell.set_vexpand(True)
        shell.append(self._build_rail())
        shell.append(self._build_pages())
        self._toast_overlay.set_child(shell)

        self._ready = True
        self._navigate(DEFAULT_PAGE)
        self._sync_chrome()
        self.connect("close-request", self._on_close_request)
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        # Start polling even if already mapped
        self._start_status_poll()

    def _on_map(self, *_a) -> None:
        self._start_status_poll()
        # Immediate refresh when the window is shown again
        GLib.idle_add(self._poll_core_status)

    def _on_unmap(self, *_a) -> None:
        # Keep polling while closed-to-tray so reopen is fresh; only stop on destroy.
        pass

    def _start_status_poll(self) -> None:
        if self._status_poll_id is not None:
            return
        # 2s matches tray tick — cheap GET /v1/status with short timeout.
        self._status_poll_id = GLib.timeout_add_seconds(2, self._poll_core_status)

    def _stop_status_poll(self) -> None:
        if self._status_poll_id is not None:
            GLib.source_remove(self._status_poll_id)
            self._status_poll_id = None

    def do_unrealize(self) -> None:
        self._stop_status_poll()
        Adw.ApplicationWindow.do_unrealize(self)

    def _status_signature(self, st) -> tuple:
        return (
            st.state.value if st.state is not None else "",
            st.path_summary or "",
            st.local_proxy or "",
            st.active_profile or "",
            st.profile_id or "",
            tuple(st.hops or []),
            bool(getattr(st, "routing_active", None)),
            (getattr(st, "routing_mode", None) or ""),
            bool(st.kill_switch_active),
            (st.message or "")[:120],
        )

    def _sync_selected_profile_from_core(self, st) -> None:
        """When core has a live path, keep desktop selection aligned."""
        if st.state != CoreState.CONNECTED:
            return
        pid = (st.profile_id or "").strip()
        if not pid:
            return
        if self._services.profiles.get(pid) is None:
            return
        if self._services.config.last_profile_id == pid:
            return
        self._services.config.last_profile_id = pid
        if st.active_profile:
            self._services.core.set_selected_profile(st.active_profile)
        try:
            self._services.save_config()
        except Exception:
            pass

    def _poll_core_status(self) -> bool:
        """Timer callback: re-fetch core status and repaint if anything changed."""
        if not self._ready:
            return True
        try:
            st = self._services.core.status(force=True)
            # Debounce true offline: one failed/sticky blip must not repaint
            # the dashboard as “Core offline” over “Not connected”.
            if st.state == CoreState.UNAVAILABLE:
                self._offline_streak += 1
                if self._offline_streak < 2 and self._last_status_sig is not None:
                    prev_state = (
                        self._last_status_sig[0] if self._last_status_sig else ""
                    )
                    if prev_state and prev_state != CoreState.UNAVAILABLE.value:
                        return True
            else:
                self._offline_streak = 0

            self._sync_selected_profile_from_core(st)
            sig = self._status_signature(st)
            if sig == self._last_status_sig:
                return True
            self._last_status_sig = sig
            if self._home is not None:
                # force_core=False: we just fetched with force=True (cache warm).
                self._home.refresh(live=False, force_core=False)
            self._sync_chrome()
            if self._apps is not None and hasattr(self._apps, "refresh_status_line"):
                try:
                    self._apps.refresh_status_line()
                except Exception:
                    pass
            app = self.get_application()
            if app is not None and hasattr(app, "_refresh_tray"):
                try:
                    # Cache is warm from force=True above — tray should not
                    # issue another blocking status call on the UI thread.
                    app._refresh_tray(force=False)
                except Exception:
                    pass
        except Exception:
            pass
        return True  # keep timer

    def _on_close_request(self, *_a) -> bool:
        app = self.get_application()
        # Prefer hide-to-tray (Mullvad-style) when the applet is running
        if app is not None and hasattr(app, "should_close_to_tray"):
            try:
                if app.should_close_to_tray():
                    self.set_visible(False)
                    return True  # handled — do not destroy
            except Exception:
                pass
        if app is not None:
            GLib.idle_add(app.quit)
        return False

    def _menu_model(self) -> Gio.Menu:
        menu = Gio.Menu()
        menu.append("Check for updates…", "app.check-updates")
        menu.append("About", "app.about")
        menu.append("Quit", "app.quit")
        return menu

    def _display_scale(self) -> int:
        """Integer scale factor for crisp rasterization (avoids soft SVG blur)."""
        display = Gdk.Display.get_default()
        if display is None:
            return 1
        monitors = display.get_monitors()
        if monitors.get_n_items() < 1:
            return 1
        mon = monitors.get_item(0)
        if mon is None:
            return 1
        return max(1, int(mon.get_scale_factor()))

    def _brand_mark(self, size: int = 26) -> Gtk.Widget:
        """Spectre glyph, rendered at device pixels so it stays sharp."""
        path = project_root() / "data" / "assets" / "mark.svg"
        if not path.is_file():
            img = Gtk.Image.new_from_icon_name(APPLICATION_ICON)
            img.set_pixel_size(size)
            img.add_css_class("brand-mark")
            img.set_halign(Gtk.Align.CENTER)
            img.set_tooltip_text(APPLICATION_NAME)
            return img

        scale = self._display_scale()
        px = size * scale
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), px, px)
            texture = Gdk.Texture.new_for_pixbuf(pb)
            img = Gtk.Image.new_from_paintable(texture)
        except GLib.Error:
            img = Gtk.Image.new_from_file(str(path))

        # Logical size only — texture already has HiDPI pixels.
        img.set_pixel_size(size)
        img.add_css_class("brand-mark")
        img.set_halign(Gtk.Align.CENTER)
        img.set_valign(Gtk.Align.CENTER)
        img.set_margin_top(8)
        img.set_margin_bottom(8)
        img.set_tooltip_text(APPLICATION_NAME)
        # Prevent CSS from stretching the paintable (blur source).
        img.set_size_request(size, size)
        return img

    def _build_rail(self) -> Gtk.Widget:
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        rail.add_css_class("nav-rail")
        rail.set_vexpand(True)

        rail.append(self._brand_mark(26))

        first: Gtk.ToggleButton | None = None
        for item in NAV_ITEMS:
            btn = self._nav_button(item)
            if first is None:
                first = btn
            else:
                btn.set_group(first)
            self._nav_buttons[item.id] = btn
            rail.append(btn)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        rail.append(spacer)
        return rail

    def _nav_button(self, item: NavItem) -> Gtk.ToggleButton:
        btn = Gtk.ToggleButton()
        btn.add_css_class("nav-btn")
        btn.set_tooltip_text(item.tooltip)
        icon = Gtk.Image.new_from_icon_name(item.icon_name)
        icon.set_pixel_size(16)
        btn.set_child(icon)
        btn.connect("toggled", self._on_nav, item.id)
        return btn

    def _set_nav_selected(self, page_id: str) -> None:
        for pid, btn in self._nav_buttons.items():
            selected = pid == page_id
            if selected:
                btn.add_css_class("nav-btn-active")
            else:
                btn.remove_css_class("nav-btn-active")
            if btn.get_active() != selected:
                btn.handler_block_by_func(self._on_nav)
                try:
                    btn.set_active(selected)
                finally:
                    btn.handler_unblock_by_func(self._on_nav)

    def _build_pages(self) -> Gtk.Widget:
        stack = Gtk.Stack()
        stack.add_css_class("page-stack")
        stack.set_hexpand(True)
        stack.set_vexpand(True)
        stack.set_hhomogeneous(False)
        stack.set_vhomogeneous(False)
        stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        stack.set_transition_duration(120)

        self._home = HomePage(
            self._services,
            on_toast=self.toast,
            on_state_changed=self._on_connection_state_changed,
            on_navigate=self._navigate,
        )
        self._profiles = ProfilesPage(
            self._services,
            parent_window=self,
            on_changed=self._on_data_changed,
            on_toast=self.toast,
        )
        self._backends = BackendsPage(
            self._services,
            parent_window=self,
            on_changed=self._on_data_changed,
            on_toast=self.toast,
        )
        self._apps = AppsPage(
            self._services,
            parent_window=self,
            on_toast=self.toast,
            on_navigate=self._navigate,
        )
        app = self.get_application()
        check_cb = None
        if app is not None and hasattr(app, "start_update_check"):
            check_cb = lambda: app.start_update_check(manual=True)  # noqa: E731
        self._settings = SettingsPage(
            self._services,
            parent_window=self,
            on_toast=self.toast,
            on_check_updates=check_cb,
        )

        stack.add_named(self._home, "home")
        stack.add_named(self._profiles, "profiles")
        stack.add_named(self._backends, "backends")
        stack.add_named(self._apps, "apps")
        stack.add_named(self._settings, "settings")
        self._page_stack = stack
        return stack

    def refresh_update_settings(self) -> None:
        if self._settings is not None:
            self._settings.refresh_update_meta()

    def refresh_all(self) -> None:
        """Reload pages after connect/disconnect or explicit data edits."""
        if self._home is not None:
            self._home.refresh(live=False, force_core=True)
        if self._profiles is not None:
            self._profiles.reload()
        if self._backends is not None:
            self._backends.reload()
        # Apps list is large — only refresh status line unless search/filter needs rebuild
        if self._apps is not None:
            if hasattr(self._apps, "refresh_status_line"):
                self._apps.refresh_status_line()
            else:
                self._apps.reload()
        self.refresh_update_settings()
        try:
            st = self._services.core.status(force=False)
            self._last_status_sig = self._status_signature(st)
        except Exception:
            self._last_status_sig = None
        self._sync_chrome()
        app = self.get_application()
        if app is not None and hasattr(app, "_refresh_tray"):
            try:
                app._refresh_tray()
            except Exception:
                pass

    def _on_data_changed(self) -> None:
        # Profiles/backends edits — don't rediscover every installed app.
        if self._home is not None:
            self._home.refresh(live=False)
        if self._profiles is not None:
            self._profiles.reload()
        if self._backends is not None:
            self._backends.reload()
        if self._apps is not None and hasattr(self._apps, "refresh_status_line"):
            self._apps.refresh_status_line()
        self._sync_chrome()

    def _on_nav(self, button: Gtk.ToggleButton, page_id: str) -> None:
        if button.get_active():
            self._navigate(page_id)

    def _navigate(self, page_id: str) -> None:
        if not self._ready or self._page_stack is None:
            return
        if self._page_stack.get_child_by_name(page_id) is None:
            return
        # Fast path: only swap the stack. Do not rebuild lists / re-probe network
        # on every sidebar click (that was freezing the UI).
        self._page_stack.set_visible_child_name(page_id)
        self._set_nav_selected(page_id)
        self._sync_chrome()

        # Cheap, deferred updates only where the page needs a status line tweak
        if page_id == "home" and self._home is not None:
            GLib.idle_add(self._idle_refresh_home)
        elif page_id == "apps" and self._apps is not None:
            GLib.idle_add(self._idle_refresh_apps_status)

    def _idle_refresh_home(self) -> bool:
        if self._home is not None:
            try:
                self._home.refresh(live=False, force_core=True)
            except Exception:
                pass
        return False

    def _idle_refresh_apps_status(self) -> bool:
        if self._apps is not None and hasattr(self._apps, "refresh_status_line"):
            try:
                self._apps.refresh_status_line()
            except Exception:
                pass
        return False

    def _on_connection_state_changed(self) -> None:
        """Home Connect/Disconnect — keep chrome and Apps open-button in sync."""
        self._sync_chrome()
        if self._apps is not None and hasattr(self._apps, "refresh_status_line"):
            try:
                self._apps.refresh_status_line()
            except Exception:
                pass

    def _sync_chrome(self) -> None:
        st = self._services.core.status()
        subtitles = {
            CoreState.UNAVAILABLE: "Core offline",
            CoreState.DISCONNECTED: "Not connected",
            CoreState.CONNECTING: "Connecting…",
            CoreState.CONNECTED: "Protected",
        }
        if self._window_title is not None:
            sub = subtitles.get(st.state, st.state.value)
            # When protected, show which path the *core* has up (not a stale label).
            if st.state == CoreState.CONNECTED and st.path_summary:
                sub = st.path_summary
            self._window_title.set_subtitle(sub)

    def toast(self, title: str, *, timeout: int | None = None) -> None:
        # Longer display when reminding users to reconnect after mid-session edits
        if timeout is None:
            timeout = 6 if "reconnect" in (title or "").lower() else 3
        t = Adw.Toast(title=title)
        t.set_timeout(timeout)
        self._toast_overlay.add_toast(t)
