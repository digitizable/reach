"""Simple profile list — name + hop line + readiness tag; optional radio select."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gtk

from core.backends import BackendStore
from core.profiles import Profile
from core.readiness import profile_status_tag


def _text_block(
    profile: Profile,
    *,
    backends: BackendStore | None = None,
) -> Gtk.Widget:
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    content.set_hexpand(True)

    title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    name = Gtk.Label(label=profile.name, xalign=0)
    name.add_css_class("profile-row-name")
    name.set_hexpand(True)
    title_row.append(name)
    if profile.favorite:
        star = Gtk.Label(label="★", xalign=1)
        star.add_css_class("muted")
        title_row.append(star)
    content.append(title_row)

    if backends is not None:
        # Prefer bound backend names when present
        parts: list[str] = []
        for hop in profile.hops:
            b = backends.get(hop.backend_id) if hop.backend_id else None
            parts.append(b.name if b else hop.kind)
        hops_text = " → ".join(parts) if parts else "No hops"
        tag = profile_status_tag(profile, backends)
        hops_text = f"{hops_text} · {tag}"
    else:
        hops_text = profile.hops_line()

    hops = Gtk.Label(label=hops_text, xalign=0)
    hops.add_css_class("profile-row-hops")
    hops.set_hexpand(True)
    content.append(hops)
    return content


class ProfileList(Gtk.Box):
    """
    Flat list of profiles:

        ● Stealth entry
          REALITY → VPN · incomplete
    """

    def __init__(
        self,
        *,
        on_selected: Callable[[str | None], None] | None = None,
        on_activate: Callable[[str], None] | None = None,
        selectable: bool = True,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("profile-list")
        self.set_hexpand(True)
        self._on_selected = on_selected
        self._on_activate = on_activate
        self._selectable = selectable
        self._buttons: dict[str, Gtk.CheckButton] = {}
        self._rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.append(self._rows)

    def selected_id(self) -> str | None:
        for pid, btn in self._buttons.items():
            if btn.get_active():
                return pid
        return None

    def set_profiles(
        self,
        profiles: list[Profile],
        *,
        selected_id: str | None = None,
        backends: BackendStore | None = None,
    ) -> None:
        child = self._rows.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._rows.remove(child)
            child = nxt

        self._buttons.clear()
        ids = [p.id for p in profiles]

        if not profiles:
            return

        pick = selected_id if selected_id in ids else None
        group_lead: Gtk.CheckButton | None = None

        for profile in profiles:
            block = _text_block(profile, backends=backends)
            if self._selectable:
                btn = Gtk.CheckButton()
                btn.add_css_class("profile-row")
                btn.set_child(block)
                if group_lead is None:
                    group_lead = btn
                else:
                    btn.set_group(group_lead)
                if pick is not None and profile.id == pick:
                    btn.set_active(True)
                btn.connect("toggled", self._on_toggled, profile.id)
                # Double-click / activate for edit
                click = Gtk.GestureClick()
                click.set_button(0)
                click.connect("pressed", self._on_pressed, profile.id)
                btn.add_controller(click)
                self._buttons[profile.id] = btn
                self._rows.append(btn)
            else:
                row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                row.add_css_class("profile-row")
                row.add_css_class("profile-row-static")
                block.set_margin_start(4)
                block.set_margin_end(4)
                block.set_margin_top(2)
                block.set_margin_bottom(2)
                row.append(block)
                self._rows.append(row)

    def _on_toggled(self, button: Gtk.CheckButton, profile_id: str) -> None:
        if not button.get_active():
            return
        if self._on_selected is not None:
            self._on_selected(profile_id)

    def _on_pressed(
        self, gesture: Gtk.GestureClick, n_press: int, _x: float, _y: float, profile_id: str
    ) -> None:
        if n_press >= 2 and self._on_activate is not None:
            self._on_activate(profile_id)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
