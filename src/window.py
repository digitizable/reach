"""Main window — compact rail + stacked pages."""

from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

from app_config import APPLICATION_ICON, APPLICATION_NAME, project_root
from core.client import CoreState
from pages.apps import AppsPage
from pages.china_ingress import ChinaIngressPage
from pages.home import HomePage
from pages.marketplace import MarketplacePage
from pages.nav import (
    DEFAULT_PAGE,
    NAV_ITEMS,
    PAGE_SUBTITLES,
    RAIL_SECTIONS,
    NavItem,
    is_operate_page,
    items_for_section,
)
from pages.paths_hub import PathsHubPage
from pages.settings import SettingsPage
from pages.tools import ToolsPage
from services import Services


class ReachWindow(Adw.ApplicationWindow):
    # Soft minimums; content scrolls instead of forcing the shell wider/taller.
    # Axes are independent — edge resize must not drag the other dimension.
    _MIN_W = 640
    _MIN_H = 480
    # First-open default: fits Home (status + map) without feeling squat or huge.
    _DEF_W = 900
    _DEF_H = 700
    # Inset from monitor edges so chrome/panels stay visible.
    _SCREEN_MARGIN = 48

    def __init__(self, app: Adw.Application, *, services: Services) -> None:
        super().__init__(application=app, title=APPLICATION_NAME)
        self.add_css_class("reach-window")
        # Min size only — never fight the user's drag with set_default_size mid-resize.
        self.set_size_request(self._MIN_W, self._MIN_H)
        self.set_resizable(True)
        self.set_icon_name(APPLICATION_ICON)

        self._services = services
        self._max_w, self._max_h = self._detect_screen_max()
        self._compute_size_connected = False

        iw, ih = self._resolve_initial_size(
            int(getattr(services.config, "window_width", 0) or 0),
            int(getattr(services.config, "window_height", 0) or 0),
        )
        # default_size sets each axis independently (not an aspect lock).
        self.set_default_size(iw, ih)

        self._nav_buttons: dict[str, Gtk.ToggleButton] = {}
        self._nav_group_leader: Gtk.ToggleButton | None = None
        self._page_stack: Gtk.Stack | None = None
        self._toast_overlay: Adw.ToastOverlay
        self._home: HomePage | None = None
        self._paths: PathsHubPage | None = None
        self._apps: AppsPage | None = None
        self._china: ChinaIngressPage | None = None
        self._tools: ToolsPage | None = None
        self._marketplace: MarketplacePage | None = None
        self._settings: SettingsPage | None = None
        self._rail: Gtk.Box | None = None
        self._rail_official_box: Gtk.Box | None = None
        self._rail_plugin_box: Gtk.Box | None = None
        self._rail_expand_btn: Gtk.ToggleButton | None = None
        self._window_title: Adw.WindowTitle | None = None
        self._ready = False
        self._bootstrapped = False
        # Periodic core poll so CLI/tray path changes appear without clicking.
        self._status_poll_id: int | None = None
        self._last_status_sig: tuple | None = None
        self._offline_streak: int = 0
        self._persist_geom_id: int | None = None

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
        from widgets.transitions import BOOT_MS, crossfade_stack

        # Not h-homogeneous: loading vs app must not share a forced width natural
        # size (that couples axes when content reflows during resize).
        self._root_stack = crossfade_stack(
            duration_ms=BOOT_MS,
            hhomogeneous=False,
            vhomogeneous=True,
            css_class="root-stack",
        )

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
        # Persist only after resize settles. Never call set_default_size here —
        # writing both axes while one edge is dragged couples H/V motion.
        self.connect("notify::default-width", self._on_geometry_changed)
        self.connect("notify::default-height", self._on_geometry_changed)

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
            mw = int(geo.width) - self._SCREEN_MARGIN
            mh = int(geo.height) - self._SCREEN_MARGIN
        except Exception:
            return 1920 - self._SCREEN_MARGIN, 1080 - self._SCREEN_MARGIN

        return max(self._MIN_W, mw), max(self._MIN_H, mh)

    def _clamp_w(self, w: int) -> int:
        return max(self._MIN_W, min(int(w), self._max_w))

    def _clamp_h(self, h: int) -> int:
        return max(self._MIN_H, min(int(h), self._max_h))

    def _resolve_initial_size(self, saved_w: int, saved_h: int) -> tuple[int, int]:
        """Pick open size. Axes independent — no aspect-ratio lock."""
        def_w = self._clamp_w(self._DEF_W)
        def_h = self._clamp_h(self._DEF_H)

        w, h = int(saved_w or 0), int(saved_h or 0)
        if w < self._MIN_W or h < self._MIN_H:
            return def_w, def_h

        w, h = self._clamp_w(w), self._clamp_h(h)

        # Near-fullscreen restores often feel accidental; fall back to default.
        if w > int(self._max_w * 0.95) and h > int(self._max_h * 0.95):
            return def_w, def_h

        return w, h

    def _bind_toplevel_size_limits(self) -> None:
        """Tell the compositor min size only (independent per axis)."""
        if self._compute_size_connected:
            return
        surface = self.get_surface()
        if surface is None:
            return
        self._max_w, self._max_h = self._detect_screen_max()
        try:
            if not isinstance(surface, Gdk.Toplevel):
                return
            surface.connect("compute-size", self._on_toplevel_compute_size)
            self._compute_size_connected = True
        except Exception:
            pass

    def _on_toplevel_compute_size(self, _toplevel, size) -> None:
        """Min size only — never set max or re-assert current size.

        Setting max_size or rewriting default size during configure makes some
        WMs adjust the orthogonal axis while the user is dragging one edge.
        """
        try:
            size.set_min_size(self._MIN_W, self._MIN_H)
        except Exception:
            pass
        # Do not call set_max_size or set_size here.

    def _on_geometry_changed(self, *_a) -> None:
        """Debounce persist only — never set_default_size (couples axes)."""
        if self._persist_geom_id is not None:
            GLib.source_remove(self._persist_geom_id)
            self._persist_geom_id = None
        # Longer debounce so mid-drag notifies don't thrash disk or geometry
        self._persist_geom_id = GLib.timeout_add(600, self._debounced_persist_geometry)

    def _debounced_persist_geometry(self) -> bool:
        self._persist_geom_id = None
        self._persist_geometry()
        return False

    def _build_loading_screen(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.add_css_class("loading-screen")
        box.set_hexpand(True)
        box.set_vexpand(True)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)

        # App icon as the loading hero
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
            # Yield once so the spinner paints "Building…" before any heavy work.
            if hasattr(self, "_loading_label"):
                self._loading_label.set_text("Building interface…")
            GLib.idle_add(self._finish_bootstrap_start)

        def on_error(msg: str) -> None:
            # Non-fatal — still open the app
            if hasattr(self, "_loading_label"):
                self._loading_label.set_text("Almost ready…")

        preload_assets(on_done=on_done, on_error=on_error)
        return False

    def _boot_set_status(self, text: str) -> None:
        if hasattr(self, "_loading_label"):
            try:
                self._loading_label.set_text(text)
            except Exception:
                pass

    def _finish_bootstrap_start(self) -> bool:
        """Staged UI build — each idle tick yields so the spinner keeps spinning.

        Previous code built rail + all pages + plugins in one main-thread burst,
        which froze the loading spinner and made the handoff feel stuck.
        """
        if self._bootstrapped:
            return False
        self._bootstrapped = True
        self._boot_phase = 0
        GLib.idle_add(self._finish_bootstrap_step)
        return False

    def _finish_bootstrap_step(self) -> bool:
        """One bootstrap phase per idle invocation; return True to continue."""
        phase = int(getattr(self, "_boot_phase", 0))
        try:
            if phase == 0:
                self._boot_set_status("Building interface…")
                while (child := self._shell_host.get_first_child()) is not None:
                    self._shell_host.remove(child)
                self._shell_host.append(self._build_rail())
            elif phase == 1:
                self._boot_set_status("Preparing Home…")
                # Home only first — user sees a live map ASAP
                self._shell_host.append(self._build_pages(home_only=True))
            elif phase == 2:
                self._boot_set_status("Opening…")
                self._ready = True
                self._navigate(DEFAULT_PAGE)
                self._sync_chrome()
                # Reveal shell while spinner is still under the crossfade
                self._root_stack.set_visible_child_name("app")
            elif phase == 3:
                # Spinner can stop once app is visible
                if hasattr(self, "_loading_spinner"):
                    try:
                        self._loading_spinner.stop()
                    except Exception:
                        pass
                self._start_status_poll()
            elif phase == 4:
                # Remaining pages — after first paint
                self._fill_remaining_pages()
            elif phase == 5:
                try:
                    self._rebuild_plugin_nav()
                except Exception:
                    pass
                self._sync_chrome()
            else:
                return False
        except Exception as exc:
            self._boot_set_status(f"Failed to start: {exc}")
            try:
                self._ready = True
                self._root_stack.set_visible_child_name("app")
                if hasattr(self, "_loading_spinner"):
                    self._loading_spinner.stop()
            except Exception:
                pass
            return False

        self._boot_phase = phase + 1
        # Continue stages; small delay after reveal so crossfade can paint
        if phase == 2:
            GLib.timeout_add(40, self._finish_bootstrap_step)
            return False
        return True

    def _build_pages(self, *, home_only: bool = False) -> Gtk.Widget:
        return self._build_pages_impl(home_only=home_only)

    def _fill_remaining_pages(self) -> None:
        """Attach non-Home pages after the shell is already visible."""
        stack = self._page_stack
        if stack is None:
            return
        # Already filled
        if self._paths is not None:
            return
        from pages.apps import AppsPage
        from pages.china_ingress import ChinaIngressPage
        from pages.marketplace import MarketplacePage
        from pages.paths_hub import PathsHubPage
        from pages.settings import SettingsPage
        from pages.tools import ToolsPage

        self._paths = PathsHubPage(
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
            self._services,
            on_toast=self.toast,
            on_navigate=self._navigate,
        )
        self._marketplace = MarketplacePage(
            self._services,
            on_toast=self.toast,
            on_plugins_changed=self._on_plugins_changed,
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
            on_plugins_changed=self._on_plugins_changed,
        )
        stack.add_named(self._paths, "paths")
        stack.add_named(self._apps, "apps")
        stack.add_named(self._china, "china")
        stack.add_named(self._tools, "tools")
        stack.add_named(self._marketplace, "marketplace")
        stack.add_named(self._settings, "settings")
        pending = getattr(self, "_pending_nav", None)
        if pending:
            self._pending_nav = None
            GLib.idle_add(lambda: (self._navigate(pending), False)[1])

    def _on_map(self, *_a) -> None:
        self._start_status_poll()
        # Immediate refresh when the window is shown again
        GLib.idle_add(self._poll_core_status)
        # Surface exists now — bind min size to this monitor (no live resize).
        GLib.idle_add(self._bind_toplevel_size_limits)
        GLib.idle_add(self._refresh_screen_max)

    def _refresh_screen_max(self) -> bool:
        """Update cached max only — never force a new default size mid-session."""
        self._max_w, self._max_h = self._detect_screen_max()
        return False

    def _on_unmap(self, *_a) -> None:
        # Keep polling while closed-to-tray so reopen is fresh; only stop on destroy.
        pass

    def _start_status_poll(self) -> None:
        if self._status_poll_id is not None:
            return
        # 3s + status cache: was 2s force and piled up with tray on the UI thread.
        self._status_poll_id = GLib.timeout_add_seconds(3, self._poll_core_status)

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
        """Timer callback: re-fetch core status and repaint if anything changed.

        Prefer cached status on most ticks (force only every ~6s) so the UI
        thread is not blocked on a Unix-socket round-trip every 2s forever.
        """
        if not self._ready:
            return True
        try:
            self._status_poll_n = int(getattr(self, "_status_poll_n", 0)) + 1
            # force every 4th tick (~12s) or when we have no signature yet
            force = self._status_poll_n % 4 == 1 or self._last_status_sig is None
            st = self._services.core.status(force=force)
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
                # force_core=False: status just fetched (or cache-warm).
                self._home.refresh(live=False, force_core=False)
            self._sync_chrome()
            self._enforce_sensitive_ops_gate()
            if self._apps is not None and hasattr(self._apps, "refresh_status_line"):
                try:
                    self._apps.refresh_status_line()
                except Exception:
                    pass
            app = self.get_application()
            if app is not None and hasattr(app, "_refresh_tray"):
                try:
                    # Prefer cache — tray should not block UI on another status call.
                    app._refresh_tray(force=False)
                except Exception:
                    pass
        except Exception:
            pass
        return True  # keep timer

    def _enforce_sensitive_ops_gate(self) -> None:
        """If path drops while on Operate pages, leave those surfaces."""
        if self._page_stack is None or not self._ready:
            return
        if self._services.sensitive_ops_allowed():
            return
        cur = self._page_stack.get_visible_child_name()
        if not cur or not is_operate_page(cur):
            return
        self.toast(self._services.sensitive_ops_block_message())
        self._navigate("home")

    def _persist_geometry(self) -> None:
        try:
            w = int(self.get_width())
            h = int(self.get_height())
            if w < self._MIN_W or h < self._MIN_H:
                return
            # Clamp each axis independently — free aspect is allowed.
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
        """App icon on loading screen and left sidebar (device-pixel sharp)."""
        root = project_root()
        candidates = (
            root / "data" / "assets" / "app-icon.png",
            root / "data" / "icons" / "hicolor" / "scalable" / "apps"
            / f"{APPLICATION_ICON}.png",
            root / "data" / "icons" / "hicolor" / "scalable" / "apps"
            / f"{APPLICATION_ICON}.svg",
        )
        path = next((p for p in candidates if p.is_file()), None)
        if path is None:
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
        # Never claim leftover horizontal space on wide/fullscreen windows.
        rail.set_hexpand(False)
        rail.set_halign(Gtk.Align.START)
        self._rail = rail
        self._nav_buttons.clear()
        self._nav_group_leader = None
        self._rail_section_labels: dict[str, Gtk.Widget] = {}
        self._rail_section_boxes: dict[str, Gtk.Widget] = {}

        # App icon in the rail (same plate as the desktop icon)
        rail.append(self._brand_mark(44))

        # Run · Path · Workspace · Operate · System
        for section_id, section_title in RAIL_SECTIONS:
            lab = self._rail_section_label(section_title)
            rail.append(lab)
            self._rail_section_labels[section_id] = lab

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            box.add_css_class("nav-rail-section")
            if section_id == "operate":
                box.add_css_class("nav-rail-plugins")
            rail.append(box)
            self._rail_section_boxes[section_id] = box

            for item in items_for_section(section_id):
                # Marketplace + plugins only under Operate
                if item.kind == "plugin":
                    continue
                btn = self._nav_button(item)
                self._nav_buttons[item.id] = btn
                box.append(btn)

            if section_id == "operate":
                # Installed plugin pages filled by _rebuild_plugin_nav
                self._plugin_nav_host = Gtk.Box(
                    orientation=Gtk.Orientation.VERTICAL, spacing=0
                )
                box.append(self._plugin_nav_host)
                self._rail_plugin_box = box
                self._rail_plugin_label = lab

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        rail.append(spacer)

        # Expand toggle
        expand = Gtk.ToggleButton()
        expand.add_css_class("nav-btn")
        expand.add_css_class("nav-expand-btn")
        expand.set_tooltip_text(
            "Expand rail — show Run / Path / Workspace / Operate / System"
        )
        self._rail_expand_btn = expand
        expand.set_active(bool(getattr(self._services.config, "rail_expanded", False)))
        expand.connect("toggled", self._on_rail_expand)
        rail.append(expand)

        self._apply_rail_expanded(
            bool(getattr(self._services.config, "rail_expanded", False))
        )
        self._apply_operate_rail()
        self._rebuild_plugin_nav()
        return rail

    def _rail_section_label(self, text: str) -> Gtk.Label:
        lab = Gtk.Label(label=text, xalign=0)
        lab.add_css_class("nav-section-label")
        lab.set_margin_start(10)
        lab.set_margin_end(6)
        lab.set_margin_top(8)
        lab.set_margin_bottom(2)
        return lab

    def _rail_sep(self) -> Gtk.Widget:
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.add_css_class("nav-section-sep")
        sep.set_margin_top(4)
        sep.set_margin_bottom(4)
        sep.set_margin_start(10)
        sep.set_margin_end(10)
        return sep

    def _on_rail_expand(self, btn: Gtk.ToggleButton) -> None:
        expanded = btn.get_active()
        self._services.config.rail_expanded = expanded
        try:
            self._services.save_config()
        except Exception:
            pass
        self._apply_rail_expanded(expanded)

    def _apply_rail_expanded(self, expanded: bool) -> None:
        rail = self._rail
        if rail is None:
            return
        # Fixed px so GTK cannot grow the rail with the window.
        rail.set_size_request(176 if expanded else 60, -1)
        if expanded:
            rail.add_css_class("nav-rail-expanded")
            rail.remove_css_class("nav-rail-compact")
        else:
            rail.add_css_class("nav-rail-compact")
            rail.remove_css_class("nav-rail-expanded")
        operate_on = bool(getattr(self._services.config, "operate_enabled", False))
        for sid, lab in getattr(self, "_rail_section_labels", {}).items():
            # Hide Operate label when suite is off (even when rail expanded)
            if sid == "operate":
                lab.set_visible(expanded and operate_on)
            else:
                lab.set_visible(expanded)
        # Rebuild button contents (icon-only vs icon+label)
        for pid, btn in list(self._nav_buttons.items()):
            item = self._nav_item_for_id(pid)
            if item is not None:
                btn.set_child(self._nav_button_child(item, expanded=expanded))
        # Expand button glyph
        if self._rail_expand_btn is not None:
            ic = Gtk.Image.new_from_icon_name(
                "go-previous-symbolic" if expanded else "go-next-symbolic"
            )
            ic.set_pixel_size(14)
            if expanded:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row.set_halign(Gtk.Align.START)
                row.set_margin_start(12)
                row.append(ic)
                lab = Gtk.Label(label="Collapse", xalign=0)
                lab.add_css_class("nav-btn-label")
                row.append(lab)
                self._rail_expand_btn.set_child(row)
            else:
                self._rail_expand_btn.set_child(ic)

    def _apply_operate_rail(self) -> None:
        """Show/hide Operate section (marketplace + plugin pages) by posture."""
        operate_on = bool(getattr(self._services.config, "operate_enabled", False))
        box = getattr(self, "_rail_section_boxes", {}).get("operate")
        lab = getattr(self, "_rail_section_labels", {}).get("operate")
        if box is not None:
            box.set_visible(operate_on)
        expanded = bool(getattr(self._services.config, "rail_expanded", False))
        if lab is not None:
            lab.set_visible(expanded and operate_on)
        # Leave Operate pages if suite was turned off
        if not operate_on and self._page_stack is not None:
            cur = self._page_stack.get_visible_child_name()
            if cur and is_operate_page(cur):
                self._page_stack.set_visible_child_name(DEFAULT_PAGE)
                self._set_nav_selected(DEFAULT_PAGE)
                self._sync_chrome()

    def _nav_item_for_id(self, page_id: str) -> NavItem | None:
        for item in NAV_ITEMS:
            if item.id == page_id:
                return item
        # Dynamic plugin pages
        if page_id.startswith("plugin:"):
            pid = page_id.removeprefix("plugin:")
            from core.plugin_store import get_installed

            inst = get_installed(pid)
            if inst and inst.manifest.nav:
                nav = inst.manifest.nav
                from core.plugin_store import _plugin_symbolic_path

                icon_path = _plugin_symbolic_path(inst) or None
                return NavItem(
                    id=page_id,
                    title=nav.title,
                    icon_name=nav.icon or "application-x-addon-symbolic",
                    tooltip=nav.tooltip or nav.title,
                    icon_path=icon_path,
                    kind="plugin",
                )
        return None

    def _nav_icon(self, item: NavItem, *, size: int = 16) -> Gtk.Widget:
        """Symbolic icon, bundled asset, or plugin SVG (rail-themed)."""
        path: Path | None = None
        icon_path = getattr(item, "icon_path", None)
        if icon_path:
            p = Path(icon_path)
            if p.is_file():
                path = p
        if path is None:
            asset = getattr(item, "icon_asset", None)
            if asset:
                cand = project_root() / "data" / "assets" / asset
                if cand.is_file():
                    path = cand
        if path is not None:
            scale = self._display_scale()
            logical = size
            px = max(logical * scale, logical)
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), px, px)
                is_plugin_icon = bool(getattr(item, "icon_path", None)) or (
                    "plugins" in str(path)
                )
                # Rail: theme monochrome. Prefer alpha-preserving recolor for
                # symbolic SVGs (white-on-transparent). Filled color plates look
                # like squares if recolored — plugins should ship icon_symbolic.
                if is_plugin_icon:
                    try:
                        pb = self._recolor_nav_pixbuf_alpha(pb, 199, 212, 238)
                    except Exception:
                        try:
                            pb = self._recolor_nav_pixbuf(pb, 180, 190, 210)
                        except Exception:
                            pass
                elif path.suffix.lower() == ".svg" and asset:
                    # Bundled mono assets (e.g. globe) — light recolor only if needed
                    pass
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

    @staticmethod
    def _recolor_nav_pixbuf(pb, r: int, g: int, b: int):
        """Tint opaque pixels for monochrome plugin marks."""
        if pb.get_n_channels() < 4 or not pb.get_has_alpha():
            pb = pb.add_alpha(False, 0, 0, 0)
        pb = pb.copy()
        w, h = pb.get_width(), pb.get_height()
        n = pb.get_n_channels()
        rowstride = pb.get_rowstride()
        buf = bytearray(pb.get_pixels())
        for y in range(h):
            row = y * rowstride
            for x in range(w):
                i = row + x * n
                if n >= 4 and buf[i + 3] < 12:
                    continue
                buf[i] = r
                buf[i + 1] = g
                buf[i + 2] = b
        return GdkPixbuf.Pixbuf.new_from_data(
            bytes(buf),
            GdkPixbuf.Colorspace.RGB,
            True,
            8,
            w,
            h,
            rowstride,
        )

    @staticmethod
    def _recolor_nav_pixbuf_alpha(pb, r: int, g: int, b: int):
        """Theme a white-on-transparent symbolic: keep alpha, set RGB to theme color.

        Dark filled plates become invisible (low luminance + high coverage) so
        plugins should prefer a true symbolic SVG for the rail.
        """
        if pb.get_n_channels() < 4 or not pb.get_has_alpha():
            pb = pb.add_alpha(False, 0, 0, 0)
        pb = pb.copy()
        w, h = pb.get_width(), pb.get_height()
        n = pb.get_n_channels()
        rowstride = pb.get_rowstride()
        buf = bytearray(pb.get_pixels())
        for y in range(h):
            row = y * rowstride
            for x in range(w):
                i = row + x * n
                a = buf[i + 3] if n >= 4 else 255
                if a < 8:
                    continue
                # Luminance of source (white strokes → high; dark plates → low)
                lum = (buf[i] + buf[i + 1] + buf[i + 2]) / 3.0
                if lum < 40:
                    # Near-black background → transparent in rail
                    buf[i + 3] = 0
                    continue
                # Scale theme color by source alpha and lightness
                strength = min(1.0, lum / 255.0)
                buf[i] = int(r * strength)
                buf[i + 1] = int(g * strength)
                buf[i + 2] = int(b * strength)
                # keep alpha
        return GdkPixbuf.Pixbuf.new_from_data(
            bytes(buf),
            GdkPixbuf.Colorspace.RGB,
            True,
            8,
            w,
            h,
            rowstride,
        )

    def _nav_button_child(self, item: NavItem, *, expanded: bool) -> Gtk.Widget:
        # Plugin custom marks read small at 16px; bump those slightly.
        has_custom = bool(getattr(item, "icon_path", None) or getattr(item, "icon_asset", None))
        icon_size = 20 if (item.kind == "plugin" and has_custom) else 16
        if getattr(item, "icon_asset", None) and item.kind != "plugin":
            icon_size = 18  # e.g. Territories globe
        icon = self._nav_icon(item, size=icon_size)
        if not expanded:
            return icon
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_halign(Gtk.Align.START)
        row.set_margin_start(12)
        row.set_margin_end(8)
        row.append(icon)
        lab = Gtk.Label(label=item.title, xalign=0)
        lab.add_css_class("nav-btn-label")
        lab.set_hexpand(False)
        try:
            from gi.repository import Pango

            lab.set_ellipsize(Pango.EllipsizeMode.END)
            lab.set_max_width_chars(14)
        except Exception:
            pass
        row.append(lab)
        return row

    def _nav_button(self, item: NavItem) -> Gtk.ToggleButton:
        btn = Gtk.ToggleButton()
        btn.add_css_class("nav-btn")
        # Plugin / marketplace buttons use the same chrome as official (no accent bar).
        if item.kind == "marketplace":
            btn.add_css_class("nav-btn-marketplace")
        if getattr(item, "icon_asset", None) or getattr(item, "icon_path", None):
            btn.add_css_class("nav-btn-flag")
        btn.set_tooltip_text(item.tooltip)
        # Keep rail width stable when selection moves between pages.
        btn.set_hexpand(False)
        btn.set_halign(Gtk.Align.FILL)
        expanded = bool(getattr(self._services.config, "rail_expanded", False))
        btn.set_child(self._nav_button_child(item, expanded=expanded))
        btn.connect("toggled", self._on_nav, item.id)
        if self._nav_group_leader is None:
            self._nav_group_leader = btn
        else:
            btn.set_group(self._nav_group_leader)
        return btn

    def _rebuild_plugin_nav(self) -> None:
        """Refresh plugin sidebar buttons and stack pages for installed plugins."""
        host = getattr(self, "_plugin_nav_host", None)
        if host is None or self._page_stack is None:
            return

        # Remove old plugin-only nav buttons
        for pid in list(self._nav_buttons.keys()):
            if pid.startswith("plugin:"):
                btn = self._nav_buttons.pop(pid)
                parent = btn.get_parent()
                if parent is not None:
                    parent.remove(btn)

        while child := host.get_first_child():
            host.remove(child)

        # Remove old plugin stack pages (keep marketplace).
        # Explicit cleanup: GTK remove may delay destroy, leaving GLib timers
        # (Hogwarts plane poll, live desktop) running → lag over the session.
        for name in list(self._list_stack_names()):
            if name.startswith("plugin:"):
                page = self._page_stack.get_child_by_name(name)
                if page is not None:
                    for meth in ("cleanup", "_on_destroy"):
                        fn = getattr(page, meth, None)
                        if callable(fn):
                            try:
                                fn()
                            except Exception:
                                pass
                            break
                    self._page_stack.remove(page)
                    try:
                        page.unparent()
                    except Exception:
                        pass

        # Operate off → no marketplace plugins on rail/stack
        if not bool(getattr(self._services.config, "operate_enabled", False)):
            return

        from core.plugin_host import pages_for_nav

        expanded = bool(getattr(self._services.config, "rail_expanded", False))
        for page_id, manifest, widget in pages_for_nav(
            services=self._services,
            toast=self.toast,
            navigate=self._navigate,
        ):
            self._page_stack.add_named(widget, page_id)
            nav = manifest.nav
            if nav is None:
                continue
            from core.plugin_store import get_installed as _gi
            from core.plugin_store import _plugin_symbolic_path

            inst2 = _gi(manifest.id)
            icon_path = _plugin_symbolic_path(inst2) if inst2 else None
            item = NavItem(
                id=page_id,
                title=nav.title,
                icon_name=nav.icon or "application-x-addon-symbolic",
                tooltip=nav.tooltip or nav.title,
                icon_path=icon_path or None,
                kind="plugin",
                section="operate",
            )
            btn = self._nav_button(item)
            btn.set_child(self._nav_button_child(item, expanded=expanded))
            self._nav_buttons[page_id] = btn
            host.append(btn)
            PAGE_SUBTITLES[page_id] = nav.title

    def _list_stack_names(self) -> list[str]:
        stack = self._page_stack
        if stack is None:
            return []
        names: list[str] = []
        # Gtk.Stack: iterate children and get_page_name
        child = stack.get_first_child()
        while child is not None:
            try:
                name = stack.get_page(child).get_name()
                if name:
                    names.append(name)
            except Exception:
                pass
            child = child.get_next_sibling()
        return names

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

    def _build_pages_impl(self, *, home_only: bool = False) -> Gtk.Widget:
        from pages.home import HomePage
        from widgets.transitions import PAGE_MS, crossfade_stack

        # vhomogeneous: every page fills the window height (scroll internally).
        # hhomogeneous False: pages must not force a shared width natural size
        # (that makes horizontal resize also change height).
        stack = crossfade_stack(
            duration_ms=PAGE_MS,
            hhomogeneous=False,
            vhomogeneous=True,
            css_class="page-stack",
        )
        stack.set_hexpand(True)
        stack.set_vexpand(True)

        self._home = HomePage(
            self._services,
            on_toast=self.toast,
            on_state_changed=self._on_connection_state_changed,
            on_navigate=self._navigate,
        )
        stack.add_named(self._home, "home")
        self._page_stack = stack

        # Paths/apps/… filled after first paint when home_only
        self._paths = None
        self._apps = None
        self._china = None
        self._tools = None
        self._marketplace = None
        self._settings = None

        if not home_only:
            self._fill_remaining_pages()

        return stack

    def refresh_update_settings(self) -> None:
        if self._settings is not None:
            self._settings.refresh_update_meta()

    def _on_plugins_changed(self) -> None:
        """Rebuild Tools + marketplace + plugin rail after install/enable/posture."""
        cur = (
            self._page_stack.get_visible_child_name()
            if self._page_stack is not None
            else None
        )
        if self._tools is not None and hasattr(self._tools, "reload"):
            self._tools.reload()
        if self._marketplace is not None and hasattr(self._marketplace, "reload"):
            self._marketplace.reload()
        try:
            self._apply_operate_rail()
            self._rebuild_plugin_nav()
            # Refresh expand labels (Operate visibility depends on posture)
            self._apply_rail_expanded(
                bool(getattr(self._services.config, "rail_expanded", False))
            )
        except Exception:
            pass
        if cur and is_operate_page(cur):
            if not bool(getattr(self._services.config, "operate_enabled", False)):
                self._navigate(DEFAULT_PAGE)
            elif not self._services.sensitive_ops_allowed():
                self.toast(self._services.sensitive_ops_block_message())
                self._navigate(DEFAULT_PAGE)
            elif cur.startswith("plugin:"):
                pid = cur.removeprefix("plugin:")
                if not self._services.installed_plugin_active(pid):
                    self._navigate("marketplace")

    def refresh_all(self) -> None:
        """Reload pages after connect/disconnect or explicit data edits."""
        if self._home is not None:
            self._home.refresh(live=False, force_core=True)
        if self._paths is not None:
            self._paths.reload()
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
        # Paths hub (recipes/adapters) edits — don't rediscover every installed app.
        if self._home is not None:
            self._home.refresh(live=False)
        if self._paths is not None:
            self._paths.reload()
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
        # Deep links: "settings:plugins", "paths:adapters", …
        # Do NOT split "plugin:com.example.id" — that is a full stack page name.
        section = ""
        raw = page_id

        # Legacy top-level ids → Paths hub sections
        if page_id == "profiles":
            page_id, section = "paths", "recipes"
        elif page_id == "backends":
            page_id, section = "paths", "adapters"
        elif page_id.startswith("settings:") and page_id.count(":") >= 1:
            page_id, section = page_id.split(":", 1)
        elif page_id.startswith("paths:") and page_id.count(":") >= 1:
            page_id, section = page_id.split(":", 1)
        elif ":" in page_id and not page_id.startswith("plugin:"):
            # Generic hub deep link for future parents
            head, rest = page_id.split(":", 1)
            if self._page_stack.get_child_by_name(head) is not None:
                page_id, section = head, rest

        # Operate suite gated — path console never loses Home/Paths
        if is_operate_page(page_id) and not bool(
            getattr(self._services.config, "operate_enabled", False)
        ):
            self.toast(
                "Enable Operate in Settings → Plugins to open marketplace & C2"
            )
            page_id, section = "settings", "plugins"
            if self._page_stack.get_child_by_name("settings") is None:
                # Pages still staging after boot — finish fill then retry
                self._pending_nav = f"settings:{section}" if section else "settings"
                self._fill_remaining_pages()
                if self._page_stack.get_child_by_name("settings") is None:
                    return

        # Sensitive Operate work requires an active path unless policy opts out
        if is_operate_page(page_id) and not self._services.sensitive_ops_allowed():
            self.toast(self._services.sensitive_ops_block_message())
            # Stay on current page if already somewhere safe; else Home
            cur = self._page_stack.get_visible_child_name()
            if cur and not is_operate_page(cur):
                page_id, section = cur, ""
            else:
                page_id, section = "home", ""

        if self._page_stack.get_child_by_name(page_id) is None:
            # Staged boot: non-Home pages may land one idle later
            if page_id != "home" and self._paths is None:
                self._pending_nav = raw
                self._fill_remaining_pages()
            if self._page_stack.get_child_by_name(page_id) is None:
                return
        # Fast path: only swap the stack. Do not rebuild lists / re-probe network
        # on every sidebar click (that was freezing the UI).
        # Heavy pages get a shorter crossfade so double-paint is less visible.
        from widgets.transitions import (
            PAGE_MS,
            effective_duration_ms,
            is_heavy_page_id,
            set_stack_child_smooth,
        )

        set_stack_child_smooth(self._page_stack, page_id, default_ms=PAGE_MS)
        self._set_nav_selected(page_id)
        self._sync_chrome()

        if page_id == "settings" and self._settings is not None:
            if hasattr(self._settings, "show_section"):
                # Rail click or settings:section deep link
                self._settings.show_section(section or "main")

        if page_id == "paths" and self._paths is not None:
            # Sidebar Paths → recipes pane; paths:adapters / backends → adapters
            self._paths.show_section(section or "recipes")

        # Defer heavy work until *after* the stack transition so the crossfade
        # is not fighting a synchronous reload on the GTK main thread.
        delay_ms = (
            70 if is_heavy_page_id(page_id) else PAGE_MS
        )
        delay = effective_duration_ms(delay_ms) + 16

        def _after_transition(cb) -> None:
            if delay <= 0:
                GLib.idle_add(cb)
            else:
                GLib.timeout_add(delay, cb)

        if page_id == "china" and self._china is not None and hasattr(
            self._china, "reload"
        ):

            def _reload_china() -> bool:
                if self._china is not None:
                    try:
                        self._china.reload()
                    except Exception:
                        pass
                return False

            _after_transition(_reload_china)

        # Cheap, deferred updates only where the page needs a status line tweak
        if page_id == "home" and self._home is not None:
            _after_transition(self._idle_refresh_home)
        elif page_id == "apps" and self._apps is not None:
            _after_transition(self._idle_refresh_apps_status)
        elif page_id == "tools" and self._tools is not None and hasattr(
            self._tools, "reload"
        ):

            def _reload_tools() -> bool:
                if self._tools is not None:
                    try:
                        self._tools.reload()
                    except Exception:
                        pass
                return False

            _after_transition(_reload_tools)
        elif page_id == "marketplace" and self._marketplace is not None:

            def _reload_market() -> bool:
                if self._marketplace is not None:
                    try:
                        self._marketplace.reload()
                    except Exception:
                        pass
                return False

            _after_transition(_reload_market)

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
            # Parent hubs expose finer subtitle (e.g. Paths · Adapters)
            if page_id == "paths" and self._paths is not None:
                page_label = self._paths.section_subtitle()
            elif page_id == "settings" and self._settings is not None:
                try:
                    name = self._settings._view.get_visible_child_name()  # type: ignore[attr-defined]
                    if name and name != "main":
                        page_label = name.replace("_", " ").title()
                except Exception:
                    pass
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
