"""Centered path diagram — slim icon nodes + arrows."""

from __future__ import annotations

from pathlib import Path

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from app_config import project_root

_ICON_SIZE = 18

# Custom multicolor marks (not recolored by CSS).
_ASSET_LOGOS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    # hop name needles → asset filename, css class
    (("tor",), "tor.svg", "path-node-icon-tor"),
    (("reality", "xray", "xtls", "vless"), "reality.svg", "path-node-icon-reality"),
)


def _icon_for_hop(name: str) -> str:
    key = name.strip().lower()
    # VPN uses a lock so it doesn't collide with Backends (network-server).
    table = {
        "you": "computer-symbolic",
        "device": "computer-symbolic",
        "desktop": "computer-symbolic",
        "vpn": "system-lock-screen-symbolic",
        "wireguard": "system-lock-screen-symbolic",
        "openvpn": "system-lock-screen-symbolic",
        "ikev2": "system-lock-screen-symbolic",
        "shadowsocks": "insert-link-symbolic",
        "hysteria": "network-transmit-receive-symbolic",
        "proxy": "insert-link-symbolic",
    }
    if key in table:
        return table[key]
    for needle, icon in table.items():
        if needle in key:
            return icon
    return "network-transmit-receive-symbolic"


def _asset_for_hop(name: str) -> tuple[Path, str] | None:
    key = name.strip().lower()
    assets = project_root() / "data" / "assets"
    for needles, filename, css in _ASSET_LOGOS:
        if any(n in key for n in needles):
            path = assets / filename
            if path.is_file():
                return path, css
    return None


def _display_scale() -> int:
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


def _image_from_asset(path: Path, css_extra: str) -> Gtk.Image | None:
    """Load SVG/PNG at device pixels for a sharp small mark."""
    scale = _display_scale()
    px = _ICON_SIZE * scale
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), px, px)
        texture = Gdk.Texture.new_for_pixbuf(pb)
        img = Gtk.Image.new_from_paintable(texture)
        img.set_pixel_size(_ICON_SIZE)
        img.set_size_request(_ICON_SIZE, _ICON_SIZE)
        img.add_css_class("path-node-icon")
        img.add_css_class(css_extra)
        img.set_halign(Gtk.Align.CENTER)
        return img
    except GLib.Error:
        return None


def _image_for(title: str, *, kind: str) -> Gtk.Image:
    """Symbolic icon, or project marks for Tor / REALITY (Xray)."""
    if kind != "you":
        asset = _asset_for_hop(title)
        if asset is not None:
            img = _image_from_asset(asset[0], asset[1])
            if img is not None:
                return img

    icon_name = (
        "computer-symbolic" if kind == "you" else _icon_for_hop(title)
    )
    img = Gtk.Image.new_from_icon_name(icon_name)
    img.set_pixel_size(_ICON_SIZE)
    img.add_css_class("path-node-icon")
    img.set_halign(Gtk.Align.CENTER)
    return img


def _node(title: str, *, live: bool = False, kind: str = "hop") -> Gtk.Widget:
    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    col.add_css_class("path-node")
    col.add_css_class(f"path-kind-{kind}")
    if live:
        col.add_css_class("path-live")
    col.set_halign(Gtk.Align.CENTER)
    col.set_valign(Gtk.Align.CENTER)
    col.set_hexpand(False)

    col.append(_image_for(title, kind=kind))

    lab = Gtk.Label(label=title)
    lab.add_css_class("path-node-label")
    lab.set_halign(Gtk.Align.CENTER)
    lab.set_max_width_chars(9)
    col.append(lab)
    return col


def _arrow() -> Gtk.Widget:
    lab = Gtk.Label(label="→")
    lab.add_css_class("path-arrow")
    lab.set_valign(Gtk.Align.CENTER)
    lab.set_halign(Gtk.Align.CENTER)
    lab.set_margin_bottom(12)
    return lab


def path_graph(
    hops: list[str] | tuple[str, ...],
    *,
    live: bool = False,
    empty: str = "No path configured",
    labels: list[str] | None = None,
) -> Gtk.Widget:
    """
    Slim centered chain.

    `hops` are kinds (for icons). Optional `labels` override node text
    (e.g. bound backend names).
    """
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    root.add_css_class("path-diagram")
    root.set_halign(Gtk.Align.CENTER)
    root.set_hexpand(True)

    if not hops:
        empty_l = Gtk.Label(label=empty, xalign=0.5, wrap=True)
        empty_l.add_css_class("path-empty")
        empty_l.set_halign(Gtk.Align.CENTER)
        root.append(empty_l)
        return root

    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    row.add_css_class("path-row")
    row.set_halign(Gtk.Align.CENTER)
    row.set_valign(Gtk.Align.CENTER)
    row.set_hexpand(False)

    row.append(_node("You", live=live, kind="you"))
    for i, hop in enumerate(hops):
        row.append(_arrow())
        if i == 0:
            kind = "entry"
        elif i == len(hops) - 1:
            kind = "exit"
        else:
            kind = "hop"
        # Icon from hop kind; label may be backend name
        label = hop
        if labels is not None and i < len(labels) and labels[i]:
            label = labels[i]
        # Use hop kind for asset/icon matching when label is a backend name
        node = _node(label, live=live, kind=kind)
        # Re-icon from hop kind if label diverged
        if labels is not None and label != hop:
            # rebuild icon via kind-aware title for assets: pass hop for image
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            col.add_css_class("path-node")
            col.add_css_class(f"path-kind-{kind}")
            if live:
                col.add_css_class("path-live")
            col.set_halign(Gtk.Align.CENTER)
            col.append(_image_for(hop, kind=kind))
            lab = Gtk.Label(label=label)
            lab.add_css_class("path-node-label")
            lab.set_halign(Gtk.Align.CENTER)
            lab.set_max_width_chars(10)
            col.append(lab)
            node = col
        row.append(node)

    root.append(row)
    return root
