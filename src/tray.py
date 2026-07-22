"""System tray applet for Reach (StatusNotifierItem over D-Bus).

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


def _pixbuf_to_pixmap(pb) -> list[tuple[int, int, bytes]]:
    """Convert a GdkPixbuf to StatusNotifier IconPixmap ARGB32 rows."""
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
    return _pixbuf_to_pixmap(pb)


# ── Animated lock frames (color fade + shackle open/close) ─────────

_LOCK_GREEN = (22, 163, 74)  # #16a34a connected
_LOCK_AMBER = (217, 119, 6)  # #d97706 mid
_LOCK_RED = (220, 38, 38)  # #dc2626 disconnected
_KEY_GREEN = (5, 46, 22)
_KEY_RED = (69, 10, 10)

_ANIM_FRAMES = 20
_ANIM_FRAME_MS = 40  # ~800ms total — long enough to read the color fade


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_rgb(
    c0: tuple[int, int, int], c1: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        int(_lerp(c0[0], c1[0], t)),
        int(_lerp(c0[1], c1[1], t)),
        int(_lerp(c0[2], c1[2], t)),
    )


def _ease_in_out(t: float) -> float:
    """Smoothstep for softer color / shackle motion."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _lock_color_at(t: float) -> tuple[int, int, int]:
    """t=0 locked green → t=1 unlocked red, through amber."""
    t = _ease_in_out(t)
    if t < 0.5:
        return _lerp_rgb(_LOCK_GREEN, _LOCK_AMBER, t * 2.0)
    return _lerp_rgb(_LOCK_AMBER, _LOCK_RED, (t - 0.5) * 2.0)


def _keyhole_color_at(t: float) -> tuple[int, int, int]:
    return _lerp_rgb(_KEY_GREEN, _KEY_RED, _ease_in_out(t))


def _rgb_hex(c: tuple[int, int, int]) -> str:
    return f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"


def _lock_frame_svg(t: float) -> str:
    """Basic-shape lock: t=0 closed, t=1 open. Color fades green→amber→red."""
    body = _rgb_hex(_lock_color_at(t))
    key = _rgb_hex(_keyhole_color_at(t))
    # Right shackle end: 10.5 seated (closed) → 7.9 open gap
    te = _ease_in_out(t)
    right_y = _lerp(10.5, 7.9, te)
    if t < 0.02:
        shackle = (
            "M8.2 10.5V7.4c0-2.1 1.7-3.8 3.8-3.8s3.8 1.7 3.8 3.8v3.1"
        )
    else:
        shackle = (
            f"M8.2 10.5V7.4c0-2.1 1.7-3.8 3.8-3.8s3.8 1.7 3.8 3.8"
            f"V{right_y:.2f}"
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">
  <rect x="5" y="10.5" width="14" height="10.5" rx="2.2" fill="{body}"/>
  <path d="{shackle}" fill="none" stroke="{body}" stroke-width="2.1"
        stroke-linecap="round"/>
  <circle cx="12" cy="15.2" r="1.25" fill="{key}"/>
  <rect x="11.35" y="15.5" width="1.3" height="2.8" rx="0.45" fill="{key}"/>
</svg>
"""


def _lock_frame_pixmap(t: float, size: int = 32) -> list[tuple[int, int, bytes]]:
    """Render one lock frame to IconPixmap data."""
    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf, Gio
    except Exception:
        return []

    svg = _lock_frame_svg(t).encode("utf-8")
    pb = None
    try:
        stream = Gio.MemoryInputStream.new_from_data(svg, None)
        pb = GdkPixbuf.Pixbuf.new_from_stream_at_scale(
            stream, size, size, True, None
        )
    except Exception:
        pb = None
    if pb is None:
        # Fallback: temp file (some GdkPixbuf builds need a path for SVG)
        try:
            import tempfile

            with tempfile.NamedTemporaryFile(
                suffix=".svg", delete=False
            ) as tmp:
                tmp.write(svg)
                path = tmp.name
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_size(path, size, size)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        except Exception:
            return []
    return _pixbuf_to_pixmap(pb)


# Pre-rendered animation frames (built lazily once) so the timer never blocks
# on SVG parse mid-transition.
_FRAME_CACHE: list[list[tuple[int, int, bytes]]] | None = None
_FRAME_CACHE_SIZE = 32
_FADE_ICON_PREFIX = "spectre-tray-fade"
_FADE_INSTALLED = False


def _animation_frames(size: int = 32) -> list[list[tuple[int, int, bytes]]]:
    """Return N+1 IconPixmap frames from t=0 (locked green) to t=1 (unlocked red)."""
    global _FRAME_CACHE, _FRAME_CACHE_SIZE
    if _FRAME_CACHE is not None and _FRAME_CACHE_SIZE == size:
        return _FRAME_CACHE
    frames: list[list[tuple[int, int, bytes]]] = []
    n = _ANIM_FRAMES
    for i in range(n + 1):
        t = i / float(n)
        frames.append(_lock_frame_pixmap(t, size=size))
    _FRAME_CACHE = frames
    _FRAME_CACHE_SIZE = size
    return frames


def _fade_icon_name(index: int) -> str:
    return f"{_FADE_ICON_PREFIX}-{index:02d}"


def ensure_fade_icons() -> None:
    """Install green→amber→red lock frames as theme icon names.

    Cinnamon/xapp paints theme IconName far more reliably than IconPixmap
    alone. Cycling named fade frames is what makes the color transition visible.
    """
    global _FADE_INSTALLED
    if _FADE_INSTALLED:
        return
    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf, Gio
    except Exception:
        return

    hicolor = _hicolor_root()
    size = 32
    size_dir = hicolor / f"{size}x{size}" / "status"
    size_dir.mkdir(parents=True, exist_ok=True)
    flat = user_data_dir() / "tray-icons"
    flat.mkdir(parents=True, exist_ok=True)

    n = _ANIM_FRAMES
    wrote = False
    for i in range(n + 1):
        name = _fade_icon_name(i)
        dest = size_dir / f"{name}.png"
        flat_dest = flat / f"{name}.png"
        # Rebuild if missing (cheap after first install)
        if dest.is_file() and flat_dest.is_file():
            continue
        t = i / float(n)
        svg = _lock_frame_svg(t).encode("utf-8")
        pb = None
        try:
            stream = Gio.MemoryInputStream.new_from_data(svg, None)
            pb = GdkPixbuf.Pixbuf.new_from_stream_at_scale(
                stream, size, size, True, None
            )
        except Exception:
            pb = None
        if pb is None:
            continue
        try:
            pb.savev(str(dest), "png", [], [])
            pb.savev(str(flat_dest), "png", [], [])
            wrote = True
        except Exception:
            pass

    if wrote:
        try:
            subprocess.run(
                ["gtk-update-icon-cache", "-f", "-t", str(hicolor)],
                check=False,
                capture_output=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    _FADE_INSTALLED = True


def _fade_index_for_t(t: float) -> int:
    t = max(0.0, min(1.0, t))
    n = _ANIM_FRAMES
    return max(0, min(n, int(round(t * n))))


def tooltip_for(state: CoreState, detail: str = "") -> tuple[str, str]:
    titles = {
        CoreState.CONNECTED: "Reach · Protected",
        CoreState.CONNECTING: "Reach · Connecting…",
        CoreState.DISCONNECTED: "Reach · Not connected",
        CoreState.UNAVAILABLE: "Reach · Core offline",
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


class ReachTray:
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
        # Warm fade frames (theme names + pixmap cache) for a smooth first transition.
        try:
            ensure_fade_icons()
            _animation_frames(32)
        except Exception:
            pass
        self._apply_visual(CoreState.DISCONNECTED, "")
        self._path_up = False
        self._connecting = False
        self._last_core_state: CoreState | None = CoreState.DISCONNECTED
        # Visual lock pose for animation (True = green closed lock shown)
        self._visual_locked = False
        self._icon_anim_id: int | None = None
        self._anim_frame = 0
        self._anim_target: CoreState | None = None
        self._anim_opening = True  # True = locked→unlocked (green→red)
        self._anim_t = 1.0  # current pose along fade (0 locked … 1 unlocked)
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
        self._visual_locked = state == CoreState.CONNECTED
        self._anim_t = 0.0 if self._visual_locked else 1.0

    def _apply_visual_frame(self, t: float, detail: str = "") -> None:
        """Animated frame: t=0 locked green, t=1 unlocked red.

        Uses a unique theme IconName per frame (Cinnamon paints these reliably)
        plus matching IconPixmap as a fallback for hosts that prefer pixmaps.
        """
        t = max(0.0, min(1.0, t))
        self._anim_t = t
        ensure_fade_icons()
        idx = _fade_index_for_t(t)
        self._icon_name = _fade_icon_name(idx)
        # Prefer the installed PNG path as a secondary hint for hosts that
        # resolve absolute files; keep theme name primary.
        flat = user_data_dir() / "tray-icons" / f"{self._icon_name}.png"
        self._icon_file = str(flat) if flat.is_file() else ""
        self._icon_theme_path = str(Path.home() / ".local" / "share" / "icons")
        frames = _animation_frames(32)
        self._pixmaps = (
            list(frames[idx]) if idx < len(frames) and frames[idx] else _lock_frame_pixmap(t, 32)
        )
        # Tooltip follows the blend (mid = connecting copy)
        if t < 0.33:
            tip_state = CoreState.CONNECTED
        elif t > 0.66:
            tip_state = CoreState.DISCONNECTED
        else:
            tip_state = CoreState.CONNECTING
        self._tip_title, self._tip_body = tooltip_for(tip_state, detail)
        self._visual_locked = t < 0.5

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
        prev = getattr(self, "_last_core_state", None)
        self._path_up = state == CoreState.CONNECTED
        self._connecting = state == CoreState.CONNECTING
        self._pending_detail = detail

        # Real connect/disconnect always passes through CONNECTING. The old
        # logic skipped animation whenever CONNECTING was involved, so the
        # icon snapped. Drive the fade from lock pose changes instead.
        self._drive_icon_for_state(prev, state, detail)

        self._last_core_state = state
        # Refresh right-click labels when connect state flips
        if was_up != self._path_up or was_conn != self._connecting:
            self._emit_layout_updated()

    def _drive_icon_for_state(
        self,
        prev: CoreState | None,
        state: CoreState,
        detail: str,
    ) -> None:
        """Pick static icon or lock open/close fade for *state*."""
        if prev is None:
            self._cancel_icon_transition()
            self._apply_visual(state, detail)
            self._emit_icon_signals()
            return

        # Desired end pose: only CONNECTED is locked green.
        # CONNECTING keeps the in-flight fade (or starts one toward the
        # direction implied by the previous state).
        if state == CoreState.CONNECTING:
            # From connected → open (unlock); from anything else → close (lock)
            opening = prev == CoreState.CONNECTED
            provisional = (
                CoreState.DISCONNECTED if opening else CoreState.CONNECTED
            )
            self._ensure_icon_transition(
                provisional, detail, opening=opening
            )
            return

        want_locked = state == CoreState.CONNECTED
        # DISCONNECTED ↔ UNAVAILABLE: same unlocked pose, no re-anim
        if (
            not want_locked
            and not self._visual_locked
            and self._icon_anim_id is None
            and prev != CoreState.CONNECTED
            and prev != CoreState.CONNECTING
        ):
            self._apply_visual(state, detail)
            self._emit_icon_signals()
            return

        opening = not want_locked  # locked→unlocked when leaving connected
        if want_locked == self._visual_locked and self._icon_anim_id is None:
            # Already showing the right static pose
            self._apply_visual(state, detail)
            self._emit_icon_signals()
            return

        self._ensure_icon_transition(state, detail, opening=opening)

    def _emit_icon_signals(self) -> None:
        if not self._ok or self._bus is None:
            return
        for sig in ("NewIcon", "NewToolTip"):
            try:
                self._bus.emit_signal(None, self._obj, _SNI, sig, None)
            except GLib.Error:
                pass
        # Some hosts (xapp) only re-read icons after NewStatus. Nudge without
        # changing Active/Passive so the fade frames actually paint.
        try:
            self._bus.emit_signal(
                None,
                self._obj,
                _SNI,
                "NewStatus",
                GLib.Variant("(s)", (self._status,)),
            )
        except GLib.Error:
            pass

    def _cancel_icon_transition(self) -> None:
        tid = getattr(self, "_icon_anim_id", None)
        if tid is not None:
            try:
                GLib.source_remove(tid)
            except Exception:
                pass
            self._icon_anim_id = None
        self._anim_frame = 0
        self._anim_target = None

    def _ensure_icon_transition(
        self, state: CoreState, detail: str, *, opening: bool
    ) -> None:
        """Start or retarget a multi-frame color fade + shackle open/close."""
        # Already running the right way — just retarget the end state.
        if (
            self._icon_anim_id is not None
            and self._anim_opening == opening
        ):
            self._anim_target = state
            self._anim_detail = detail
            return
        self._start_icon_transition(state, detail, opening=opening)

    def _start_icon_transition(
        self, state: CoreState, detail: str, *, opening: bool
    ) -> None:
        """Multi-frame color fade + shackle open/close (~0.8s).

        *opening* True: locked green → unlocked red (disconnect).
        *opening* False: unlocked red → locked green (connect).
        Continues from the current pose when reversing mid-flight.
        """
        # Capture current pose before cancel clears bookkeeping
        start_t = float(getattr(self, "_anim_t", 0.0 if opening else 1.0))
        if self._icon_anim_id is None:
            # From static: snap start to the end we're leaving
            start_t = 0.0 if opening else 1.0
            if self._visual_locked and opening:
                start_t = 0.0
            elif not self._visual_locked and not opening:
                start_t = 1.0

        self._cancel_icon_transition()
        self._anim_target = state
        self._anim_opening = opening
        self._anim_detail = detail

        end_t = 1.0 if opening else 0.0
        # How far we still need to travel along t
        span = end_t - start_t
        if abs(span) < 1e-6:
            self._apply_visual(state, detail)
            self._emit_icon_signals()
            return

        n = _ANIM_FRAMES
        # Frame count proportional to remaining distance so reverse is snappy
        steps = max(4, int(round(n * abs(span))))
        self._anim_frame = 0

        # First frame immediately from current pose
        self._apply_visual_frame(start_t, detail)
        self._emit_icon_signals()

        def tick() -> bool:
            self._anim_frame += 1
            if self._anim_frame >= steps:
                self._icon_anim_id = None
                target = self._anim_target if self._anim_target is not None else state
                detail_now = getattr(self, "_anim_detail", detail)
                # Only settle on a static named icon when core already matches
                live = getattr(self, "_last_core_state", None)
                if live is not None and live != CoreState.CONNECTING:
                    self._apply_visual(live, detail_now)
                else:
                    self._apply_visual_frame(end_t, detail_now)
                self._emit_icon_signals()
                return False
            p = self._anim_frame / float(steps)
            t = start_t + span * p
            self._apply_visual_frame(t, getattr(self, "_anim_detail", detail))
            self._emit_icon_signals()
            return True

        self._icon_anim_id = GLib.timeout_add(_ANIM_FRAME_MS, tick)

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
            # Theme names (including per-frame fade-NN) paint on Cinnamon/xapp.
            return GLib.Variant("s", self._icon_name or "")
        if name == "IconThemePath":
            return GLib.Variant("s", self._icon_theme_path)
        if name == "IconPixmap":
            return GLib.Variant("a(iiay)", list(self._pixmaps or []))
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
            (_MID_SHOW, _menu_props(label="Show Reach")),
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
