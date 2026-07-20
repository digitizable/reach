"""Main window — compact rail + stacked pages."""

from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

from app_config import APPLICATION_ICON, APPLICATION_NAME, project_root
from core.client import CoreState
from pages.apps import AppsPage
from pages.backends import BackendsPage
from pages.china_ingress import ChinaIngressPage
from pages.home import HomePage
from pages.nav import DEFAULT_PAGE, NAV_ITEMS, PAGE_SUBTITLES, NavItem
from pages.profiles import ProfilesPage
from pages.settings import SettingsPage
from pages.tools import ToolsPage
from services import Services


class ReachWindow(Adw.ApplicationWindow):
    # Soft minimums; maximum always follows the active monitor.
    _MIN_W = 560
    _MIN_H = 480
    _DEF_W = 720
    _DEF_H = 540
    # Inset from monitor edges so chrome/panels stay visible.
    _SCREEN_MARGIN = 48

    def __init__(self, app: Adw.Application, *, services: Services) -> None:
        super().__init__(application=app, title=APPLICATION_NAME)
        self.add_css_class("reach-window")
        self.set_size_request(self._MIN_W, self._MIN_H)
        self.set_resizable(True)
        self.set_icon_name(APPLICATION_ICON)

        self._services = services
        self._max_w, self._max_h = self._detect_screen_max()
        self._compute_size_connected = False

        w = int(getattr(services.config, "window_width", 0) or 0)
        h = int(getattr(services.config, "window_height", 0) or 0)
        # Drop oversized heights from earlier defaults (680–860 era).
        if h >= 680:
            h = self._DEF_H
        # Prefer saved size when sensible; always clamp to this screen.
        if w >= self._MIN_W and h >= self._MIN_H:
            self.set_default_size(
                self._clamp_w(w),
                self._clamp_h(h),
            )
        else:
            self.set_default_size(
                self._clamp_w(self._DEF_W),
                self._clamp_h(self._DEF_H),
            )
        self._nav_buttons: dict[str, Gtk.ToggleButton] = {}
        self._page_stack: Gtk.Stack | None = None
        self._toast_overlay: Adw.ToastOverlay
        self._home: HomePage | None = None
        self._profiles: ProfilesPage | None = None
        self._backends: BackendsPage | None = None
        self._apps: AppsPage | None = None
        self._china: ChinaIngressPage | None = None
        self._tools: ToolsPage | None = None
        self._settings: SettingsPage | None = None
        self._window_title: Adw.WindowTitle | None = None
        self._ready = False
        self._bootstrapped = False
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
        self._window_title = Adw.WindowTitle(
            title=APPLICATION_NAME, subtitle="Loading…"
        )
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

        # Show loading screen immediately; build real shell after assets load.
        self._root_stack = Gtk.Stack()
        self._root_stack.set_hexpand(True)
        self._root_stack.set_vexpand(True)
        self._root_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._root_stack.set_transition_duration(220)

        self._loading = self._build_loading_screen()
        self._root_stack.add_named(self._loading, "loading")

        self._shell_host = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._shell_host.add_css_class("shell")
        self._shell_host.set_hexpand(True)
        self._shell_host.set_vexpand(True)
        self._root_stack.add_named(self._shell_host, "app")
        self._root_stack.set_visible_child_name("loading")

        self._toast_overlay.set_child(self._root_stack)

        self.connect("close-request", self._on_close_request)
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.connect("notify::default-width", self._on_default_size_notify)
        self.connect("notify::default-height", self._on_default_size_notify)

        # First paint → load assets in background → build UI
        GLib.idle_add(self._start_bootstrap)

    # ── Screen-aware size limits ──────────────────────────────────

    def _detect_screen_max(self) -> tuple[int, int]:
        """Largest useful window size on the current (or primary) monitor."""
        display = Gdk.Display.get_default()
        if display is None:
            return 1920 - self._SCREEN_MARGIN, 1080 - self._SCREEN_MARGIN

        monitor = None
        try:
            surface = self.get_surface()
            if surface is not None:
                monitor = display.get_monitor_at_surface(surface)
        except Exception:
            monitor = None

        if monitor is None:
            try:
                monitors = display.get_monitors()
                if monitors.get_n_items() > 0:
                    monitor = monitors.get_item(0)
            except Exception:
                monitor = None

        if monitor is None:
            return 1920 - self._SCREEN_MARGIN, 1080 - self._SCREEN_MARGIN

        try:
            geo = monitor.get_geometry()
            # Prefer logical pixels (geometry is already in application pixels
            # on most GTK4 setups; scale_factor is for hardware pixels).
            mw = int(geo.width) - self._SCREEN_MARGIN
            mh = int(geo.height) - self._SCREEN_MARGIN
        except Exception:
            return 1920 - self._SCREEN_MARGIN, 1080 - self._SCREEN_MARGIN

        # Never below our soft minimums.
        return max(self._MIN_W, mw), max(self._MIN_H, mh)

    def _clamp_w(self, w: int) -> int:
        return max(self._MIN_W, min(int(w), self._max_w))

    def _clamp_h(self, h: int) -> int:
        return max(self._MIN_H, min(int(h), self._max_h))

    def _bind_toplevel_size_limits(self) -> None:
        """Tell the compositor min/max size from the active monitor."""
        if self._compute_size_connected:
            return
        surface = self.get_surface()
        if surface is None:
            return
        # Refresh max for the monitor this window is actually on.
        self._max_w, self._max_h = self._detect_screen_max()
        try:
            if not isinstance(surface, Gdk.Toplevel):
                return
            surface.connect("compute-size", self._on_toplevel_compute_size)
            self._compute_size_connected = True
        except Exception:
            pass

    def _on_toplevel_compute_size(self, _toplevel, size) -> None:
        """Gdk.ToplevelSize: enforce min size; max from screen when available."""
        try:
            size.set_min_size(self._MIN_W, self._MIN_H)
        except Exception:
            pass
        # Prefer explicit max when GI exposes it; else bounds already track display.
        try:
            setter = getattr(size, "set_max_size", None)
            if callable(setter):
                setter(self._max_w, self._max_h)
                return
        except Exception:
            pass
        # Fallback: if requested size would exceed screen, some WMs still
        # honor set_size as a hint for the configure.
        try:
            bounds = size.get_bounds()
            # bounds may be a tuple-like (width, height) or result object
            if hasattr(bounds, "bounds_width"):
                bw, bh = int(bounds.bounds_width), int(bounds.bounds_height)
            elif isinstance(bounds, (tuple, list)) and len(bounds) >= 2:
                bw, bh = int(bounds[0]), int(bounds[1])
            else:
                bw, bh = self._max_w, self._max_h
            # Keep our margin under the compositor bounds.
            self._max_w = max(self._MIN_W, min(self._max_w, bw))
            self._max_h = max(self._MIN_H, min(self._max_h, bh))
        except Exception:
            pass

    def _on_default_size_notify(self, *_a) -> None:
        """Keep default size (and restore) within the screen max."""
        try:
            w, h = self.get_default_size()
            cw, ch = self._clamp_w(w), self._clamp_h(h)
            if (cw, ch) != (w, h):
                self.set_default_size(cw, ch)
        except Exception:
            pass

    def _build_loading_screen(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.add_css_class("loading-screen")
        box.set_hexpand(True)
        box.set_vexpand(True)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)

        # Brand mark — large hands outline as the loading hero
        mark = self._brand_mark(140)
        mark.add_css_class("brand-mark-loading")
        mark.set_halign(Gtk.Align.CENTER)
        box.append(mark)

        title = Gtk.Label(label=APPLICATION_NAME)
        title.add_css_class("loading-title")
        title.set_halign(Gtk.Align.CENTER)
        box.append(title)

        spinner = Gtk.Spinner()
        spinner.add_css_class("loading-spinner")
        spinner.set_size_request(36, 36)
        spinner.set_halign(Gtk.Align.CENTER)
        spinner.start()
        self._loading_spinner = spinner
        box.append(spinner)

        self._loading_label = Gtk.Label(label="Loading assets…")
        self._loading_label.add_css_class("loading-status")
        self._loading_label.set_halign(Gtk.Align.CENTER)
        box.append(self._loading_label)
        return box

    def _start_bootstrap(self) -> bool:
        """Begin background preload; keep loading UI visible until ready."""
        if self._bootstrapped:
            return False
        from core.bootstrap import preload_assets

        if hasattr(self, "_loading_label"):
            self._loading_label.set_text("Loading map and Mullvad data…")

        def on_done() -> None:
            GLib.idle_add(self._finish_bootstrap)

        def on_error(msg: str) -> None:
            # Non-fatal — still open the app
            if hasattr(self, "_loading_label"):
                self._loading_label.set_text("Almost ready…")

        preload_assets(on_done=on_done, on_error=on_error)
        return False

    def _finish_bootstrap(self) -> bool:
        """Build rail + pages on the main thread after assets are cached."""
        if self._bootstrapped:
            return False
        self._bootstrapped = True
        if hasattr(self, "_loading_label"):
            self._loading_label.set_text("Starting…")

        try:
            # Clear shell host and attach real UI
            while (child := self._shell_host.get_first_child()) is not None:
                self._shell_host.remove(child)
            self._shell_host.append(self._build_rail())
            self._shell_host.append(self._build_pages())
            self._ready = True
            self._navigate(DEFAULT_PAGE)
            self._sync_chrome()
            self._start_status_poll()
            self._root_stack.set_visible_child_name("app")
            if hasattr(self, "_loading_spinner"):
                self._loading_spinner.stop()
        except Exception as exc:
            if hasattr(self, "_loading_label"):
                self._loading_label.set_text(f"Failed to start: {exc}")
            # Still try to show something
            try:
                self._ready = True
                self._root_stack.set_visible_child_name("app")
            except Exception:
                pass
        return False

    def _on_map(self, *_a) -> None:
        self._start_status_poll()
        # Immediate refresh when the window is shown again
        GLib.idle_add(self._poll_core_status)
        # Surface exists now — bind min/max to this monitor.
        GLib.idle_add(self._bind_toplevel_size_limits)
        # Re-detect if the window moved to another display later.
        GLib.idle_add(self._refresh_screen_max_and_clamp)

    def _refresh_screen_max_and_clamp(self) -> bool:
        self._max_w, self._max_h = self._detect_screen_max()
        try:
            w = self.get_width()
            h = self.get_height()
            if w > 1 and h > 1:
                cw, ch = self._clamp_w(w), self._clamp_h(h)
                if cw < w or ch < h:
                    # Shrink if current size exceeds the new monitor max.
                    self.set_default_size(cw, ch)
        except Exception:
            pass
        return False

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

    def _persist_geometry(self) -> None:
        try:
            w = int(self.get_width())
            h = int(self.get_height())
            if w < 400 or h < 400:
                return
            # Store clamped so restore never exceeds this screen next launch.
            w, h = self._clamp_w(w), self._clamp_h(h)
            cfg = self._services.config
            if cfg.window_width == w and cfg.window_height == h:
                return
            cfg.window_width = w
            cfg.window_height = h
            self._services.save_config()
        except Exception:
            pass

    def _on_close_request(self, *_a) -> bool:
        self._persist_geometry()
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
        """Reach mark, rendered at device pixels so it stays sharp."""
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
        img.set_tooltip_text(APPLICATION_NAME)
        # Prevent CSS from stretching the paintable (blur source).
        img.set_size_request(size, size)
        return img

    def _build_rail(self) -> Gtk.Widget:
        rail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        rail.add_css_class("nav-rail")
        rail.set_vexpand(True)

        # Hands outline mark — large enough to read in the narrow rail
        rail.append(self._brand_mark(44))

        first: Gtk.ToggleButton | None = None
        for item in NAV_ITEMS:
            if item.section_start:
                sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                sep.add_css_class("nav-section-sep")
                sep.set_margin_top(4)
                sep.set_margin_bottom(4)
                sep.set_margin_start(10)
                sep.set_margin_end(10)
                rail.append(sep)
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

    def _nav_icon(self, item: NavItem, *, size: int = 16) -> Gtk.Widget:
        """Symbolic icon, or crisp asset SVG (e.g. national flag) when set."""
        asset = getattr(item, "icon_asset", None)
        if asset:
            path = project_root() / "data" / "assets" / asset
            if path.is_file():
                scale = self._display_scale()
                # Square asset box (map silhouettes / flags fitted inside).
                logical = size
                px = logical * scale
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), px, px)
                    texture = Gdk.Texture.new_for_pixbuf(pb)
                    img = Gtk.Image.new_from_paintable(texture)
                except GLib.Error:
                    img = Gtk.Image.new_from_file(str(path))
                img.set_pixel_size(size)
                img.set_size_request(logical, logical)
                img.add_css_class("nav-flag")
                img.add_css_class("nav-map")
                img.set_halign(Gtk.Align.CENTER)
                img.set_valign(Gtk.Align.CENTER)
                return img
        icon = Gtk.Image.new_from_icon_name(item.icon_name)
        icon.set_pixel_size(size)
        return icon

    def _nav_button(self, item: NavItem) -> Gtk.ToggleButton:
        btn = Gtk.ToggleButton()
        btn.add_css_class("nav-btn")
        if getattr(item, "icon_asset", None):
            btn.add_css_class("nav-btn-flag")
        btn.set_tooltip_text(item.tooltip)
        btn.set_child(self._nav_icon(item, size=16))
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
        # Homogeneous height so every page gets the fixed window allocation.
        # Without this, tall pages (e.g. Apps with 100+ rows) request full list
        # height and the bottom chrome is clipped by the 600px window.
        stack.set_vhomogeneous(True)
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
        self._china = ChinaIngressPage(
            self._services,
            parent_window=self,
            on_toast=self.toast,
            on_changed=self._on_data_changed,
            on_navigate=self._navigate,
        )
        self._tools = ToolsPage(
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
        stack.add_named(self._china, "china")
        stack.add_named(self._tools, "tools")
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
        if self._china is not None and hasattr(self._china, "reload"):
            self._china.reload()
        if self._tools is not None and hasattr(self._tools, "reload"):
            self._tools.reload()
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
        if self._china is not None and hasattr(self._china, "reload"):
            self._china.reload()
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

        if page_id == "china" and self._china is not None and hasattr(self._china, "reload"):
            self._china.reload()

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
        state_sub = {
            CoreState.UNAVAILABLE: "Core offline",
            CoreState.DISCONNECTED: "Not connected",
            CoreState.CONNECTING: "Connecting…",
            CoreState.CONNECTED: "Protected",
        }
        if self._window_title is None:
            return
        page_id = (
            self._page_stack.get_visible_child_name()
            if self._page_stack is not None
            else None
        )
        # Always lead with live path state when connected / connecting.
        if st.state == CoreState.CONNECTED:
            sub = st.path_summary or "Protected"
        elif st.state == CoreState.CONNECTING:
            sub = "Connecting…"
        elif st.state == CoreState.UNAVAILABLE:
            sub = "Core offline"
        else:
            page_label = PAGE_SUBTITLES.get(page_id or "", "")
            base = state_sub.get(st.state, st.state.value)
            sub = f"{base} · {page_label}" if page_label and page_id != "home" else base
        self._window_title.set_subtitle(sub)

    def toast(self, title: str, *, timeout: int | None = None) -> None:
        # Longer display when reminding users to reconnect after mid-session edits
        if timeout is None:
            timeout = 6 if "reconnect" in (title or "").lower() else 3
        t = Adw.Toast(title=title)
        t.set_timeout(timeout)
        self._toast_overlay.add_toast(t)
