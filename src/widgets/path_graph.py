"""Centered path diagram — icon wells + arrows + exit/not-exit roles."""

from __future__ import annotations

from pathlib import Path

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from app_config import project_root

_ICON_SIZE = 22
_WELL_SIZE = 44
_ARROW_WIDTH = 28
_NODE_MIN_WIDTH = 64
_ROW_SPACING = 8

# Custom brand marks (not recolored by CSS).
_ASSET_LOGOS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("tor",), "tor.svg", "path-node-icon-tor"),
    (("reality", "xray", "xtls", "vless"), "reality.svg", "path-node-icon-reality"),
    (("mullvad",), "mullvad.png", "path-node-icon-mullvad"),
)


def _icon_for_hop(name: str) -> str:
    key = name.strip().lower()
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


def _asset_for_hop(*names: str) -> tuple[Path, str] | None:
    assets = project_root() / "data" / "assets"
    key = " ".join(n.strip().lower() for n in names if n and n.strip())
    if not key:
        return None
    for needles, filename, css in _ASSET_LOGOS:
        if any(n in key for n in needles):
            path = assets / filename
            if path.is_file():
                return path, css
            alt = assets / (
                filename.replace(".png", ".svg")
                if filename.endswith(".png")
                else filename.replace(".svg", ".png")
            )
            if alt.is_file():
                return alt, css
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
        img.set_valign(Gtk.Align.CENTER)
        img.set_hexpand(False)
        img.set_vexpand(False)
        return img
    except GLib.Error:
        return None


def _image_for(title: str, *, kind: str, also: str = "") -> Gtk.Image:
    if kind != "you":
        asset = _asset_for_hop(title, also)
        if asset is not None:
            img = _image_from_asset(asset[0], asset[1])
            if img is not None:
                return img

    icon_name = "computer-symbolic" if kind == "you" else _icon_for_hop(title)
    img = Gtk.Image.new_from_icon_name(icon_name)
    img.set_pixel_size(_ICON_SIZE)
    img.set_size_request(_ICON_SIZE, _ICON_SIZE)
    img.add_css_class("path-node-icon")
    img.set_halign(Gtk.Align.CENTER)
    img.set_valign(Gtk.Align.CENTER)
    img.set_hexpand(False)
    img.set_vexpand(False)
    return img


def _icon_well(img: Gtk.Widget) -> Gtk.Widget:
    """Square well; icon dead-centered via CenterBox."""
    well = Gtk.CenterBox()
    well.add_css_class("path-node-well")
    well.set_halign(Gtk.Align.CENTER)
    well.set_valign(Gtk.Align.CENTER)
    well.set_size_request(_WELL_SIZE, _WELL_SIZE)
    well.set_hexpand(False)
    well.set_vexpand(False)
    img.set_halign(Gtk.Align.CENTER)
    img.set_valign(Gtk.Align.CENTER)
    well.set_center_widget(img)
    return well


def _arrow_slot(*, muted: bool = False) -> Gtk.Widget:
    """
    Arrow column matching well height so the glyph centers on the wells,
    not on the full node (well + labels).
    """
    slot = Gtk.CenterBox()
    slot.add_css_class("path-arrow-slot")
    slot.set_size_request(_ARROW_WIDTH, _WELL_SIZE)
    slot.set_halign(Gtk.Align.CENTER)
    slot.set_valign(Gtk.Align.START)  # top-align with wells
    slot.set_hexpand(False)
    slot.set_vexpand(False)

    lab = Gtk.Label(label="→")
    lab.add_css_class("path-arrow")
    if muted:
        lab.add_css_class("path-arrow-muted")
    lab.set_halign(Gtk.Align.CENTER)
    lab.set_valign(Gtk.Align.CENTER)
    lab.set_xalign(0.5)
    lab.set_yalign(0.5)
    slot.set_center_widget(lab)
    return slot


def _hop_node(
    title: str,
    *,
    live: bool = False,
    kind: str = "hop",
    role: str = "hop",
    sublabel: str = "",
    also: str = "",
) -> Gtk.Widget:
    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    col.add_css_class("path-node")
    col.add_css_class(f"path-kind-{kind}")
    col.add_css_class(f"path-role-{role}")
    if live:
        col.add_css_class("path-live")
    if role in ("not-exit", "underlay"):
        col.add_css_class("path-node-muted")
    col.set_halign(Gtk.Align.CENTER)
    col.set_valign(Gtk.Align.START)
    col.set_hexpand(False)
    col.set_size_request(_NODE_MIN_WIDTH, -1)

    img = _image_for(
        title if kind != "you" else "you", kind=kind, also=also or title
    )
    col.append(_icon_well(img))

    lab = Gtk.Label(label=title)
    lab.add_css_class("path-node-label")
    lab.set_halign(Gtk.Align.CENTER)
    lab.set_justify(Gtk.Justification.CENTER)
    lab.set_xalign(0.5)
    try:
        from gi.repository import Pango

        lab.set_ellipsize(Pango.EllipsizeMode.END)
    except Exception:
        pass
    lab.set_max_width_chars(10)
    lab.set_width_chars(8)
    col.append(lab)

    tag = (sublabel or "").strip()
    if not tag and role in ("exit", "not-exit", "underlay", "entry"):
        tag = {
            "exit": "exit",
            "not-exit": "not exit",
            "underlay": "underlay",
            "entry": "entry",
        }.get(role, "")
    if tag:
        sub = Gtk.Label(label=tag)
        sub.add_css_class("path-node-sub")
        if role == "exit":
            sub.add_css_class("path-node-sub-exit")
        elif role in ("not-exit", "underlay"):
            sub.add_css_class("path-node-sub-muted")
        sub.set_halign(Gtk.Align.CENTER)
        sub.set_justify(Gtk.Justification.CENTER)
        sub.set_xalign(0.5)
        col.append(sub)

    return col


def path_graph(
    hops: list[str] | tuple[str, ...],
    *,
    live: bool = False,
    empty: str = "No path configured",
    labels: list[str] | None = None,
    roles: list[str] | None = None,
    sublabels: list[str] | None = None,
    caption: str = "",
) -> Gtk.Widget:
    """
    Centered hop chain: wells and arrows share one top band; labels sit under wells.
    """
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    root.add_css_class("path-diagram")
    root.set_halign(Gtk.Align.CENTER)
    root.set_valign(Gtk.Align.CENTER)
    root.set_hexpand(True)

    if not hops:
        empty_l = Gtk.Label(label=empty, xalign=0.5, wrap=True)
        empty_l.add_css_class("path-empty")
        empty_l.set_halign(Gtk.Align.CENTER)
        empty_l.set_justify(Gtk.Justification.CENTER)
        root.append(empty_l)
        return root

    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=_ROW_SPACING)
    row.add_css_class("path-row")
    row.set_halign(Gtk.Align.CENTER)
    row.set_valign(Gtk.Align.START)
    row.set_hexpand(False)
    row.set_vexpand(False)

    row.append(_hop_node("You", live=live, kind="you", role="you", sublabel=""))

    for i, hop in enumerate(hops):
        role = "hop"
        if roles is not None and i < len(roles) and roles[i]:
            role = roles[i]
        elif i == 0:
            role = "entry"
        elif i == len(hops) - 1:
            role = "exit"

        muted_arrow = role in ("not-exit", "underlay")
        row.append(_arrow_slot(muted=muted_arrow))

        label = hop
        if labels is not None and i < len(labels) and labels[i]:
            label = labels[i]
        sub = ""
        if sublabels is not None and i < len(sublabels):
            sub = sublabels[i] or ""

        vis_kind = role if role in ("exit", "entry", "not-exit", "underlay") else "hop"
        if i == 0 and role == "hop":
            vis_kind = "entry"

        row.append(
            _hop_node(
                label,
                live=live and role not in ("not-exit",),
                kind=vis_kind,
                role=role,
                sublabel=sub,
                also=hop,
            )
        )

    root.append(row)

    if caption and caption.strip():
        cap = Gtk.Label(
            label=caption.strip(),
            wrap=True,
            justify=Gtk.Justification.CENTER,
        )
        cap.add_css_class("path-caption")
        cap.set_halign(Gtk.Align.CENTER)
        cap.set_xalign(0.5)
        cap.set_max_width_chars(40)
        root.append(cap)

    return root
