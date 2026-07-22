"""Visual choice cards — prefer these over dense DropDowns for short option sets.

UX pattern (same idea as Territories mode cards / Readiness sub-pages):
  * Show what something *is* (title + short line + icon), not a menu of strings
  * Keep long lists (many backends, many cities) as search/lists — cards for ≤6 options
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from gi.repository import Gtk


@dataclass(frozen=True)
class Choice:
    id: str
    title: str
    subtitle: str = ""
    icon_name: str = "emblem-default-symbolic"


class ChoiceCards(Gtk.Box):
    """Horizontal/vertical wrap of selectable cards; one active id at a time."""

    def __init__(
        self,
        choices: Sequence[Choice],
        *,
        selected: str | None = None,
        on_changed: Callable[[str], None] | None = None,
        compact: bool = False,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class("choice-cards")
        if compact:
            self.add_css_class("choice-cards-compact")
        self._on_changed = on_changed
        self._choices = list(choices)
        self._buttons: dict[str, Gtk.Button] = {}
        self._selected = selected or (choices[0].id if choices else "")

        flow = Gtk.FlowBox()
        flow.add_css_class("choice-cards-flow")
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_max_children_per_line(2 if not compact else 4)
        flow.set_min_children_per_line(1)
        flow.set_column_spacing(8)
        flow.set_row_spacing(8)
        flow.set_hexpand(True)
        self._flow = flow

        for c in self._choices:
            cell = Gtk.FlowBoxChild()
            cell.set_child(self._make_card(c))
            flow.append(cell)

        self.append(flow)
        self._paint_selected()

    @property
    def selected_id(self) -> str:
        return self._selected

    def set_selected(self, choice_id: str, *, notify: bool = False) -> None:
        if choice_id not in self._buttons:
            return
        self._selected = choice_id
        self._paint_selected()
        if notify and self._on_changed is not None:
            self._on_changed(choice_id)

    def _make_card(self, c: Choice) -> Gtk.Button:
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.add_css_class("choice-card")
        btn.set_hexpand(True)
        btn.connect("clicked", lambda *_: self._pick(c.id))

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_halign(Gtk.Align.FILL)

        ic = Gtk.Image.new_from_icon_name(c.icon_name)
        ic.set_pixel_size(20)
        ic.add_css_class("choice-card-icon")
        ic.set_valign(Gtk.Align.CENTER)
        row.append(ic)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        col.set_hexpand(True)
        t = Gtk.Label(label=c.title, xalign=0)
        t.add_css_class("choice-card-title")
        col.append(t)
        if c.subtitle:
            s = Gtk.Label(label=c.subtitle, xalign=0, wrap=True)
            s.add_css_class("choice-card-sub")
            col.append(s)
        row.append(col)

        btn.set_child(row)
        self._buttons[c.id] = btn
        return btn

    def _pick(self, choice_id: str) -> None:
        if choice_id == self._selected:
            return
        self._selected = choice_id
        self._paint_selected()
        if self._on_changed is not None:
            self._on_changed(choice_id)

    def _paint_selected(self) -> None:
        for cid, btn in self._buttons.items():
            if cid == self._selected:
                btn.add_css_class("choice-card-active")
            else:
                btn.remove_css_class("choice-card-active")
