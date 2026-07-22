"""Apps — GNOME-style grid to open apps on clearnet while the path protects the rest.

Contract (with Routing mode → Entire system):
  - Whole machine uses Spectre after Connect.
  - Apps opened here start a *separate* clearnet instance (clearnet-run netns
    preferred; mullvad-exclude marks as fallback).
  - Normal launches from the app menu stay on Spectre.

With Selected apps only, the machine is already clearnet from Spectre’s
point of view; exclude is still available but usually unnecessary.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Gtk, Pango

from core.apps import RoutedApp
from core.client import CoreState
from core.launcher import launch_app, probe_exclude_tooling
from services import Services
from widgets.chrome import clear_box, page_header

# GNOME overview-ish tile size
_ICON_PX = 72
_TILE_W = 112
_TILE_H = 118


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
        self._filter = ""
        self._scope = "all"  # all | pinned | custom
        self._show_hidden = False
        self._tooling_cache = None
        self._clearnet_busy = False
        self._scope_btns: dict[str, Gtk.ToggleButton] = {}
        self._tile_by_id: dict[str, Gtk.FlowBoxChild] = {}
        self._context_app_id: str | None = None

        add_btn = Gtk.Button()
        add_btn.set_icon_name("list-add-symbolic")
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add a custom command")
        add_btn.connect("clicked", self._on_add_command)
        self.append(
            page_header(
                "Apps",
                subtitle="Clearnet launch · rest of the system stays on path",
                end=add_btn,
            )
        )

        # ── Top chrome ────────────────────────────────────────────
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        top.add_css_class("apps-top")
        top.set_margin_start(16)
        top.set_margin_end(16)
        top.set_margin_top(6)
        top.set_vexpand(False)

        # Status banner (setup / routing / ready)
        self._banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._banner.add_css_class("apps-banner")
        self._banner.set_hexpand(True)

        self._banner_dot = Gtk.Box()
        self._banner_dot.add_css_class("apps-banner-dot")
        self._banner_dot.set_valign(Gtk.Align.CENTER)
        self._banner_dot.set_size_request(8, 8)
        self._banner.append(self._banner_dot)

        ban_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        ban_col.set_hexpand(True)
        self._banner_title = Gtk.Label(label="", xalign=0)
        self._banner_title.add_css_class("apps-banner-title")
        ban_col.append(self._banner_title)
        self._banner_sub = Gtk.Label(label="", xalign=0, wrap=True)
        self._banner_sub.add_css_class("apps-banner-sub")
        self._banner_sub.add_css_class("muted")
        ban_col.append(self._banner_sub)
        self._banner.append(ban_col)

        self._banner_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._banner_actions.set_valign(Gtk.Align.CENTER)

        self._switch_system_btn = Gtk.Button(label="Use system routing")
        self._switch_system_btn.add_css_class("suggested-action")
        self._switch_system_btn.set_tooltip_text(
            "Switch routing to Entire system (also in Settings → Network)"
        )
        self._switch_system_btn.connect("clicked", self._on_switch_system)
        self._banner_actions.append(self._switch_system_btn)

        self._clearnet_repair_btn = Gtk.Button(label="Repair")
        self._clearnet_repair_btn.add_css_class("flat")
        self._clearnet_repair_btn.set_tooltip_text(
            "Refresh clearnet networking. Needs spectre setup-clearnet once."
        )
        self._clearnet_repair_btn.connect("clicked", self._on_clearnet_repair)
        self._banner_actions.append(self._clearnet_repair_btn)

        self._tools_btn = Gtk.Button(label="Tools")
        self._tools_btn.add_css_class("flat")
        self._tools_btn.set_tooltip_text("Full clearnet diagnostics")
        self._tools_btn.connect(
            "clicked",
            lambda *_: self._on_navigate("tools") if self._on_navigate else None,
        )
        self._banner_actions.append(self._tools_btn)
        self._banner.append(self._banner_actions)
        top.append(self._banner)

        # Progress (repair only)
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
        self._progress_trough.set_size_request(-1, 10)
        self._progress_trough.set_overflow(Gtk.Overflow.HIDDEN)
        self._progress_fill = Gtk.Box()
        self._progress_fill.add_css_class("apps-progress-fill")
        self._progress_fill.set_size_request(0, 10)
        self._progress_fill.set_halign(Gtk.Align.START)
        self._progress_trough.append(self._progress_fill)
        self._progress_trough.connect("notify::width-request", self._on_progress_trough_size)
        self._progress_trough.connect("map", self._on_progress_trough_size)
        self._progress_box.append(self._progress_trough)
        self._progress_fraction = 0.0
        self._progress_hide_id: int | None = None
        top.append(self._progress_box)

        # Centered search (GNOME overview style)
        search_wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        search_wrap.set_halign(Gtk.Align.CENTER)
        search_wrap.set_hexpand(True)
        search_wrap.add_css_class("apps-search-wrap")
        self._search = Gtk.SearchEntry()
        self._search.add_css_class("apps-search")
        self._search.set_placeholder_text("Type to search…")
        self._search.set_size_request(320, -1)
        self._search.set_hexpand(False)
        self._search.connect("search-changed", self._on_search)
        self._search.connect("activate", self._on_search_activate)
        search_wrap.append(self._search)
        top.append(search_wrap)

        # Filter bar under search
        filter_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        filter_row.set_halign(Gtk.Align.CENTER)
        filter_row.add_css_class("apps-filters")
        first_f: Gtk.ToggleButton | None = None
        for key, label, tip in (
            ("all", "All", "Installed and custom apps"),
            ("pinned", "Pinned", "Pinned for quick access"),
            ("custom", "Custom", "Commands you added"),
        ):
            b = Gtk.ToggleButton(label=label)
            b.add_css_class("apps-filter-btn")
            b.add_css_class("flat")
            b.set_tooltip_text(tip)
            if first_f is None:
                first_f = b
                b.set_active(True)
            else:
                b.set_group(first_f)
            b.connect("toggled", self._on_scope, key)
            self._scope_btns[key] = b
            filter_row.append(b)

        self._hidden_btn = Gtk.ToggleButton()
        self._hidden_btn.set_icon_name("view-conceal-symbolic")
        self._hidden_btn.add_css_class("flat")
        self._hidden_btn.add_css_class("apps-filter-btn")
        self._hidden_btn.set_tooltip_text("Show hidden apps")
        self._hidden_btn.connect("toggled", self._on_show_hidden)
        filter_row.append(self._hidden_btn)

        refresh = Gtk.Button()
        refresh.set_icon_name("view-refresh-symbolic")
        refresh.add_css_class("flat")
        refresh.add_css_class("apps-filter-btn")
        refresh.set_tooltip_text("Rescan installed applications")
        refresh.connect("clicked", self._on_refresh)
        filter_row.append(refresh)
        top.append(filter_row)

        self._count_label = Gtk.Label(label="", xalign=0.5)
        self._count_label.add_css_class("muted")
        self._count_label.add_css_class("apps-count")
        self._count_label.set_halign(Gtk.Align.CENTER)
        top.append(self._count_label)

        self.append(top)

        # ── Icon grid ─────────────────────────────────────────────
        from widgets.scroll import scrolled_window

        self._grid = Gtk.FlowBox()
        self._grid.add_css_class("apps-grid")
        self._grid.set_valign(Gtk.Align.START)
        self._grid.set_halign(Gtk.Align.CENTER)
        self._grid.set_hexpand(True)
        self._grid.set_vexpand(False)
        self._grid.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._grid.set_activate_on_single_click(True)
        self._grid.set_homogeneous(True)
        self._grid.set_max_children_per_line(12)
        self._grid.set_min_children_per_line(2)
        self._grid.set_column_spacing(8)
        self._grid.set_row_spacing(8)
        self._grid.set_margin_start(12)
        self._grid.set_margin_end(12)
        self._grid.set_margin_top(4)
        self._grid.set_margin_bottom(16)
        self._grid.connect("child-activated", self._on_tile_activated)

        # Right-click context on grid
        rclick = Gtk.GestureClick()
        rclick.set_button(3)
        rclick.connect("pressed", self._on_grid_right_click)
        self._grid.add_controller(rclick)

        self._list_scroll = scrolled_window(
            h_policy=Gtk.PolicyType.NEVER,
            v_policy=Gtk.PolicyType.AUTOMATIC,
            css_class="apps-list-scroll",
        )
        self._list_scroll.set_propagate_natural_height(False)
        self._list_scroll.set_propagate_natural_width(False)
        self._list_scroll.set_child(self._grid)
        self._list_scroll.set_vexpand(True)
        self._list_scroll.set_hexpand(True)

        self._empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._empty.add_css_class("apps-empty")
        self._empty.set_halign(Gtk.Align.CENTER)
        self._empty.set_valign(Gtk.Align.CENTER)
        self._empty.set_vexpand(True)
        self._empty.set_hexpand(True)
        empty_ic = Gtk.Image.new_from_icon_name("view-app-grid-symbolic")
        empty_ic.set_pixel_size(56)
        empty_ic.add_css_class("apps-empty-icon")
        self._empty.append(empty_ic)
        self._empty_title = Gtk.Label(label="No apps found")
        self._empty_title.add_css_class("apps-empty-title")
        self._empty.append(self._empty_title)
        self._empty_sub = Gtk.Label(
            label="",
            justify=Gtk.Justification.CENTER,
            wrap=True,
        )
        self._empty_sub.add_css_class("muted")
        self._empty.append(self._empty_sub)
        empty_add = Gtk.Button(label="Add custom command")
        empty_add.add_css_class("suggested-action")
        empty_add.set_halign(Gtk.Align.CENTER)
        empty_add.set_margin_top(6)
        empty_add.connect("clicked", self._on_add_command)
        self._empty_add = empty_add
        self._empty.append(empty_add)

        # Context popover (reused)
        self._ctx_pop = Gtk.Popover()
        self._ctx_pop.add_css_class("apps-context-popover")
        self._ctx_pop.set_has_arrow(False)
        self._ctx_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._ctx_box.set_margin_top(6)
        self._ctx_box.set_margin_bottom(6)
        self._ctx_box.set_margin_start(6)
        self._ctx_box.set_margin_end(6)
        self._ctx_pop.set_child(self._ctx_box)
        self._ctx_pop.set_parent(self)

        area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        area.set_hexpand(True)
        area.set_vexpand(True)
        area.append(self._empty)
        area.append(self._list_scroll)
        self.append(area)

        self.reload()

    # ── Filters / search ──────────────────────────────────────────

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        self._filter = entry.get_text() or ""
        self.reload()

    def _on_search_activate(self, *_a) -> None:
        """Enter in search: open the first visible tile."""
        child = self._grid.get_child_at_index(0)
        if child is None:
            return
        app_id = getattr(child, "_app_id", None)
        if app_id:
            self._launch_id(str(app_id))

    def _on_scope(self, btn: Gtk.ToggleButton, key: str) -> None:
        if not btn.get_active():
            return
        self._scope = key
        self.reload()

    def _on_show_hidden(self, btn: Gtk.ToggleButton) -> None:
        self._show_hidden = btn.get_active()
        btn.set_icon_name(
            "view-reveal-symbolic" if self._show_hidden else "view-conceal-symbolic"
        )
        btn.set_tooltip_text(
            "Hide hidden apps" if self._show_hidden else "Show hidden apps"
        )
        self.reload()

    def _on_refresh(self, *_a) -> None:
        self._services.apps.invalidate_system_cache()
        self._tooling_cache = None
        self.reload()
        n = self._services.apps.count_system()
        self._toast(f"Found {n} installed application{'s' if n != 1 else ''}")

    # ── Progress ──────────────────────────────────────────────────

    def _on_progress_trough_size(self, *_a) -> None:
        self._apply_progress_fill_width()

    def _apply_progress_fill_width(self) -> None:
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
            w = 300
        fill_w = 0 if self._progress_fraction <= 0 else max(3, int(w * self._progress_fraction))
        self._progress_fill.set_size_request(fill_w, 10)
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
        if busy is not None:
            self._clearnet_busy = busy
            if busy:
                self._clearnet_repair_btn.set_sensitive(False)
                self._cancel_progress_hide()
                self._progress_box.set_visible(True)
            else:
                self._clearnet_repair_btn.set_sensitive(True)
        self._progress_fraction = max(0.0, min(1.0, float(fraction)))
        if text:
            self._progress_label.set_text(text)
        self._apply_progress_fill_width()
        if busy is False:
            self._cancel_progress_hide()
            delay_ms = 700 if self._progress_fraction >= 1.0 else 400
            self._progress_hide_id = GLib.timeout_add(delay_ms, self._hide_progress_bar)

    def _progress_from_worker(self, fraction: float, label: str) -> None:
        def _apply() -> bool:
            self._set_progress(fraction, text=label, busy=True)
            return False

        GLib.idle_add(_apply)

    def _on_clearnet_repair(self, *_a) -> None:
        if self._clearnet_busy:
            return
        self._set_progress(0.0, text="Repairing clearnet path…", busy=True)

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
                    self._toast(err)
                    self.refresh_status_line()
                    return False
                self._set_progress(1.0, text="Done", busy=False)
                self._toast(msg)
                self._tooling_cache = None
                self.refresh_status_line()
                return False

            GLib.idle_add(done)

        threading.Thread(target=worker, name="spectre-clearnet-repair", daemon=True).start()

    # ── Routing / status ──────────────────────────────────────────

    def _routing_mode(self) -> str:
        mode = (self._services.config.routing_mode or "system").strip().lower()
        return "apps" if mode == "apps" else "system"

    def _tooling(self):
        if self._tooling_cache is None:
            self._tooling_cache = probe_exclude_tooling(check_sudo=True)
        return self._tooling_cache

    def _on_switch_system(self, *_a) -> None:
        cfg = self._services.config
        if (cfg.routing_mode or "").strip().lower() != "apps":
            self._toast("Already using system routing")
            return
        cfg.routing_mode = "system"
        self._services.save_config()
        st = self._services.core.status()
        if st.state == CoreState.CONNECTED:
            self._toast(
                "Switched to system routing — Disconnect and Connect again to apply"
            )
        else:
            self._toast("Switched to system routing — applies on Connect")
        self.refresh_status_line()

    def refresh_status_line(self) -> None:
        tools = self._tooling()
        mode = self._routing_mode()
        st = self._services.core.status()
        n_sys = self._services.apps.count_system()
        n_custom = len(self._services.apps.list(include_system=False))
        n_open = self._services.launch_session.active_count()

        for cls in (
            "apps-banner-ok",
            "apps-banner-warn",
            "apps-banner-bad",
            "apps-banner-info",
        ):
            self._banner.remove_css_class(cls)
            self._banner_dot.remove_css_class(cls)

        self._switch_system_btn.set_visible(False)
        self._clearnet_repair_btn.set_visible(True)
        self._tools_btn.set_visible(True)
        self._banner_sub.set_visible(True)

        if not tools.any_ready:
            self._banner.add_css_class("apps-banner-bad")
            self._banner_dot.add_css_class("apps-banner-bad")
            self._banner_title.set_text("Setup needed")
            self._banner_sub.set_text(
                f"Run spectre setup-clearnet once · {tools.summary()}"
            )
            self._clearnet_repair_btn.set_visible(False)
        elif mode == "apps":
            self._banner.add_css_class("apps-banner-info")
            self._banner_dot.add_css_class("apps-banner-info")
            self._banner_title.set_text("Selected-apps routing")
            self._banner_sub.set_text(
                "Machine is already clearnet from Spectre. Prefer system routing + Open here."
            )
            self._switch_system_btn.set_visible(True)
        elif tools.can_clearnet_run:
            self._banner.add_css_class("apps-banner-ok")
            self._banner_dot.add_css_class("apps-banner-ok")
            path_bit = "Path up" if st.state == CoreState.CONNECTED else "Path down"
            self._banner_title.set_text(f"Ready · {path_bit}")
            self._banner_sub.set_text(
                "Click to open on clearnet · menu launches stay on path"
            )
        else:
            self._banner.add_css_class("apps-banner-warn")
            self._banner_dot.add_css_class("apps-banner-warn")
            self._banner_title.set_text("Fallback mode")
            self._banner_sub.set_text(
                f"{tools.summary()} · full isolation: spectre setup-clearnet"
            )

        from core.clearnet_health import find_clearnet_netns

        self._clearnet_repair_btn.set_sensitive(
            bool(find_clearnet_netns()) and not self._clearnet_busy
        )

        parts = [f"{n_sys} apps"]
        if n_custom:
            parts.append(f"{n_custom} custom")
        if n_open:
            names = self._services.launch_session.names()
            if names:
                shown = ", ".join(names[:3])
                extra = f" +{len(names) - 3}" if len(names) > 3 else ""
                parts.append(f"{n_open} open ({shown}{extra})")
            else:
                parts.append(f"{n_open} open")
        self._count_label.set_text(" · ".join(parts))
        self._count_label.set_tooltip_text(tools.summary())

    def reload(self) -> None:
        apps = self._services.apps.list(
            enabled_only=not self._show_hidden,
            include_system=True,
            query=self._filter,
            pinned_only=self._scope == "pinned",
            custom_only=self._scope == "custom",
        )
        empty = len(apps) == 0
        self._empty.set_visible(empty)
        self._list_scroll.set_visible(not empty)
        try:
            self._grid.remove_all()
        except Exception:
            clear_box(self._grid)  # type: ignore[arg-type]
        self._tile_by_id.clear()
        self.refresh_status_line()

        if empty:
            q = self._filter.strip()
            if q:
                self._empty_title.set_text("No matches")
                self._empty_sub.set_text(f"Nothing matches “{q}”.")
                self._empty_add.set_visible(False)
            elif self._scope == "pinned":
                self._empty_title.set_text("No pinned apps")
                self._empty_sub.set_text("Right-click any app → Pin to top.")
                self._empty_add.set_visible(False)
            elif self._scope == "custom":
                self._empty_title.set_text("No custom commands")
                self._empty_sub.set_text("Add a CLI or tool without a desktop entry.")
                self._empty_add.set_visible(True)
            else:
                self._empty_title.set_text("No applications detected")
                self._empty_sub.set_text(
                    "Install desktop apps, rescan, or add a custom command."
                )
                self._empty_add.set_visible(True)

        for app in apps:
            child = self._make_tile(app)
            self._tile_by_id[app.id] = child
            self._grid.append(child)

    # ── Tiles (GNOME overview style) ──────────────────────────────

    def _make_tile(self, app: RoutedApp) -> Gtk.FlowBoxChild:
        child = Gtk.FlowBoxChild()
        child.add_css_class("apps-tile-child")
        child._app_id = app.id  # type: ignore[attr-defined]
        child.set_tooltip_text(self._tile_tooltip(app))

        # Outer button-like surface
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("apps-tile")
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.START)
        box.set_size_request(_TILE_W, _TILE_H)
        if not app.enabled:
            box.add_css_class("apps-tile-hidden")
        if self._services.apps.is_pinned(app.id):
            box.add_css_class("apps-tile-pinned")

        # Icon plate (rounded square behind icon — GNOME-ish)
        plate = Gtk.CenterBox()
        plate.add_css_class("apps-tile-plate")
        plate.set_halign(Gtk.Align.CENTER)
        plate.set_size_request(_ICON_PX + 16, _ICON_PX + 16)
        icon = self._load_app_icon(app, size=_ICON_PX)
        icon.add_css_class("apps-tile-icon")
        plate.set_center_widget(icon)
        box.append(plate)

        # Name under icon
        name = Gtk.Label(label=app.name)
        name.add_css_class("apps-tile-name")
        name.set_wrap(True)
        name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        name.set_justify(Gtk.Justification.CENTER)
        name.set_lines(2)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(12)
        name.set_halign(Gtk.Align.CENTER)
        box.append(name)

        # Small badges
        badges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        badges.set_halign(Gtk.Align.CENTER)
        badges.add_css_class("apps-tile-badges")
        if self._services.apps.is_pinned(app.id):
            pin = Gtk.Image.new_from_icon_name("view-pin-symbolic")
            pin.set_pixel_size(10)
            pin.add_css_class("apps-tile-badge-icon")
            pin.set_tooltip_text("Pinned")
            badges.append(pin)
        if app.is_custom:
            lab = Gtk.Label(label="custom")
            lab.add_css_class("apps-tile-badge")
            badges.append(lab)
        if not app.enabled:
            lab = Gtk.Label(label="hidden")
            lab.add_css_class("apps-tile-badge")
            badges.append(lab)
        if badges.get_first_child() is not None:
            box.append(badges)

        child.set_child(box)
        return child

    def _tile_tooltip(self, app: RoutedApp) -> str:
        kind = "Custom command" if app.is_custom else "Installed app"
        bits = [app.name, kind, app.command]
        if self._services.apps.is_pinned(app.id):
            bits.append("Pinned")
        if not app.enabled:
            bits.append("Hidden")
        bits.append("Click to open on clearnet · Right-click for more")
        return "\n".join(bits)

    def _load_app_icon(self, app: RoutedApp, *, size: int) -> Gtk.Image:
        icon_name = (app.icon_name or "").strip() or "application-x-executable"
        img = Gtk.Image()
        img.set_pixel_size(size)

        # Absolute path (file icon)
        if icon_name.startswith("/"):
            try:
                from pathlib import Path

                if Path(icon_name).is_file():
                    try:
                        pb = GdkPixbuf.Pixbuf.new_from_file_at_size(
                            icon_name, size, size
                        )
                        texture = Gdk.Texture.new_for_pixbuf(pb)
                        img.set_from_paintable(texture)
                        return img
                    except Exception:
                        img.set_from_file(icon_name)
                        return img
            except Exception:
                pass

        # Theme icon name (prefer full-color, not symbolic)
        name = icon_name
        if name.endswith("-symbolic"):
            # Try non-symbolic first for big tiles
            base = name.removesuffix("-symbolic")
            try:
                display = Gdk.Display.get_default()
                if display is not None:
                    theme = Gtk.IconTheme.get_for_display(display)
                    if theme.has_icon(base):
                        name = base
            except Exception:
                pass
        img.set_from_icon_name(name)
        return img

    def _on_tile_activated(self, _grid: Gtk.FlowBox, child: Gtk.FlowBoxChild) -> None:
        app_id = getattr(child, "_app_id", None)
        if app_id:
            self._launch_id(str(app_id))

    def _on_grid_right_click(
        self, gesture: Gtk.GestureClick, _n: int, x: float, y: float
    ) -> None:
        child = self._grid.get_child_at_pos(int(x), int(y))
        if child is None:
            return
        app_id = getattr(child, "_app_id", None)
        if not app_id:
            return
        app = self._services.apps.get(str(app_id))
        if app is None:
            return
        self._context_app_id = str(app_id)
        self._rebuild_context_menu(app)
        # Point popover at the tile (keep parent on page — no reparent)
        try:
            ok, bounds = child.compute_bounds(self)
            if ok:
                rect = Gdk.Rectangle()
                rect.x = int(bounds.get_x())
                rect.y = int(bounds.get_y())
                rect.width = max(1, int(bounds.get_width()))
                rect.height = max(1, int(bounds.get_height()))
                self._ctx_pop.set_pointing_to(rect)
        except Exception:
            pass
        self._ctx_pop.popup()
        try:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        except Exception:
            pass

    def _rebuild_context_menu(self, app: RoutedApp) -> None:
        clear_box(self._ctx_box)

        def item(label: str, handler, *, destructive: bool = False) -> None:
            b = Gtk.Button(label=label)
            b.add_css_class("flat")
            b.add_css_class("apps-menu-item")
            b.set_halign(Gtk.Align.FILL)
            if destructive:
                b.add_css_class("destructive-action")
            b.connect("clicked", handler)
            self._ctx_box.append(b)

        item("Open on clearnet", lambda *_: self._ctx_action("open"))
        pinned = self._services.apps.is_pinned(app.id)
        item("Unpin" if pinned else "Pin to top", lambda *_: self._ctx_action("pin"))
        if app.is_system:
            item(
                "Show in grid" if not app.enabled else "Hide from grid",
                lambda *_: self._ctx_action("toggle"),
            )
        else:
            item(
                "Enable" if not app.enabled else "Disable",
                lambda *_: self._ctx_action("toggle"),
            )
            item("Remove", lambda *_: self._ctx_action("delete"), destructive=True)
        item("Copy command", lambda *_: self._ctx_action("copy"))

    def _ctx_action(self, action: str) -> None:
        self._ctx_pop.popdown()
        app_id = self._context_app_id
        if not app_id:
            return
        if action == "open":
            self._launch_id(app_id)
            return
        if action == "pin":
            now = self._services.apps.toggle_pin(app_id)
            self.reload()
            self._toast("Pinned" if now else "Unpinned")
            return
        if action == "toggle":
            app = self._services.apps.get(app_id)
            if app is None:
                return
            was = app.enabled
            self._services.apps.update(app_id, enabled=not was)
            self.reload()
            if app.is_system:
                self._toast("Hidden" if was else "Shown again")
            else:
                self._toast("Disabled" if was else "Enabled")
            return
        if action == "delete":
            app = self._services.apps.get(app_id)
            if app is None or not app.is_custom:
                self._toast("Installed apps can’t be removed — hide them instead")
                return
            name = app.name
            if self._services.apps.delete(app_id):
                self.reload()
                self._toast(f"Removed “{name}”")
            return
        if action == "copy":
            app = self._services.apps.get(app_id)
            if app is None:
                return
            display = Gdk.Display.get_default()
            if display is None:
                self._toast("Could not access clipboard")
                return
            display.get_clipboard().set(app.command)
            self._toast("Command copied")

    def _toast(self, msg: str) -> None:
        if self._on_toast:
            self._on_toast(msg)

    def _on_add_command(self, *_a) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self._parent_window,
            heading="Add custom command",
            body=(
                "For tools without a desktop entry. "
                "Shown in the grid and opened on clearnet like any other app."
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
        name.set_placeholder_text("Display name (e.g. My tool)")
        box.append(name)
        cmd = Gtk.Entry()
        cmd.set_placeholder_text("Command (e.g. curl https://ifconfig.me)")
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
            self.reload()
            self._toast(f"Added “{app.name}” — click its icon to open")

        dialog.connect("response", on_response)
        dialog.present()

    def _launch_id(self, app_id: str) -> None:
        app = self._services.apps.get(app_id)
        if app is None:
            return
        if not app.enabled:
            self._toast("App is hidden — right-click → Show in grid")
            return
        self._tooling_cache = None
        tools = self._tooling()
        if not tools.any_ready:
            self._toast("Clearnet path not ready — run spectre setup-clearnet")
            self.refresh_status_line()
            return
        result = launch_app(
            app,
            self._services.core,
            session=self._services.launch_session,
            tooling=tools,
        )
        if result.ok:
            self._services.apps.touch(app_id)
            self._services.log(
                f"Excluded app {app.name} method={result.method} pid={result.pid}"
            )
        self._toast(result.message)
        self.refresh_status_line()
