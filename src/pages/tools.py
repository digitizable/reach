"""Tools — diagnostics and lab utilities only (no path Connect / Disconnect).

Core: status, preflight, unlock, deps, clearnet, SOCKS, logs, TCP probe.
Built-in packs (Settings → Plugins): path fingerprint, lab companions.
C2 lives in the marketplace plugin Hogwarts. Session control is Home.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gi.repository import Adw, Gdk, GLib, Gtk

from app_config import user_data_dir
from core.client import CoreState
from core.desktop_log import log_path
from core.readiness import profile_readiness
from services import Services
from widgets.chrome import scroll_body
from widgets.transitions import SUBPAGE_MS, slide_stack


@dataclass(frozen=True)
class _Tool:
    id: str
    title: str
    subtitle: str
    icon: str
    action: str  # "run" | "page" | "nav"
    target: str = ""  # page id or nav id
    # Brand asset under data/assets/ (same marks as Settings / path diagram)
    asset: str = ""


# Each tool gets a distinct icon (no shared glyphs with Plugins / Settings tiles).
_TOOLS: tuple[_Tool, ...] = (
    _Tool(
        "core",
        "Core status",
        "Ping spectred and show live path state",
        "network-server-symbolic",
        "run",
        "core",
        asset="spectre.png",  # brand mark — unique to Spectre core
    ),
    _Tool(
        "preflight",
        "Path preflight",
        "Readiness of the active path (no Connect)",
        "emblem-ok-symbolic",
        "run",
        "preflight",
    ),
    _Tool(
        "deps",
        "Dependencies",
        "xray · wg-quick · mullvad · helpers on PATH",
        "package-x-generic-symbolic",
        "run",
        "deps",
    ),
    _Tool(
        "unlock",
        "Unlock network",
        "If kill switch left the machine offline: spectre unlock",
        "network-offline-symbolic",
        "run",
        "unlock",
    ),
    _Tool(
        "clearnet",
        "Clearnet health",
        "Check the exclude-apps / clearnet netns path",
        "weather-clear-symbolic",
        "run",
        "clearnet",
    ),
    _Tool(
        "probe",
        "TCP probe",
        "TCP open check · host:port (not Connect)",
        "network-transmit-receive-symbolic",
        "page",
        "probe",
    ),
    _Tool(
        "socks",
        "Copy SOCKS",
        "Copy the local proxy URL when connected",
        "edit-copy-symbolic",
        "run",
        "socks",
    ),
    _Tool(
        "logs",
        "Open desktop log",
        "View Reach’s local log file",
        "document-open-recent-symbolic",
        "run",
        "logs",
    ),
    # Plugin-gated (Settings → Plugins · Path fingerprint pack)
    _Tool(
        "fingerprint",
        "Path fingerprint",
        "Lab pack · ΔRTT / path latency on live SOCKS",
        "utilities-system-monitor-symbolic",
        "run",
        "fingerprint",
    ),
)


class ToolsPage(Gtk.Box):
    def __init__(
        self,
        services: Services | None = None,
        *,
        on_toast: Callable[[str], None] | None = None,
        on_navigate: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("page")
        self.add_css_class("tools-page")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._services = services
        self._on_toast = on_toast
        self._on_navigate = on_navigate
        self._busy = False

        self._view = slide_stack(
            duration_ms=SUBPAGE_MS,
            left_right=True,
            hhomogeneous=True,
            vhomogeneous=True,
            css_class="tools-view-stack",
        )
        self._view.add_named(self._build_main(), "main")
        self._view.add_named(self._build_probe_page(), "probe")
        self.append(self._view)

    # ── Main dashboard ────────────────────────────────────────────

    def _build_main(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.set_hexpand(True)
        page.set_vexpand(True)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("pane-header")
        header.set_hexpand(True)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        titles.set_hexpand(True)
        titles.set_valign(Gtk.Align.CENTER)
        t = Gtk.Label(label="Tools", xalign=0)
        t.add_css_class("pane-header-title")
        titles.append(t)
        sub = Gtk.Label(
            label="Diagnostics only · Connect lives on Home",
            xalign=0,
        )
        sub.add_css_class("pane-header-sub")
        titles.append(sub)
        plugins_btn = Gtk.Button(label="Plugins")
        plugins_btn.add_css_class("flat")
        plugins_btn.set_valign(Gtk.Align.CENTER)
        plugins_btn.set_tooltip_text("Plugins marketplace")
        plugins_btn.connect("clicked", lambda *_: self._open_plugins_settings())
        header.append(titles)
        header.append(plugins_btn)
        page.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.add_css_class("tools-body")
        body.set_halign(Gtk.Align.CENTER)
        body.set_hexpand(True)

        self._plugins_banner = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=10
        )
        self._plugins_banner.add_css_class("tools-plugins-banner")
        self._plugins_banner.set_hexpand(True)
        ban_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        ban_col.set_hexpand(True)
        self._plugins_banner_title = Gtk.Label(label="", xalign=0)
        self._plugins_banner_title.add_css_class("tools-plugins-banner-title")
        ban_col.append(self._plugins_banner_title)
        self._plugins_banner_sub = Gtk.Label(label="", xalign=0, wrap=True)
        self._plugins_banner_sub.add_css_class("muted")
        ban_col.append(self._plugins_banner_sub)
        self._plugins_banner.append(ban_col)
        ban_go = Gtk.Button(label="Open")
        ban_go.add_css_class("suggested-action")
        ban_go.set_valign(Gtk.Align.CENTER)
        ban_go.connect("clicked", lambda *_: self._open_plugins_settings())
        self._plugins_banner.append(ban_go)
        body.append(self._plugins_banner)

        self._tools_grid = Gtk.FlowBox()
        self._tools_grid.add_css_class("tools-action-grid")
        self._tools_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        self._tools_grid.set_max_children_per_line(2)
        self._tools_grid.set_min_children_per_line(1)
        self._tools_grid.set_homogeneous(True)
        self._tools_grid.set_column_spacing(10)
        self._tools_grid.set_row_spacing(10)
        self._tools_grid.set_hexpand(True)
        body.append(self._tools_grid)

        out_lab = Gtk.Label(label="Output", xalign=0)
        out_lab.add_css_class("section-label")
        body.append(out_lab)

        self._output = Gtk.TextView()
        self._output.set_editable(False)
        self._output.set_cursor_visible(False)
        self._output.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._output.set_top_margin(8)
        self._output.set_bottom_margin(8)
        self._output.set_left_margin(10)
        self._output.set_right_margin(10)
        self._output.add_css_class("tools-output")
        self._output_buf = self._output.get_buffer()
        self._set_output(
            "Pick a tool above.\n"
            "Uses the active path and spectred — does not Connect or Disconnect."
        )

        out_scroll = Gtk.ScrolledWindow()
        out_scroll.add_css_class("tools-output-scroll")
        out_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        out_scroll.set_min_content_height(140)
        out_scroll.set_max_content_height(220)
        out_scroll.set_hexpand(True)
        out_scroll.set_child(self._output)
        body.append(out_scroll)

        # Lab companions — gated by Lab plugin
        self._lab_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._lab_section.set_margin_top(8)
        lab_lab = Gtk.Label(label="Lab companions", xalign=0)
        lab_lab.add_css_class("section-label")
        self._lab_section.append(lab_lab)
        self._lab_section.append(self._lab_row())
        body.append(self._lab_section)

        page.append(scroll_body(body, margin=18))
        self._rebuild_tools_ui()
        return page

    def _enabled_plugins(self) -> list[str]:
        if self._services is None:
            return []
        return self._services.plugins_enabled()

    def _open_plugins_settings(self) -> None:
        # Marketplace is the primary plugins surface; Settings packs remain.
        if self._on_navigate:
            self._on_navigate("marketplace")

    def _rebuild_tools_ui(self) -> None:
        """Show/hide tools and lab row from Settings → Plugins."""
        from core.plugins import (
            enabled_summary,
            lab_companions_visible,
            tool_allowed,
        )

        enabled = self._enabled_plugins()
        grid = getattr(self, "_tools_grid", None)
        if grid is not None:
            while child := grid.get_first_child():
                grid.remove(child)
            for tool in _TOOLS:
                if not tool_allowed(enabled, tool.target or tool.id):
                    continue
                cell = Gtk.FlowBoxChild()
                cell.set_child(self._action_tile(tool))
                grid.append(cell)

        if getattr(self, "_lab_section", None) is not None:
            self._lab_section.set_visible(lab_companions_visible(enabled))

        banner = getattr(self, "_plugins_banner", None)
        if banner is not None:
            operate = bool(
                getattr(self._services.config, "operate_enabled", False)
            )
            if not enabled:
                banner.set_visible(True)
                self._plugins_banner_title.set_text("Lab packs off")
                self._plugins_banner_sub.set_text(
                    "Enable packs in Settings → Plugins. "
                    "C2 (Hogwarts) needs Operate posture."
                )
            else:
                banner.set_visible(True)
                self._plugins_banner_title.set_text("Plugins active")
                self._plugins_banner_sub.set_text(
                    enabled_summary(enabled, operate_enabled=operate)
                )

    def _action_tile(self, tool: _Tool) -> Gtk.Widget:
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.add_css_class("tools-action-tile")
        btn.set_hexpand(True)
        btn.set_tooltip_text(tool.subtitle)
        btn.connect("clicked", lambda *_: self._on_tool(tool))

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        ic_wrap = Gtk.CenterBox()
        ic_wrap.add_css_class("tools-action-icon-wrap")
        ic_wrap.set_size_request(40, 40)
        ic = self._tool_icon(tool, size=20)
        ic_wrap.set_center_widget(ic)
        row.append(ic_wrap)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col.set_hexpand(True)
        title = Gtk.Label(label=tool.title, xalign=0)
        title.add_css_class("tools-action-title")
        col.append(title)
        sub = Gtk.Label(label=tool.subtitle, xalign=0, wrap=True)
        sub.add_css_class("tools-action-sub")
        col.append(sub)
        row.append(col)

        chev = Gtk.Image.new_from_icon_name("go-next-symbolic")
        chev.set_pixel_size(12)
        chev.add_css_class("tools-action-chev")
        chev.set_valign(Gtk.Align.CENTER)
        row.append(chev)

        btn.set_child(row)
        return btn

    def _tool_icon(self, tool: _Tool, *, size: int = 20) -> Gtk.Image:
        """Theme icon, or brand asset recolored like Settings (Spectre, etc.)."""
        if tool.asset:
            from gi.repository import Gdk, GdkPixbuf, GLib

            from app_config import project_root

            assets = project_root() / "data" / "assets"
            candidates = [assets / tool.asset]
            if tool.asset.endswith(".png"):
                candidates.append(assets / tool.asset.replace(".png", ".svg"))
            elif tool.asset.endswith(".svg"):
                candidates.append(assets / tool.asset.replace(".svg", ".png"))

            scale = 1
            display = Gdk.Display.get_default()
            if display is not None:
                mons = display.get_monitors()
                if mons.get_n_items() > 0:
                    mon = mons.get_item(0)
                    if mon is not None:
                        scale = max(1, int(mon.get_scale_factor()))

            asset_l = tool.asset.lower()
            for path in candidates:
                if not path.is_file():
                    continue
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_size(
                        str(path), size * scale, size * scale
                    )
                except GLib.Error:
                    continue
                if "spectre" in asset_l or "mullvad" in asset_l:
                    pb = self._recolor_mono_pixbuf(pb, 199, 212, 238)
                texture = Gdk.Texture.new_for_pixbuf(pb)
                ic = Gtk.Image.new_from_paintable(texture)
                ic.set_pixel_size(size)
                ic.set_size_request(size, size)
                ic.add_css_class("tools-action-icon")
                if "spectre" in asset_l:
                    ic.add_css_class("settings-tile-icon-spectre")
                ic.set_halign(Gtk.Align.CENTER)
                ic.set_valign(Gtk.Align.CENTER)
                return ic

        ic = Gtk.Image.new_from_icon_name(tool.icon)
        ic.set_pixel_size(size)
        ic.add_css_class("tools-action-icon")
        ic.set_halign(Gtk.Align.CENTER)
        ic.set_valign(Gtk.Align.CENTER)
        return ic

    @staticmethod
    def _recolor_mono_pixbuf(pb, r: int, g: int, b: int):
        from gi.repository import GdkPixbuf

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
                if n >= 4 and buf[i + 3] < 8:
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

    def _lab_row(self) -> Gtk.Widget:
        """Drift / Mirage / Sounding — detect install, per-tool Install/Update."""
        from core.lab_companions import COMPANIONS, probe_companion

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("tools-lab-row")
        self._lab_rows: dict[str, dict] = {}

        for c in COMPANIONS:
            st = probe_companion(c)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.add_css_class("tools-lab-item")

            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            col.set_hexpand(True)
            n = Gtk.Label(label=c.title, xalign=0)
            n.add_css_class("tools-lab-name")
            col.append(n)
            r = Gtk.Label(label=c.role, xalign=0)
            r.add_css_class("muted")
            col.append(r)
            loc = Gtk.Label(label="", xalign=0, wrap=True)
            loc.add_css_class("tools-lab-loc")
            loc.add_css_class("muted")
            col.append(loc)
            row.append(col)

            badge = Gtk.Label(label="")
            badge.add_css_class("tools-lab-badge")
            badge.set_valign(Gtk.Align.CENTER)
            row.append(badge)

            install_btn = Gtk.Button(label="Install")
            install_btn.add_css_class("suggested-action")
            install_btn.set_valign(Gtk.Align.CENTER)
            install_btn.connect(
                "clicked",
                lambda _b, cid=c.id: self._install_companion(cid),
            )
            row.append(install_btn)

            link = Gtk.LinkButton(
                uri=f"https://github.com/{c.github}",
                label="GitHub",
            )
            link.add_css_class("flat")
            link.set_valign(Gtk.Align.CENTER)
            row.append(link)

            box.append(row)
            self._lab_rows[c.id] = {
                "badge": badge,
                "loc": loc,
                "btn": install_btn,
                "state": st,
            }
            self._paint_lab_row(c.id, st)

        return box

    def _paint_lab_row(self, companion_id: str, st) -> None:
        row = self._lab_rows.get(companion_id)
        if not row:
            return
        badge: Gtk.Label = row["badge"]
        loc: Gtk.Label = row["loc"]
        btn: Gtk.Button = row["btn"]
        for cls in ("tools-lab-badge-ok", "tools-lab-badge-missing"):
            badge.remove_css_class(cls)
        if st.installed:
            src = st.source or "installed"
            label = {
                "path": "on PATH",
                "lab": "installed",
                "workspace": "workspace",
            }.get(src, "installed")
            badge.set_text(label)
            badge.add_css_class("tools-lab-badge-ok")
            loc.set_text(st.location or "")
            loc.set_visible(bool(st.location))
            btn.set_label("Update")
            btn.set_tooltip_text("git pull latest into Reach lab directory")
        else:
            badge.set_text("not installed")
            badge.add_css_class("tools-lab-badge-missing")
            loc.set_text("")
            loc.set_visible(False)
            btn.set_label("Install")
            btn.set_tooltip_text(
                f"Clone {st.companion.github} into the Reach lab folder "
                f"and add a ~/.local/bin/{st.companion.binary} launcher"
            )
        btn.set_sensitive(True)
        row["state"] = st

    def _install_companion(self, companion_id: str) -> None:
        if self._busy:
            self._toast("A tool is already running…")
            return
        from core.lab_companions import companion_by_id, install_companion, probe_companion

        c = companion_by_id(companion_id)
        title = c.title if c else companion_id
        row = self._lab_rows.get(companion_id)
        if row:
            row["btn"].set_sensitive(False)
            row["btn"].set_label("…")

        def work() -> str:
            ok, msg = install_companion(companion_id)
            return ("OK\n" if ok else "FAILED\n") + msg

        def after() -> None:
            # refresh status for this companion
            st = probe_companion(
                companion_by_id(companion_id)  # type: ignore[arg-type]
            ) if companion_by_id(companion_id) else None
            if st is not None:
                self._paint_lab_row(companion_id, st)

        self._busy = True
        self._set_output(f"Installing {title}…")

        def thread() -> None:
            try:
                result = work()
            except Exception as exc:
                result = f"FAILED\n{title} install error: {exc}"

            def done() -> bool:
                self._busy = False
                self._set_output(result)
                first = (result.splitlines() or [""])[0][:120]
                if first:
                    self._toast(f"{title}: {first}")
                after()
                return False

            GLib.idle_add(done)

        threading.Thread(
            target=thread, name=f"reach-install-{companion_id}", daemon=True
        ).start()

    # ── TCP probe sub-page ────────────────────────────────────────

    def _build_probe_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.set_hexpand(True)
        page.set_vexpand(True)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.add_css_class("pane-header")
        back = Gtk.Button()
        back.add_css_class("flat")
        back.add_css_class("circular")
        back.set_icon_name("go-previous-symbolic")
        back.set_tooltip_text("Back to Tools")
        back.connect("clicked", lambda *_: self._view.set_visible_child_name("main"))
        header.append(back)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        titles.set_hexpand(True)
        t = Gtk.Label(label="TCP probe", xalign=0)
        t.add_css_class("pane-header-title")
        titles.append(t)
        s = Gtk.Label(
            label="TCP check only · not Connect · outside vantage",
            xalign=0,
        )
        s.add_css_class("pane-header-sub")
        titles.append(s)
        header.append(titles)
        page.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_halign(Gtk.Align.CENTER)
        body.set_hexpand(True)
        body.set_size_request(420, -1)

        self._probe_host = Gtk.Entry()
        self._probe_host.set_placeholder_text("host or IP")
        self._probe_host.set_hexpand(True)
        body.append(self._field("Host", self._probe_host))

        self._probe_port = Gtk.SpinButton.new_with_range(1, 65535, 1)
        self._probe_port.set_value(443)
        self._probe_port.set_hexpand(True)
        body.append(self._field("Port", self._probe_port))

        run = Gtk.Button(label="Probe")
        run.add_css_class("suggested-action")
        run.set_halign(Gtk.Align.START)
        run.connect("clicked", self._run_probe)
        body.append(run)

        self._probe_result = Gtk.Label(label="", xalign=0, wrap=True)
        self._probe_result.add_css_class("tools-probe-result")
        body.append(self._probe_result)

        page.append(scroll_body(body, margin=20))
        return page

    def _field(self, title: str, child: Gtk.Widget) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lab = Gtk.Label(label=title, xalign=0)
        lab.add_css_class("field-label")
        box.append(lab)
        box.append(child)
        return box

    # ── Actions ───────────────────────────────────────────────────

    def _on_tool(self, tool: _Tool) -> None:
        if tool.action == "nav":
            if self._on_navigate:
                self._on_navigate(tool.target)
            return
        if tool.action == "page":
            self._view.set_visible_child_name(tool.target)
            return
        if self._busy:
            self._toast("A tool is already running…")
            return
        from core.plugins import tool_allowed

        if not tool_allowed(self._enabled_plugins(), tool.target or tool.id):
            self._set_output(
                f"{tool.title} requires a plugin.\n"
                "Settings → Plugins — enable the matching pack, then Save."
            )
            self._toast("Plugin required")
            return
        runners = {
            "core": self._run_core_status,
            "preflight": self._run_preflight,
            "deps": self._run_deps,
            "unlock": self._run_unlock,
            "clearnet": self._run_clearnet,
            "fingerprint": self._run_path_fingerprint,
            "socks": self._run_copy_socks,
            "logs": self._run_open_logs,
        }
        fn = runners.get(tool.target)
        if fn is not None:
            fn()

    def _set_output(self, text: str) -> None:
        self._output_buf.set_text(text or "")

    def _toast(self, msg: str) -> None:
        if self._on_toast:
            self._on_toast(msg)

    def _run_async(self, title: str, worker: Callable[[], str]) -> None:
        self._busy = True
        self._set_output(f"{title}…")

        def thread() -> None:
            try:
                result = worker()
            except Exception as exc:
                result = f"{title} failed: {exc}"

            def done() -> bool:
                self._busy = False
                self._set_output(result)
                # First line as toast
                first = (result.splitlines() or [""])[0][:120]
                if first:
                    self._toast(first)
                return False

            GLib.idle_add(done)

        threading.Thread(target=thread, name=f"reach-tool-{title}", daemon=True).start()

    def _run_core_status(self) -> None:
        if self._services is None:
            self._set_output("Services unavailable.")
            return

        def work() -> str:
            st = self._services.core.status(force=True)
            hop_n = getattr(st, "hop_count", None) or len(st.hops or [])
            lines = [
                f"State: {st.state.value}",
                f"Message: {st.message or '—'}",
                f"Path: {st.path_summary or '—'}",
                f"Hops: {hop_n}",
                f"Profile: {st.active_profile or st.profile_id or '—'}",
                f"Local proxy: {st.local_proxy or '—'}",
            ]
            note = (getattr(st, "fingerprint_note", None) or "").strip()
            if note:
                lines.append(f"Fingerprint: {note}")
            if st.routing_mode:
                lines.append(
                    f"Routing: {st.routing_mode}"
                    + (
                        f" ({'active' if st.routing_active else 'inactive'})"
                        if st.routing_active is not None
                        else ""
                    )
                )
            if st.kill_switch_active is not None:
                lines.append(
                    f"Kill switch: {'active' if st.kill_switch_active else 'off'}"
                )
            from core.client import default_socket_path

            sock = (
                getattr(self._services.core, "socket_path", None)
                or self._services.config.core_socket
                or default_socket_path()
            )
            lines.append(f"Socket: {sock}")
            return "\n".join(lines)

        self._run_async("Core status", work)

    def _run_preflight(self) -> None:
        if self._services is None:
            self._set_output("Services unavailable.")
            return

        def work() -> str:
            cfg = self._services.config
            pid = (cfg.last_profile_id or "").strip()
            profile = self._services.profiles.get(pid) if pid else None
            if profile is None:
                lst = self._services.profiles.list()
                profile = lst[0] if lst else None
            if profile is None:
                return "No path profile — create one under Paths or Territories."
            try:
                ready = profile_readiness(
                    profile,
                    self._services.backends,
                    routing_mode=cfg.routing_mode or "system",
                    kill_switch=bool(cfg.kill_switch),
                    live=True,
                )
            except TypeError:
                ready = profile_readiness(profile, self._services.backends)
            lines = [
                f"Profile: {profile.name}",
                f"Result: {'READY' if ready.ok else 'NOT READY'}",
                f"Summary: {ready.summary}",
            ]
            if ready.issues:
                lines.append("")
                lines.append("Issues:")
                for i in ready.issues:
                    lines.append(f"  • {i}")
            if ready.warnings:
                lines.append("")
                lines.append("Warnings:")
                for w in ready.warnings:
                    lines.append(f"  • {w}")
            if not ready.ok and not ready.issues:
                lines.append(f"Detail: {ready.detail}")
            return "\n".join(lines)

        self._run_async("Path preflight", work)

    def _run_deps(self) -> None:
        def work() -> str:
            checks: list[tuple[str, str | None]] = [
                ("xray (REALITY)", shutil.which("xray")),
                ("wg-quick (WireGuard)", shutil.which("wg-quick")),
                ("mullvad CLI", shutil.which("mullvad")),
                ("spectre", shutil.which("spectre")),
                ("spectred", shutil.which("spectred")),
                ("clearnet-netns", shutil.which("clearnet-netns")),
                ("spectre-nft", shutil.which("spectre-nft")),
                ("curl", shutil.which("curl")),
            ]
            # Common install locations
            home_xray = Path.home() / ".local" / "bin" / "xray"
            lines = ["Dependency check", ""]
            for label, path in checks:
                if path:
                    lines.append(f"  ✓  {label}: {path}")
                elif label.startswith("xray") and home_xray.is_file():
                    lines.append(f"  ✓  {label}: {home_xray}")
                else:
                    lines.append(f"  ✗  {label}: not found")
            # Laminar (O1 path fingerprint) — workspace or lab, not always on PATH
            try:
                from core.path_fingerprint import ensure_laminar_importable

                lam_err = ensure_laminar_importable()
                if lam_err is None:
                    lines.append("  ✓  laminar (path fingerprint lab): importable")
                else:
                    lines.append("  ·  laminar: not found (Tools → Lab companions)")
            except Exception as exc:
                lines.append(f"  ·  laminar: check failed ({exc})")
            # spectre-nft common paths
            for p in (
                "/usr/local/libexec/spectre/spectre-nft",
                "/usr/libexec/spectre/spectre-nft",
            ):
                if Path(p).is_file() and not shutil.which("spectre-nft"):
                    lines.append(f"  ·  spectre-nft at {p} (not on PATH)")
                    break
            return "\n".join(lines)

        self._run_async("Dependencies", work)

    def _parent_window(self) -> Gtk.Window | None:
        root = self.get_root()
        return root if isinstance(root, Gtk.Window) else None

    def _run_unlock(self) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self._parent_window(),
            heading="Unlock network?",
            body=(
                "This runs spectre unlock and clears kill-switch / path "
                "firewall rules so the machine can reach the clearnet again.\n\n"
                "Use this when a kill switch left you offline. It does not "
                "disconnect Mullvad by itself."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("unlock", "Unlock")
        dialog.set_response_appearance(
            "unlock", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d: Adw.MessageDialog, response: str) -> None:
            if response != "unlock":
                return
            self._run_unlock_confirmed()

        dialog.connect("response", on_response)
        dialog.present()

    def _run_unlock_confirmed(self) -> None:
        def work() -> str:
            spectre = shutil.which("spectre")
            if not spectre:
                return (
                    "spectre CLI not found.\n"
                    "Install Spectre core, then run: spectre unlock"
                )
            try:
                r = subprocess.run(  # noqa: S603
                    [spectre, "unlock"],
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
            except subprocess.TimeoutExpired:
                return "spectre unlock timed out (45s)."
            except OSError as exc:
                return f"Could not run spectre unlock: {exc}"
            out = (r.stdout or "").strip()
            err = (r.stderr or "").strip()
            if r.returncode == 0:
                return f"Unlock OK\n{out or err or 'Network unlock requested.'}"
            return f"Unlock failed (exit {r.returncode})\n{err or out or 'No output'}"

        self._run_async("Unlock network", work)

    def _run_clearnet(self) -> None:
        def work() -> str:
            from core.clearnet_health import check_clearnet

            try:
                h = check_clearnet()
            except Exception as exc:
                return f"Clearnet check failed: {exc}"
            lines = [
                f"Clearnet: {'OK' if h.ok else 'NEEDS ATTENTION'}",
                h.summary or "",
            ]
            if h.detail_lines:
                lines.append("")
                lines.extend(f"  {d}" for d in h.detail_lines)
            if h.sample_mbps is not None:
                lines.append(f"Sample: {h.sample_mbps:.1f} Mbit/s")
            if h.inet_ping_ms is not None:
                lines.append(f"Ping: {h.inet_ping_ms:.0f} ms")
            return "\n".join(lines)

        self._run_async("Clearnet health", work)

    def _run_path_fingerprint(self) -> None:
        """O1 lab: Laminar F2 ΔRTT against live Spectre SOCKS."""
        if self._services is None:
            self._set_output("Services unavailable.")
            return

        def work() -> str:
            from core.client import CoreState
            from core.path_fingerprint import (
                format_report,
                measure_path_fingerprint,
                status_fields_from_core,
            )

            st = self._services.core.status(force=True)
            fields = status_fields_from_core(st)
            if st.state != CoreState.CONNECTED or not fields["local_proxy"]:
                lines = [
                    "Path fingerprint (lab)",
                    "",
                    "Connect a path on Home first, then run this tool.",
                    f"State: {st.state.value}",
                    f"Proxy: {fields['local_proxy'] or '—'}",
                ]
                if fields.get("fingerprint_note"):
                    lines.append(f"Note: {fields['fingerprint_note']}")
                return "\n".join(lines)
            rep = measure_path_fingerprint(
                local_proxy=fields["local_proxy"],
                path_summary=fields["path_summary"],
                hops=fields["hops"],
                hop_count=fields["hop_count"],
                fingerprint_note=fields["fingerprint_note"],
            )
            return format_report(rep)

        self._run_async("Path fingerprint", work)

    def _run_copy_socks(self) -> None:
        if self._services is None:
            self._set_output("Services unavailable.")
            return
        try:
            st = self._services.core.status(force=True)
        except Exception as exc:
            self._set_output(f"Could not read core status: {exc}")
            return
        proxy = (st.local_proxy or "").strip()
        if st.state != CoreState.CONNECTED or not proxy:
            self._set_output(
                "Not connected — Connect a path on Home first.\n"
                f"State: {st.state.value}"
            )
            self._toast("Not connected")
            return
        # Prefer socks5:// form
        text = proxy
        if not proxy.startswith("socks"):
            text = f"socks5://{proxy}" if "://" not in proxy else proxy
        try:
            display = Gdk.Display.get_default()
            if display is not None:
                clipboard = display.get_clipboard()
                clipboard.set(text)
                self._set_output(f"Copied to clipboard:\n{text}")
                self._toast("SOCKS URL copied")
                return
        except Exception:
            pass
        self._set_output(f"SOCKS (copy manually):\n{text}")
        self._toast(text)

    def _run_open_logs(self) -> None:
        path = log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.is_file():
                path.write_text("# Reach desktop log\n", encoding="utf-8")
        except OSError as exc:
            self._set_output(f"Cannot create log: {exc}")
            return
        try:
            subprocess.Popen(  # noqa: S603
                ["xdg-open", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._set_output(f"Opened:\n{path}")
            self._toast("Opened desktop log")
        except OSError:
            self._set_output(f"Log file:\n{path}\n(Could not open automatically)")

    def _run_probe(self, *_a) -> None:
        host = (self._probe_host.get_text() or "").strip()
        port = int(self._probe_port.get_value())
        if not host:
            self._probe_result.set_text("Enter a host first")
            return
        self._probe_result.set_text(f"Probing {host}:{port}…")

        def work() -> None:
            ok = False
            err = ""
            try:
                with socket.create_connection((host, port), timeout=4.0):
                    ok = True
            except OSError as exc:
                err = str(exc)

            def done() -> bool:
                if ok:
                    msg = f"Open — TCP connect to {host}:{port} succeeded (outside vantage)."
                else:
                    msg = f"Closed / filtered — {host}:{port}\n{err}"
                self._probe_result.set_text(msg)
                self._set_output(msg)
                self._toast("TCP open" if ok else "TCP failed")
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, name="reach-tcp-probe", daemon=True).start()

    def reload(self) -> None:
        """Refresh plugin-gated tools and lab companion status."""
        try:
            self._rebuild_tools_ui()
        except Exception:
            pass
        try:
            from core.lab_companions import probe_all

            for st in probe_all():
                if st.companion.id in getattr(self, "_lab_rows", {}):
                    self._paint_lab_row(st.companion.id, st)
        except Exception:
            pass
