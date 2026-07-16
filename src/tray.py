"""System tray applet for Spectre Desktop (StatusNotifierItem over D-Bus).

Cinnamon uses xapp-sn-watcher → xapp-status applet:

- Object path must be exactly ``/StatusNotifierItem`` when registered by bus name.
- Theme icon names (like blueman) paint reliably; absolute paths often show a
  broken caution glyph.
- Right-click menu is ``com.canonical.dbusmenu`` at ``/MenuBar`` (secondary menu).

Icons (custom, not Mullvad-branded):

- Connected → green closed lock
- Disconnected / offline → red open lock
- Connecting → amber lock
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from gi.repository import Gio, GLib

from app_config import APPLICATION_NAME, project_root, user_data_dir
from core.client import CoreState

_SNI = "org.kde.StatusNotifierItem"
_WATCHER_NAME = "org.kde.StatusNotifierWatcher"
_WATCHER_PATH = "/StatusNotifierWatcher"
_WATCHER_IFACE = "org.kde.StatusNotifierWatcher"
_MENU = "com.canonical.dbusmenu"

_KEYS = {
    CoreState.CONNECTED: "spectre-tray-locked",
    CoreState.CONNECTING: "spectre-tray-connecting",
    CoreState.DISCONNECTED: "spectre-tray-unlocked",
    CoreState.UNAVAILABLE: "spectre-tray-unlocked",
}

_ICON_SIZES = (16, 22, 24, 32, 48)

# Menu item ids (stable for Event handling)
_MID_SHOW = 1
_MID_CONNECT = 2
_MID_DISCONNECT = 3
_MID_DISCONNECT_QUIT = 4
_MID_SEP = 5
_MID_QUIT = 6


def _hicolor_root() -> Path:
    return Path.home() / ".local" / "share" / "icons" / "hicolor"


def _svg_dir() -> Path:
    return project_root() / "data" / "icons" / "hicolor" / "scalable" / "status"


def ensure_tray_icons() -> None:
    """Install tray icons into the user hicolor theme + flat tray dirs."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    svg_dir = _svg_dir()
    flat_dirs = [
        project_root() / "data" / "icons" / "tray",
        user_data_dir() / "tray-icons",
    ]
    for d in flat_dirs:
        d.mkdir(parents=True, exist_ok=True)

    hicolor = _hicolor_root()
    scalable = hicolor / "scalable" / "status"
    scalable.mkdir(parents=True, exist_ok=True)

    ordered: list[str] = []
    seen: set[str] = set()
    for n in _KEYS.values():
        if n not in seen:
            seen.add(n)
            ordered.append(n)

    for name in ordered:
        svg = svg_dir / f"{name}.svg"
        if not svg.is_file():
            continue
        dest_svg = scalable / f"{name}.svg"
        try:
            if not dest_svg.is_file() or dest_svg.read_bytes() != svg.read_bytes():
                dest_svg.write_bytes(svg.read_bytes())
        except OSError:
            pass

        for size in _ICON_SIZES:
            size_dir = hicolor / f"{size}x{size}" / "status"
            size_dir.mkdir(parents=True, exist_ok=True)
            dest = size_dir / f"{name}.png"
            if dest.is_file() and dest.stat().st_mtime >= svg.stat().st_mtime:
                continue
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_size(str(svg), size, size)
                if pb is not None:
                    pb.savev(str(dest), "png", [], [])
            except Exception:
                pass

        for dest_dir in flat_dirs:
            dest = dest_dir / f"{name}.png"
            if dest.is_file() and dest.stat().st_mtime >= svg.stat().st_mtime:
                continue
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_size(str(svg), 32, 32)
                if pb is not None:
                    pb.savev(str(dest), "png", [], [])
            except Exception:
                pass

    try:
        subprocess.run(
            ["gtk-update-icon-cache", "-f", "-t", str(hicolor)],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def icon_name_for_state(state: CoreState) -> str:
    return _KEYS.get(state, "spectre-tray-unlocked")


def icon_file_for_state(state: CoreState) -> str:
    name = icon_name_for_state(state)
    candidates = [
        _hicolor_root() / "32x32" / "status" / f"{name}.png",
        _hicolor_root() / "24x24" / "status" / f"{name}.png",
        project_root() / "data" / "icons" / "tray" / f"{name}.png",
        user_data_dir() / "tray-icons" / f"{name}.png",
    ]
    for p in candidates:
        if p.is_file():
            return str(p.resolve())
    ensure_tray_icons()
    for p in candidates:
        if p.is_file():
            return str(p.resolve())
    return ""


def _png_to_pixmap(path: str, size: int = 32) -> list[tuple[int, int, bytes]]:
    if not path or not Path(path).is_file():
        return []
    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        pb = GdkPixbuf.Pixbuf.new_from_file_at_size(path, size, size)
    except Exception:
        return []
    if pb is None:
        return []
    if pb.get_n_channels() == 3:
        pb = pb.add_alpha(False, 0, 0, 0)
    w, h = pb.get_width(), pb.get_height()
    rowstride = pb.get_rowstride()
    pixels = pb.get_pixels()
    nch = pb.get_n_channels()
    out = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            i = y * rowstride + x * nch
            r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
            a = pixels[i + 3] if nch >= 4 else 255
            o = (y * w + x) * 4
            struct.pack_into(">BBBB", out, o, a, r, g, b)
    return [(w, h, bytes(out))]


def tooltip_for(state: CoreState, detail: str = "") -> tuple[str, str]:
    titles = {
        CoreState.CONNECTED: "Spectre · Protected",
        CoreState.CONNECTING: "Spectre · Connecting…",
        CoreState.DISCONNECTED: "Spectre · Not connected",
        CoreState.UNAVAILABLE: "Spectre · Core offline",
    }
    return titles.get(state, APPLICATION_NAME), (detail or "").strip()


def _menu_props(**kwargs: object) -> dict[str, GLib.Variant]:
    """Build a{sv} menu item properties for dbusmenu."""
    out: dict[str, GLib.Variant] = {}
    for key, val in kwargs.items():
        key = key.replace("_", "-")
        if isinstance(val, bool):
            out[key] = GLib.Variant("b", val)
        elif isinstance(val, int):
            out[key] = GLib.Variant("i", val)
        else:
            out[key] = GLib.Variant("s", str(val))
    return out


class SpectreTray:
    def __init__(
        self,
        *,
        on_show: Callable[[], None] | None = None,
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        on_disconnect_quit: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
    ) -> None:
        self._on_show = on_show
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_disconnect_quit = on_disconnect_quit
        self._on_quit = on_quit
        self._bus: Gio.DBusConnection | None = None
        self._reg_sni: int | None = None
        self._reg_menu: int | None = None
        self._own: int | None = None
        pid = os.getpid()
        self._bus_name = f"org.kde.StatusNotifierItem-{pid}-1"
        self._obj = "/StatusNotifierItem"
        self._menu_obj = "/MenuBar"

        ensure_tray_icons()
        self._apply_visual(CoreState.DISCONNECTED, "")
        self._path_up = False
        self._connecting = False
        self._ok = False
        self._status = "Active"
        self._rev = 1

    def _apply_visual(self, state: CoreState, detail: str = "") -> None:
        self._icon_name = icon_name_for_state(state)
        self._icon_file = icon_file_for_state(state)
        self._icon_theme_path = str(Path.home() / ".local" / "share" / "icons")
        self._pixmaps: list[tuple[int, int, bytes]] = []
        if self._icon_file:
            self._pixmaps = _png_to_pixmap(self._icon_file, 32)
        self._tip_title, self._tip_body = tooltip_for(state, detail)

    @property
    def available(self) -> bool:
        return self._ok

    def start(self) -> bool:
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return False

        sni_xml = f"""<node>
          <interface name="{_SNI}">
            <property name="Category" type="s" access="read"/>
            <property name="Id" type="s" access="read"/>
            <property name="Title" type="s" access="read"/>
            <property name="Status" type="s" access="read"/>
            <property name="IconName" type="s" access="read"/>
            <property name="IconThemePath" type="s" access="read"/>
            <property name="IconPixmap" type="a(iiay)" access="read"/>
            <property name="AttentionIconName" type="s" access="read"/>
            <property name="OverlayIconName" type="s" access="read"/>
            <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
            <property name="ItemIsMenu" type="b" access="read"/>
            <property name="Menu" type="o" access="read"/>
            <method name="Activate">
              <arg direction="in" type="i" name="x"/>
              <arg direction="in" type="i" name="y"/>
            </method>
            <method name="SecondaryActivate">
              <arg direction="in" type="i" name="x"/>
              <arg direction="in" type="i" name="y"/>
            </method>
            <method name="ContextMenu">
              <arg direction="in" type="i" name="x"/>
              <arg direction="in" type="i" name="y"/>
            </method>
            <method name="Scroll">
              <arg direction="in" type="i" name="delta"/>
              <arg direction="in" type="s" name="orientation"/>
            </method>
            <signal name="NewIcon"/>
            <signal name="NewToolTip"/>
            <signal name="NewStatus"><arg type="s" name="status"/></signal>
          </interface>
        </node>"""

        menu_xml = f"""<node>
          <interface name="{_MENU}">
            <property name="Version" type="u" access="read"/>
            <property name="TextDirection" type="s" access="read"/>
            <property name="Status" type="s" access="read"/>
            <property name="IconThemePath" type="as" access="read"/>
            <method name="GetLayout">
              <arg direction="in" type="i" name="parentId"/>
              <arg direction="in" type="i" name="recursionDepth"/>
              <arg direction="in" type="as" name="propertyNames"/>
              <arg direction="out" type="u" name="revision"/>
              <arg direction="out" type="(ia{{sv}}av)" name="layout"/>
            </method>
            <method name="GetGroupProperties">
              <arg direction="in" type="ai" name="ids"/>
              <arg direction="in" type="as" name="propertyNames"/>
              <arg direction="out" type="a(ia{{sv}})" name="properties"/>
            </method>
            <method name="GetProperty">
              <arg direction="in" type="i" name="id"/>
              <arg direction="in" type="s" name="name"/>
              <arg direction="out" type="v" name="value"/>
            </method>
            <method name="Event">
              <arg direction="in" type="i" name="id"/>
              <arg direction="in" type="s" name="eventId"/>
              <arg direction="in" type="v" name="data"/>
              <arg direction="in" type="u" name="timestamp"/>
            </method>
            <method name="EventGroup">
              <arg direction="in" type="a(isvu)" name="events"/>
              <arg direction="out" type="ai" name="idErrors"/>
            </method>
            <method name="AboutToShow">
              <arg direction="in" type="i" name="id"/>
              <arg direction="out" type="b" name="needUpdate"/>
            </method>
            <method name="AboutToShowGroup">
              <arg direction="in" type="ai" name="ids"/>
              <arg direction="out" type="ai" name="updatesNeeded"/>
              <arg direction="out" type="ai" name="idErrors"/>
            </method>
            <signal name="ItemsPropertiesUpdated">
              <arg type="a(ia{{sv}})" name="updatedProps"/>
              <arg type="a(ias)" name="removedProps"/>
            </signal>
            <signal name="LayoutUpdated">
              <arg type="u" name="revision"/>
              <arg type="i" name="parent"/>
            </signal>
          </interface>
        </node>"""

        try:
            self._reg_sni = self._bus.register_object(
                self._obj,
                Gio.DBusNodeInfo.new_for_xml(sni_xml).interfaces[0],
                self._on_sni_call,
                self._on_sni_get,
                None,
            )
            self._reg_menu = self._bus.register_object(
                self._menu_obj,
                Gio.DBusNodeInfo.new_for_xml(menu_xml).interfaces[0],
                self._on_menu_call,
                self._on_menu_get,
                None,
            )
        except GLib.Error:
            return False

        self._own = Gio.bus_own_name_on_connection(
            self._bus,
            self._bus_name,
            Gio.BusNameOwnerFlags.NONE,
            lambda *_: self._on_name_acquired(),
            None,
        )
        self._ok = True
        self._status = "Active"
        # Also schedule delayed registers: name-acquired can race the panel
        # watcher (Cinnamon/xapp) so a pure sync register often no-ops.
        GLib.timeout_add(100, self._retry_register_with_watcher)
        GLib.timeout_add(750, self._retry_register_with_watcher)
        GLib.timeout_add(2000, self._retry_register_with_watcher)
        return True

    def stop(self) -> None:
        """Unregister the tray so the panel drops the icon immediately.

        Order matters: Passive status → unregister objects → release bus name →
        flush the main context so NameOwnerChanged reaches xapp-sn-watcher
        before the process exits. Skipping the flush leaves a ghost lock icon.
        """
        if self._bus is not None and self._ok:
            self._status = "Passive"
            try:
                self._bus.emit_signal(
                    None,
                    self._obj,
                    _SNI,
                    "NewStatus",
                    GLib.Variant("(s)", ("Passive",)),
                )
            except GLib.Error:
                pass
        if self._bus is not None:
            if self._reg_sni is not None:
                try:
                    self._bus.unregister_object(self._reg_sni)
                except Exception:
                    pass
                self._reg_sni = None
            if self._reg_menu is not None:
                try:
                    self._bus.unregister_object(self._reg_menu)
                except Exception:
                    pass
                self._reg_menu = None
        if self._own is not None:
            try:
                Gio.bus_unown_name(self._own)
            except Exception:
                pass
            self._own = None
        self._ok = False
        self._bus = None
        # Flush pending D-Bus so the watcher sees NameOwnerChanged now,
        # not after the process is already gone (or stuck half-quit).
        try:
            ctx = GLib.MainContext.default()
            for _ in range(40):
                if not ctx.pending():
                    break
                ctx.iteration(False)
        except Exception:
            pass

    def update_state(self, state: CoreState, detail: str = "") -> None:
        was_up = self._path_up
        was_conn = self._connecting
        self._path_up = state == CoreState.CONNECTED
        self._connecting = state == CoreState.CONNECTING
        self._apply_visual(state, detail)
        if not self._ok or self._bus is None:
            return
        for sig in ("NewIcon", "NewToolTip"):
            try:
                self._bus.emit_signal(None, self._obj, _SNI, sig, None)
            except GLib.Error:
                pass
        # Refresh right-click labels when connect state flips
        if was_up != self._path_up or was_conn != self._connecting:
            self._emit_layout_updated()

    def _emit_layout_updated(self) -> None:
        self._rev += 1
        if self._bus is None:
            return
        try:
            self._bus.emit_signal(
                None,
                self._menu_obj,
                _MENU,
                "LayoutUpdated",
                GLib.Variant("(ui)", (self._rev, 0)),
            )
        except GLib.Error:
            pass

    def _on_name_acquired(self) -> None:
        self._register_with_watcher()

    def _service_ids(self) -> list[str]:
        """Candidate RegisterStatusNotifierItem service ids (host-dependent)."""
        ids = [self._bus_name, f"{self._bus_name}/StatusNotifierItem"]
        if self._bus is not None:
            try:
                uniq = self._bus.get_unique_name()
            except Exception:
                uniq = None
            if uniq:
                ids.append(f"{uniq}{self._obj}")
                ids.append(uniq)
        # Preserve order, drop dupes
        out: list[str] = []
        seen: set[str] = set()
        for s in ids:
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def _register_with_watcher(self) -> bool:
        """Register with every known SNI watcher. Returns True if any accepted."""
        if self._bus is None or not self._ok:
            return False
        ok_any = False
        last_err: str | None = None
        watchers = (_WATCHER_NAME, "org.x.StatusNotifierWatcher")
        for watcher in watchers:
            for service in self._service_ids():
                try:
                    self._bus.call_sync(
                        watcher,
                        _WATCHER_PATH,
                        _WATCHER_IFACE,
                        "RegisterStatusNotifierItem",
                        GLib.Variant("(s)", (service,)),
                        None,
                        Gio.DBusCallFlags.NONE,
                        2500,
                        None,
                    )
                    ok_any = True
                    # One successful id per watcher is enough; still try the
                    # other watcher (kde vs xapp names can be distinct).
                    break
                except GLib.Error as exc:
                    last_err = f"{watcher}/{service}: {exc.message}"
                    continue
        if not ok_any and last_err:
            print(
                f"spectre tray: RegisterStatusNotifierItem failed ({last_err})",
                file=sys.stderr,
            )
        return ok_any

    def _retry_register_with_watcher(self) -> bool:
        """GLib timeout callback — do not repeat from this source."""
        self._register_with_watcher()
        return False

    def _on_sni_get(self, *_a) -> GLib.Variant | None:
        name = _a[4] if len(_a) > 4 else _a[-1]
        if name == "Category":
            return GLib.Variant("s", "ApplicationStatus")
        if name == "Id":
            return GLib.Variant("s", "spectre")
        if name == "Title":
            return GLib.Variant("s", APPLICATION_NAME)
        if name == "Status":
            return GLib.Variant("s", self._status)
        if name == "IconName":
            if self._icon_name:
                return GLib.Variant("s", self._icon_name)
            return GLib.Variant("s", self._icon_file or "")
        if name == "IconThemePath":
            return GLib.Variant("s", self._icon_theme_path)
        if name == "IconPixmap":
            return GLib.Variant("a(iiay)", list(self._pixmaps))
        if name in ("AttentionIconName", "OverlayIconName"):
            return GLib.Variant("s", "")
        if name == "ItemIsMenu":
            # False: left-click activates; right-click opens Menu (secondary).
            return GLib.Variant("b", False)
        if name == "Menu":
            return GLib.Variant("o", self._menu_obj)
        if name == "ToolTip":
            return GLib.Variant(
                "(sa(iiay)ss)",
                (
                    self._icon_name or self._icon_file,
                    [],
                    self._tip_title,
                    self._tip_body,
                ),
            )
        return None

    def _on_sni_call(
        self,
        _c: Gio.DBusConnection,
        _s: str,
        _p: str,
        _i: str,
        method: str,
        _params: GLib.Variant,
        inv: Gio.DBusMethodInvocation,
    ) -> None:
        if method in ("Activate", "SecondaryActivate"):
            # Left-click → show window
            if self._on_show:
                GLib.idle_add(self._on_show)
            inv.return_value(None)
            return
        if method == "ContextMenu":
            # Right-click is handled by the panel via dbusmenu; no-op is fine.
            inv.return_value(None)
            return
        if method == "Scroll":
            inv.return_value(None)
            return
        inv.return_error_literal(
            Gio.dbus_error_quark(),
            Gio.DBusError.UNKNOWN_METHOD,
            method,
        )

    # ── dbusmenu ────────────────────────────────────────────────────

    def _menu_items(self) -> list[tuple[int, dict[str, GLib.Variant]]]:
        """Visible menu entries for the current connection state."""
        items: list[tuple[int, dict[str, GLib.Variant]]] = [
            (_MID_SHOW, _menu_props(label="Show Spectre")),
        ]
        if self._path_up or self._connecting:
            items.append((_MID_DISCONNECT, _menu_props(label="Disconnect")))
            items.append(
                (_MID_DISCONNECT_QUIT, _menu_props(label="Disconnect and quit"))
            )
        else:
            items.append((_MID_CONNECT, _menu_props(label="Connect")))
        items.append((_MID_SEP, _menu_props(type="separator")))
        items.append((_MID_QUIT, _menu_props(label="Quit")))
        return items

    def _layout_variant(self) -> GLib.Variant:
        children = [
            GLib.Variant("(ia{sv}av)", (iid, props, []))
            for iid, props in self._menu_items()
        ]
        return GLib.Variant(
            "(ia{sv}av)",
            (0, _menu_props(children_display="submenu"), children),
        )

    def _on_menu_get(self, *_a) -> GLib.Variant | None:
        name = _a[4] if len(_a) > 4 else _a[-1]
        if name == "Version":
            return GLib.Variant("u", 3)
        if name == "TextDirection":
            return GLib.Variant("s", "ltr")
        if name == "Status":
            return GLib.Variant("s", "normal")
        if name == "IconThemePath":
            return GLib.Variant("as", [])
        return None

    def _on_menu_call(
        self,
        _c: Gio.DBusConnection,
        _s: str,
        _p: str,
        _i: str,
        method: str,
        params: GLib.Variant,
        inv: Gio.DBusMethodInvocation,
    ) -> None:
        if method == "GetLayout":
            inv.return_value(
                GLib.Variant.new_tuple(
                    GLib.Variant("u", self._rev),
                    self._layout_variant(),
                )
            )
            return
        if method == "GetGroupProperties":
            want = set(params.get_child_value(0).unpack())
            out = [(iid, props) for iid, props in self._menu_items() if iid in want]
            inv.return_value(GLib.Variant("(a(ia{sv}))", (out,)))
            return
        if method == "GetProperty":
            iid = int(params.get_child_value(0).unpack())
            prop = str(params.get_child_value(1).unpack())
            for i, props in self._menu_items():
                if i == iid and prop in props:
                    inv.return_value(GLib.Variant("(v)", (props[prop],)))
                    return
            inv.return_value(GLib.Variant("(v)", (GLib.Variant("s", ""),)))
            return
        if method == "Event":
            iid = int(params.get_child_value(0).unpack())
            event = str(params.get_child_value(1).unpack())
            if event == "clicked":
                self._click(iid)
            inv.return_value(None)
            return
        if method == "EventGroup":
            events = params.get_child_value(0).unpack()
            for ev in events:
                # (id, eventId, data, timestamp)
                if len(ev) >= 2 and str(ev[1]) == "clicked":
                    self._click(int(ev[0]))
            inv.return_value(GLib.Variant("(ai)", ([],)))
            return
        if method == "AboutToShow":
            # True → host should re-fetch layout (state may have changed)
            inv.return_value(GLib.Variant("(b)", (True,)))
            return
        if method == "AboutToShowGroup":
            inv.return_value(GLib.Variant("(aiai)", ([], [])))
            return
        inv.return_error_literal(
            Gio.dbus_error_quark(),
            Gio.DBusError.UNKNOWN_METHOD,
            method,
        )

    def _click(self, iid: int) -> None:
        if iid == _MID_SHOW and self._on_show:
            GLib.idle_add(self._on_show)
        elif iid == _MID_CONNECT and self._on_connect:
            GLib.idle_add(self._on_connect)
        elif iid == _MID_DISCONNECT and self._on_disconnect:
            GLib.idle_add(self._on_disconnect)
        elif iid == _MID_DISCONNECT_QUIT:
            if self._on_disconnect_quit:
                GLib.idle_add(self._on_disconnect_quit)
            elif self._on_disconnect and self._on_quit:
                def _both() -> None:
                    if self._on_disconnect:
                        self._on_disconnect()
                    if self._on_quit:
                        self._on_quit()

                GLib.idle_add(_both)
        elif iid == _MID_QUIT and self._on_quit:
            GLib.idle_add(self._on_quit)
