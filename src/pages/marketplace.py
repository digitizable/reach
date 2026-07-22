"""Plugins marketplace — two-pane catalog (list + detail)."""

from __future__ import annotations

import threading
from collections.abc import Callable

from gi.repository import Adw, GLib, Gtk

from services import Services
from widgets.chrome import page_header
from widgets.scroll import scrolled_window


class MarketplacePage(Gtk.Box):
    def __init__(
        self,
        services: Services,
        *,
        on_toast: Callable[[str], None] | None = None,
        on_plugins_changed: Callable[[], None] | None = None,
        on_navigate: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("page")
        self.add_css_class("marketplace-page")
        self.add_css_class("master-detail-page")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._services = services
        self._on_toast = on_toast
        self._on_plugins_changed = on_plugins_changed
        self._on_navigate = on_navigate
        self._busy = False
        self._items: list[dict] = []
        self._selected_id: str = ""
        self._filter: str = "all"  # all | official | installed
        self._row_by_id: dict[str, Gtk.ListBoxRow] = {}
        self._filter_btns: dict[str, Gtk.ToggleButton] = {}

        refresh = Gtk.Button()
        refresh.set_icon_name("view-refresh-symbolic")
        refresh.add_css_class("flat")
        refresh.set_tooltip_text("Refresh catalog")
        refresh.connect("clicked", lambda *_: self.reload())
        self.append(
            page_header(
                "Plugins",
                subtitle="Operate suite · Hogwarts C2 · community",
                end=refresh,
            )
        )

        split = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        split.add_css_class("master-detail")
        split.add_css_class("marketplace-split")
        split.set_hexpand(True)
        split.set_vexpand(True)

        # ── Left: catalog list ────────────────────────────────────
        master = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        master.add_css_class("master-pane")
        master.add_css_class("marketplace-master")
        master.set_size_request(300, -1)
        master.set_hexpand(False)
        master.set_vexpand(True)

        # Filters
        filters = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        filters.add_css_class("marketplace-filters")
        filters.set_margin_start(12)
        filters.set_margin_end(12)
        filters.set_margin_top(10)
        filters.set_margin_bottom(6)
        first_f: Gtk.ToggleButton | None = None
        for key, label in (
            ("all", "All"),
            ("official", "Official"),
            ("installed", "Installed"),
        ):
            b = Gtk.ToggleButton(label=label)
            b.add_css_class("marketplace-filter-btn")
            b.add_css_class("flat")
            if first_f is None:
                first_f = b
                b.set_active(True)
            else:
                b.set_group(first_f)
            b.connect("toggled", self._on_filter, key)
            self._filter_btns[key] = b
            filters.append(b)
        master.append(filters)

        # Install from GitHub
        install_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        install_box.add_css_class("marketplace-install")
        install_box.set_margin_start(12)
        install_box.set_margin_end(12)
        install_box.set_margin_bottom(10)
        lab = Gtk.Label(label="Install from GitHub", xalign=0)
        lab.add_css_class("section-label")
        install_box.append(lab)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._repo_entry = Gtk.Entry()
        self._repo_entry.set_placeholder_text("owner/repo")
        self._repo_entry.set_hexpand(True)
        self._repo_entry.connect("activate", self._on_install_github)
        row.append(self._repo_entry)
        self._install_btn = Gtk.Button(label="Install")
        self._install_btn.add_css_class("suggested-action")
        self._install_btn.connect("clicked", self._on_install_github)
        row.append(self._install_btn)
        install_box.append(row)
        master.append(install_box)

        list_scroll = scrolled_window(
            h_policy=Gtk.PolicyType.NEVER,
            v_policy=Gtk.PolicyType.AUTOMATIC,
        )
        list_scroll.set_vexpand(True)
        list_scroll.set_hexpand(True)
        self._list = Gtk.ListBox()
        self._list.add_css_class("marketplace-list")
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.set_show_separators(False)
        self._list.connect("row-selected", self._on_row_selected)
        list_scroll.set_child(self._list)
        master.append(list_scroll)

        self._list_empty = Gtk.Label(
            label="No plugins in this filter.",
            xalign=0.5,
        )
        self._list_empty.add_css_class("muted")
        self._list_empty.set_margin_top(20)
        self._list_empty.set_visible(False)
        master.append(self._list_empty)

        split.append(master)

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.add_css_class("master-detail-sep")
        split.append(sep)

        # ── Right: detail ─────────────────────────────────────────
        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        detail.add_css_class("detail-pane")
        detail.add_css_class("marketplace-detail")
        detail.set_hexpand(True)
        detail.set_vexpand(True)

        from widgets.transitions import PANEL_MS, crossfade_stack

        self._detail_stack = crossfade_stack(
            duration_ms=PANEL_MS,
            hhomogeneous=True,
            vhomogeneous=True,
            css_class="detail-stack",
        )
        self._detail_stack.set_hexpand(True)
        self._detail_stack.set_vexpand(True)

        # Empty state
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        empty.add_css_class("marketplace-detail-empty")
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_halign(Gtk.Align.CENTER)
        empty.set_hexpand(True)
        empty.set_vexpand(True)
        empty_ic = Gtk.Image.new_from_icon_name("application-x-addon-symbolic")
        empty_ic.set_pixel_size(48)
        empty_ic.add_css_class("marketplace-empty-icon")
        empty.append(empty_ic)
        empty_t = Gtk.Label(label="Select a plugin")
        empty_t.add_css_class("detail-title")
        empty.append(empty_t)
        empty_s = Gtk.Label(
            label="Select a pack · or install owner/repo with reach-plugin.json",
            justify=Gtk.Justification.CENTER,
        )
        empty_s.add_css_class("muted")
        empty.append(empty_s)
        self._detail_stack.add_named(empty, "empty")

        # Detail content (rebuilt on selection)
        self._detail_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._detail_host.set_hexpand(True)
        self._detail_host.set_vexpand(True)
        self._detail_stack.add_named(self._detail_host, "detail")
        self._detail_stack.set_visible_child_name("empty")

        detail.append(self._detail_stack)
        split.append(detail)

        self.append(split)
        self.reload()

    # ── Data ──────────────────────────────────────────────────────

    def reload(self) -> None:
        from core.plugin_store import catalog_rows

        keep = self._selected_id
        disabled = list(self._services.config.plugins_disabled or [])
        self._items = list(catalog_rows(disabled_ids=disabled))
        # Built-in packs: reflect Settings plugins_enabled when we can map ids
        self._annotate_builtin_active()
        self._rebuild_list()
        # Restore selection if still present
        if keep and keep in self._row_by_id:
            self._list.select_row(self._row_by_id[keep])
        elif self._items:
            visible = self._filtered_items()
            if visible:
                rid = str(visible[0].get("id") or "")
                if rid in self._row_by_id:
                    self._list.select_row(self._row_by_id[rid])
                else:
                    self._show_empty_detail()
            else:
                self._show_empty_detail()
        else:
            self._show_empty_detail()

    def _annotate_builtin_active(self) -> None:
        """Map catalog builtin packs to Settings plugins_enabled keys."""
        # com.digitizable.fingerprint → fingerprint, etc.
        for item in self._items:
            if not item.get("builtin"):
                continue
            pid = str(item.get("id") or "")
            short = pid.rsplit(".", 1)[-1] if pid else ""
            item["active"] = self._services.plugin_enabled(short)
            item["toggleable"] = False  # still Settings; not filesystem toggle

    def _filtered_items(self) -> list[dict]:
        out: list[dict] = []
        for item in self._items:
            if self._filter == "official" and not item.get("official"):
                continue
            if self._filter == "installed" and not (
                item.get("installed") or item.get("builtin") or item.get("on_disk")
            ):
                continue
            out.append(item)
        return out

    def _rebuild_list(self) -> None:
        while child := self._list.get_first_child():
            self._list.remove(child)
        self._row_by_id.clear()

        visible = self._filtered_items()
        self._list_empty.set_visible(not visible)
        for item in visible:
            row = self._make_list_row(item)
            self._list.append(row)
            pid = str(item.get("id") or "")
            if pid:
                self._row_by_id[pid] = row

    def _make_list_row(self, item: dict) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.add_css_class("marketplace-list-row")
        row.set_activatable(True)
        row._plugin_id = str(item.get("id") or "")  # type: ignore[attr-defined]
        row._plugin_item = item  # type: ignore[attr-defined]

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(12)
        box.set_margin_end(10)

        # Icon badge — full color, fill container (not rail-themed)
        ic_wrap = Gtk.CenterBox()
        ic_wrap.add_css_class("marketplace-list-icon")
        ic_wrap.set_valign(Gtk.Align.CENTER)
        ic_wrap.set_size_request(40, 40)
        ic = self._marketplace_icon(item, size=40)
        ic_wrap.set_center_widget(ic)
        box.append(ic_wrap)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col.set_hexpand(True)
        title = Gtk.Label(
            label=str(item.get("name") or item.get("id")),
            xalign=0,
        )
        title.add_css_class("marketplace-list-title")
        col.append(title)
        tag = Gtk.Label(
            label=str(item.get("tagline") or "")[:72],
            xalign=0,
            ellipsize=3,
        )
        try:
            from gi.repository import Pango

            tag.set_ellipsize(Pango.EllipsizeMode.END)
        except Exception:
            pass
        tag.add_css_class("marketplace-list-tag")
        tag.add_css_class("muted")
        col.append(tag)
        box.append(col)

        # Status chip
        chip = Gtk.Label(label=self._status_label(item))
        chip.add_css_class("marketplace-chip")
        chip.add_css_class(self._status_chip_class(item))
        chip.set_valign(Gtk.Align.CENTER)
        box.append(chip)

        row.set_child(box)
        return row

    @staticmethod
    def _status_label(item: dict) -> str:
        if item.get("on_disk") or (item.get("installed") and item.get("toggleable")):
            return "On" if item.get("active") else "Off"
        if item.get("builtin"):
            return "On" if item.get("active") else "Off"
        if item.get("installed"):
            return "On" if item.get("active", True) else "Off"
        if item.get("community"):
            return "Community"
        if item.get("official"):
            return "Official"
        return "—"

    @staticmethod
    def _status_chip_class(item: dict) -> str:
        if item.get("on_disk") or item.get("toggleable"):
            return (
                "marketplace-chip-ok"
                if item.get("active")
                else "marketplace-chip-off"
            )
        if item.get("builtin"):
            return (
                "marketplace-chip-ok"
                if item.get("active")
                else "marketplace-chip-off"
            )
        if item.get("installed") and item.get("active", True):
            return "marketplace-chip-ok"
        if item.get("community"):
            return "marketplace-chip-community"
        return "marketplace-chip-muted"

    # ── Selection / detail ────────────────────────────────────────

    def _on_filter(self, btn: Gtk.ToggleButton, key: str) -> None:
        if not btn.get_active():
            return
        self._filter = key
        self._rebuild_list()
        visible = self._filtered_items()
        if visible:
            rid = str(visible[0].get("id") or "")
            if rid in self._row_by_id:
                self._list.select_row(self._row_by_id[rid])
        else:
            self._show_empty_detail()

    def _on_row_selected(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            self._show_empty_detail()
            return
        item = getattr(row, "_plugin_item", None)
        if not isinstance(item, dict):
            self._show_empty_detail()
            return
        self._selected_id = str(item.get("id") or "")
        self._show_detail(item)

    def _show_empty_detail(self) -> None:
        self._selected_id = ""
        self._detail_stack.set_visible_child_name("empty")

    def _show_detail(self, item: dict) -> None:
        while child := self._detail_host.get_first_child():
            self._detail_host.remove(child)

        scroll = scrolled_window(
            h_policy=Gtk.PolicyType.NEVER,
            v_policy=Gtk.PolicyType.AUTOMATIC,
        )
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        inner.add_css_class("marketplace-detail-inner")
        inner.set_margin_top(24)
        inner.set_margin_bottom(28)
        inner.set_margin_start(28)
        inner.set_margin_end(28)
        inner.set_halign(Gtk.Align.FILL)
        # Cap readable width of detail copy
        inner.set_size_request(0, -1)

        # Hero — full-color plugin icon in marketplace (rail stays themed)
        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        hero_ic = Gtk.CenterBox()
        hero_ic.add_css_class("marketplace-hero-icon")
        hero_ic.set_valign(Gtk.Align.START)
        hero_ic.set_size_request(72, 72)
        hi = self._marketplace_icon(item, size=72)
        hero_ic.set_center_widget(hi)
        hero.append(hero_ic)

        hero_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        hero_col.set_hexpand(True)
        name = Gtk.Label(
            label=str(item.get("name") or item.get("id")),
            xalign=0,
            wrap=True,
        )
        name.add_css_class("marketplace-detail-name")
        hero_col.append(name)

        badges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        badges.set_margin_top(4)
        for text, cls in self._detail_badges(item):
            b = Gtk.Label(label=text)
            b.add_css_class("marketplace-badge")
            b.add_css_class(cls)
            badges.append(b)
        hero_col.append(badges)
        hero.append(hero_col)
        inner.append(hero)

        # Tagline
        tagline = str(item.get("tagline") or "").strip()
        if tagline:
            tl = Gtk.Label(label=tagline, xalign=0, wrap=True)
            tl.add_css_class("marketplace-detail-tagline")
            inner.append(tl)

        # Meta grid
        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        meta.add_css_class("marketplace-meta-card")
        for k, v in self._meta_rows(item):
            r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            kl = Gtk.Label(label=k, xalign=0)
            kl.add_css_class("marketplace-meta-key")
            kl.set_size_request(88, -1)
            r.append(kl)
            vl = Gtk.Label(label=v, xalign=0, wrap=True, selectable=True)
            vl.add_css_class("marketplace-meta-val")
            vl.set_hexpand(True)
            r.append(vl)
            meta.append(r)
        inner.append(meta)

        # Description blurb
        about = Gtk.Label(label="About", xalign=0)
        about.add_css_class("section-label")
        inner.append(about)
        desc = self._about_text(item)
        dl = Gtk.Label(label=desc, xalign=0, wrap=True)
        dl.add_css_class("muted")
        dl.add_css_class("marketplace-detail-about")
        inner.append(dl)

        # Actions
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        actions.set_margin_top(8)
        actions.add_css_class("marketplace-detail-actions")
        for w in self._detail_actions(item):
            actions.append(w)
        inner.append(actions)

        # Trust note
        note = Gtk.Label(
            label="Runs in-process. Only install code you trust.",
            xalign=0,
            wrap=True,
        )
        note.add_css_class("muted")
        note.add_css_class("marketplace-trust-note")
        note.set_margin_top(8)
        inner.append(note)

        scroll.set_child(inner)
        self._detail_host.append(scroll)
        self._detail_stack.set_visible_child_name("detail")

    def _marketplace_icon(self, item: dict, *, size: int = 22) -> Gtk.Image:
        """Full-color plugin mark for marketplace — fills the badge (not rail-themed)."""
        from pathlib import Path

        from gi.repository import Gdk, GdkPixbuf, GLib

        icon_path = str(item.get("icon_path") or "").strip()
        if icon_path:
            p = Path(icon_path)
            if p.is_file():
                try:
                    scale = 1
                    display = Gdk.Display.get_default()
                    if display is not None:
                        mons = display.get_monitors()
                        if mons.get_n_items() > 0:
                            mon = mons.get_item(0)
                            if mon is not None:
                                scale = max(1, int(mon.get_scale_factor()))
                    # Fill the container: render at full badge size
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_size(
                        str(p), size * scale, size * scale
                    )
                    texture = Gdk.Texture.new_for_pixbuf(pb)
                    img = Gtk.Image.new_from_paintable(texture)
                    img.set_pixel_size(size)
                    img.set_size_request(size, size)
                    img.set_hexpand(True)
                    img.set_vexpand(True)
                    img.set_halign(Gtk.Align.FILL)
                    img.set_valign(Gtk.Align.FILL)
                    img.add_css_class("marketplace-plugin-icon")
                    return img
                except GLib.Error:
                    pass

        icon_name = "application-x-addon-symbolic"
        if item.get("builtin"):
            icon_name = "emblem-system-symbolic"
        elif item.get("installed") or item.get("on_disk"):
            icon_name = "emblem-ok-symbolic"
        ic = Gtk.Image.new_from_icon_name(icon_name)
        ic.set_pixel_size(size)
        return ic

    def _detail_badges(self, item: dict) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if item.get("official"):
            out.append(("Official", "marketplace-badge-official"))
        if item.get("community"):
            out.append(("Community", "marketplace-badge-community"))
        if item.get("builtin"):
            out.append(("Built-in", "marketplace-badge"))
        if item.get("on_disk") or (item.get("installed") and not item.get("builtin")):
            if item.get("active"):
                out.append(("Enabled", "marketplace-badge-ok"))
            else:
                out.append(("Disabled", "marketplace-badge-soon"))
            out.append(("Installed", "marketplace-badge"))
        cat = item.get("category")
        if cat:
            out.append((str(cat).title(), "marketplace-badge"))
        return out

    def _meta_rows(self, item: dict) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = [
            ("Id", str(item.get("id") or "—")),
        ]
        if item.get("version"):
            rows.append(("Version", f"v{item['version']}"))
        if item.get("repo"):
            rows.append(("GitHub", str(item["repo"])))
        if item.get("path"):
            rows.append(("Path", str(item["path"])))
        if item.get("local_example"):
            rows.append(("Example", str(item["local_example"])))
        if item.get("on_disk") or item.get("toggleable"):
            rows.append(
                (
                    "Status",
                    "Enabled" if item.get("active") else "Disabled (still installed)",
                )
            )
        elif item.get("builtin"):
            rows.append(
                (
                    "Status",
                    "Enabled in Settings"
                    if item.get("active")
                    else "Off in Settings → Plugins",
                )
            )
        else:
            rows.append(("Status", "Available"))
        return rows

    def _about_text(self, item: dict) -> str:
        pid = str(item.get("id") or "")
        if item.get("builtin"):
            return (
                f"{item.get('name')} is a built-in Reach pack. "
                "Enable it under Settings → Plugins (Privacy / Lab presets). "
                "It does not install a separate GitHub package."
            )
        if item.get("local_example"):
            return (
                "Official example plugin with a full GTK page and sidebar entry. "
                "Install from the Reach tree (examples/) to try the plugin host, "
                "or use it as a template for your own reach-plugin.json repo."
            )
        if pid == "com.digitizable.hogwarts" and not (
            item.get("on_disk") or item.get("installed")
        ):
            return (
                "Hogwarts is the C2 keep for Reach (named for Hogwarts): "
                "channels, listeners, egress, and playbooks. Install from GitHub "
                f"({item.get('repo') or 'digitizable/hogwarts'}). "
                "After install it appears under Plugins in the sidebar."
            )
        if item.get("on_disk") or item.get("installed"):
            if item.get("active"):
                return (
                    f"{item.get('name')} is installed and enabled. "
                    "Open it from the sidebar under Plugins. "
                    "Disable to hide it without uninstalling, or Remove to delete files."
                )
            return (
                f"{item.get('name')} is installed but disabled. "
                "Enable it to show the sidebar page again without reinstalling."
            )
        return (
            "Install from GitHub if the repository includes a reach-plugin.json "
            "manifest at the root. See docs/PLUGIN_SPEC.md for the format."
        )

    def _detail_actions(self, item: dict) -> list[Gtk.Widget]:
        widgets: list[Gtk.Widget] = []
        pid = str(item.get("id") or "")

        if item.get("builtin"):
            btn = Gtk.Button(label="Open Settings → Plugins")
            btn.add_css_class("suggested-action")
            btn.connect(
                "clicked",
                lambda *_: self._on_navigate("settings:plugins")
                if self._on_navigate
                else None,
            )
            widgets.append(btn)
            return widgets

        if item.get("on_disk") or (item.get("installed") and item.get("toggleable")):
            active = bool(item.get("active"))
            if active:
                open_b = Gtk.Button(label="Open plugin")
                open_b.add_css_class("suggested-action")
                open_b.connect(
                    "clicked",
                    lambda *_a, p=pid: self._on_navigate(f"plugin:{p}")
                    if self._on_navigate
                    else None,
                )
                widgets.append(open_b)
                off = Gtk.Button(label="Disable")
                off.add_css_class("flat")
                off.set_tooltip_text("Turn off without uninstalling")
                off.connect(
                    "clicked",
                    lambda *_a, p=pid: self._set_active(p, False),
                )
                widgets.append(off)
            else:
                on = Gtk.Button(label="Enable")
                on.add_css_class("suggested-action")
                on.set_tooltip_text("Show in sidebar again")
                on.connect(
                    "clicked",
                    lambda *_a, p=pid: self._set_active(p, True),
                )
                widgets.append(on)
            rm = Gtk.Button(label="Remove")
            rm.add_css_class("destructive-action")
            rm.connect("clicked", lambda *_a, p=pid: self._uninstall(p))
            widgets.append(rm)
            return widgets

        if item.get("local_example"):
            b = Gtk.Button(label="Install example")
            b.add_css_class("suggested-action")
            rel = str(item["local_example"])
            b.connect("clicked", lambda *_a, r=rel: self._install_local_example(r))
            widgets.append(b)
            return widgets

        if item.get("repo"):
            b = Gtk.Button(label="Install from GitHub")
            b.add_css_class("suggested-action")
            repo = str(item["repo"])
            branch = str(item.get("branch") or "")
            official = bool(item.get("official"))
            b.connect(
                "clicked",
                lambda *_a, r=repo, br=branch, o=official: self._install_repo(
                    r, branch=br, official=o
                ),
            )
            widgets.append(b)
        return widgets

    # ── Enable / install / remove ─────────────────────────────────

    def _toast(self, msg: str) -> None:
        if self._on_toast:
            self._on_toast(msg)

    def _set_active(self, plugin_id: str, active: bool) -> None:
        self._services.set_installed_plugin_active(plugin_id, active)
        self._toast(
            f"{'Enabled' if active else 'Disabled'} {plugin_id.split('.')[-1]}"
        )
        self.reload()
        if self._on_plugins_changed:
            self._on_plugins_changed()

    def _on_install_github(self, *_a) -> None:
        spec = (self._repo_entry.get_text() or "").strip()
        if not spec:
            self._toast("Enter owner/repo")
            return
        self._install_repo(spec, branch="", official=False)

    def _install_local_example(self, rel: str) -> None:
        if self._busy:
            return
        self._busy = True
        self._toast("Installing example…")

        def work() -> None:
            import shutil
            from pathlib import Path

            from app_config import project_root
            from core.plugin_manifest import find_manifest, load_manifest_file
            from core.plugin_store import plugin_dir

            ok = False
            msg = ""
            installed_id = ""
            try:
                src = project_root() / rel
                if not src.is_dir():
                    src = Path(__file__).resolve().parents[2] / rel
                man = find_manifest(src)
                if man is None:
                    raise FileNotFoundError(f"No reach-plugin.json under {src}")
                m = load_manifest_file(man, source=f"local:{rel}")
                dest = plugin_dir(m.id)
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
                ok = True
                installed_id = m.id
                msg = f"Installed example {m.name}"
            except Exception as exc:
                msg = str(exc)

            def done() -> bool:
                self._busy = False
                self._toast(msg)
                if ok:
                    self._selected_id = installed_id
                self.reload()
                if ok and self._on_plugins_changed:
                    self._on_plugins_changed()
                return False

            GLib.idle_add(done)

        threading.Thread(
            target=work, name="reach-plugin-local", daemon=True
        ).start()

    def _install_repo(
        self, spec: str, *, branch: str = "", official: bool = False
    ) -> None:
        if self._busy:
            self._toast("Install already running…")
            return
        self._busy = True
        self._install_btn.set_sensitive(False)
        self._toast(f"Installing {spec}…")

        def work() -> None:
            from core.plugin_store import install_from_github

            ok, msg, manifest = install_from_github(
                spec, branch=branch, official=official
            )
            installed_id = manifest.id if manifest is not None else ""

            def done() -> bool:
                self._busy = False
                self._install_btn.set_sensitive(True)
                self._toast(msg)
                if ok and installed_id:
                    self._selected_id = installed_id
                self.reload()
                if ok and self._on_plugins_changed:
                    self._on_plugins_changed()
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, name="reach-plugin-install", daemon=True).start()

    def _uninstall(self, plugin_id: str) -> None:
        parent = self.get_root()
        win = parent if isinstance(parent, Gtk.Window) else None
        dialog = Adw.MessageDialog(
            transient_for=win,
            heading="Remove plugin?",
            body=f"Uninstall {plugin_id} from this machine. You can reinstall later.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d: Adw.MessageDialog, response: str) -> None:
            if response != "remove":
                return
            from core.plugin_store import uninstall

            ok, msg = uninstall(plugin_id)
            self._toast(msg)
            if ok and self._selected_id == plugin_id:
                self._selected_id = ""
            self.reload()
            if ok and self._on_plugins_changed:
                self._on_plugins_changed()

        dialog.connect("response", on_response)
        dialog.present()
