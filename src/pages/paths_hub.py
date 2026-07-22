"""Paths — single page with Recipes / Adapters panes (not hub sub-pages).

Rail: **Paths**. In-page panes switch instantly (no back / no dashboard).

Deep links: ``paths``, ``paths:recipes``, ``paths:adapters``
(aliases: ``profiles`` → recipes, ``backends`` → adapters).
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from app_config import project_root
from pages.backends import BackendsPage
from pages.profiles import ProfilesPage
from services import Services
from widgets.transitions import PANEL_MS, crossfade_stack


class PathsHubPage(Gtk.Box):
    """Paths parent: header + pane switcher + recipes | adapters content."""

    def __init__(
        self,
        services: Services,
        *,
        parent_window: Gtk.Window | None = None,
        on_changed: Callable[[], None] | None = None,
        on_toast: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("page")
        self.add_css_class("paths-page")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._services = services
        self._section = "recipes"
        self._pane_btns: dict[str, Gtk.ToggleButton] = {}

        # ── Header ────────────────────────────────────────────────
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("pane-header")
        header.add_css_class("paths-header")
        header.set_hexpand(True)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        titles.set_hexpand(True)
        titles.set_valign(Gtk.Align.CENTER)
        t = Gtk.Label(label="Paths", xalign=0)
        t.add_css_class("pane-header-title")
        titles.append(t)
        self._header_sub = Gtk.Label(label="Recipes · hop chains for Connect", xalign=0)
        self._header_sub.add_css_class("pane-header-sub")
        titles.append(self._header_sub)
        header.append(titles)

        # Pane switcher (icon + label)
        switcher = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        switcher.add_css_class("paths-pane-switcher")
        switcher.set_valign(Gtk.Align.CENTER)
        first: Gtk.ToggleButton | None = None
        for key, label, tip, icon_name, icon_asset in (
            (
                "recipes",
                "Recipes",
                "Hop chains · used by Connect",
                "view-list-symbolic",
                "recipes.svg",  # open recipe book (distinct from Paths hop-chain)
            ),
            (
                "adapters",
                "Adapters",
                "VPN · Tor · REALITY · proxy",
                "network-server-symbolic",
                None,
            ),
        ):
            b = Gtk.ToggleButton()
            b.add_css_class("paths-pane-btn")
            b.add_css_class("flat")
            b.set_tooltip_text(tip)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.set_halign(Gtk.Align.CENTER)
            ic = self._pane_icon(icon_name, asset=icon_asset, size=14)
            ic.add_css_class("paths-pane-icon")
            row.append(ic)
            lab = Gtk.Label(label=label)
            row.append(lab)
            b.set_child(row)
            if first is None:
                first = b
                b.set_active(True)
            else:
                b.set_group(first)
            b.connect("toggled", self._on_pane_toggled, key)
            self._pane_btns[key] = b
            switcher.append(b)
        header.append(switcher)

        self._add_btn = Gtk.Button()
        self._add_btn.set_icon_name("list-add-symbolic")
        self._add_btn.add_css_class("flat")
        self._add_btn.set_valign(Gtk.Align.CENTER)
        self._add_btn.set_tooltip_text("New path")
        self._add_btn.connect("clicked", self._on_add)
        header.append(self._add_btn)

        self.append(header)

        # ── Pane stack ────────────────────────────────────────────
        self._stack = crossfade_stack(
            duration_ms=PANEL_MS,
            hhomogeneous=True,
            vhomogeneous=True,
            css_class="paths-pane-stack",
        )
        self._stack.set_hexpand(True)
        self._stack.set_vexpand(True)

        self._recipes = ProfilesPage(
            services,
            parent_window=parent_window,
            on_changed=on_changed,
            on_toast=on_toast,
            embedded=True,
        )
        self._adapters = BackendsPage(
            services,
            parent_window=parent_window,
            on_changed=on_changed,
            on_toast=on_toast,
            embedded=True,
        )
        self._stack.add_named(self._recipes, "recipes")
        self._stack.add_named(self._adapters, "adapters")
        self.append(self._stack)

        self.show_section("recipes")

    # ── Public API ────────────────────────────────────────────────

    def show_section(self, section_id: str | None = None) -> None:
        """Show recipes or adapters pane (aliases accepted)."""
        sid = (section_id or "recipes").strip().lower()
        if sid in ("", "main", "hub", "home", "paths", "path", "profiles"):
            sid = "recipes"
        elif sid in ("adapters", "backends", "backend"):
            sid = "adapters"
        elif sid not in ("recipes", "adapters"):
            sid = "recipes"

        self._section = sid
        self._stack.set_visible_child_name(sid)

        # Sync switcher without re-entering toggled handler loops
        for key, btn in self._pane_btns.items():
            want = key == sid
            if btn.get_active() != want:
                btn.handler_block_by_func(self._on_pane_toggled)
                try:
                    btn.set_active(want)
                finally:
                    btn.handler_unblock_by_func(self._on_pane_toggled)

        if sid == "recipes":
            self._header_sub.set_text("Recipes · hop chains for Connect")
            self._add_btn.set_tooltip_text("New path")
            self._recipes.reload()
        else:
            self._header_sub.set_text("Adapters · VPN · Tor · REALITY · proxy")
            self._add_btn.set_tooltip_text("New adapter")
            self._adapters.reload()

    def reload(self) -> None:
        self._recipes.reload()
        self._adapters.reload()

    def current_section(self) -> str:
        return self._section

    def section_subtitle(self) -> str:
        return "Recipes" if self._section == "recipes" else "Adapters"

    # ── Internals ─────────────────────────────────────────────────

    def _on_pane_toggled(self, btn: Gtk.ToggleButton, key: str) -> None:
        if not btn.get_active():
            return
        if key == self._section:
            return
        self.show_section(key)

    def _on_add(self, *_a) -> None:
        if self._section == "adapters":
            self._adapters.open_new()
        else:
            self._recipes.open_new()

    @staticmethod
    def _pane_icon(
        icon_name: str,
        *,
        asset: str | None = None,
        size: int = 14,
    ) -> Gtk.Image:
        """Theme icon or white SVG asset (paths.svg, recipes.svg, etc.)."""
        if asset:
            path = project_root() / "data" / "assets" / asset
            if path.is_file():
                try:
                    scale = 1
                    display = Gdk.Display.get_default()
                    if display is not None:
                        mons = display.get_monitors()
                        if mons.get_n_items() > 0:
                            mon = mons.get_item(0)
                            if mon is not None:
                                scale = max(1, int(mon.get_scale_factor()))
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_size(
                        str(path), size * scale, size * scale
                    )
                    texture = Gdk.Texture.new_for_pixbuf(pb)
                    img = Gtk.Image.new_from_paintable(texture)
                    img.set_pixel_size(size)
                    return img
                except (GLib.Error, OSError):
                    pass
        img = Gtk.Image.new_from_icon_name(icon_name)
        img.set_pixel_size(size)
        return img
