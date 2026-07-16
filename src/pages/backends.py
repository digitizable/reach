"""Backends — configure VPN / REALITY / Tor / Proxy adapters."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

from core.backends import Backend
from services import Services
from widgets.backend_editor import BackendEditorDialog
from widgets.chrome import clear_box, page_header, scroll_body


class BackendsPage(Gtk.Box):
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
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._services = services
        self._parent_window = parent_window
        self._on_changed = on_changed
        self._on_toast = on_toast
        self._selected_id: str | None = None
        self._row_buttons: dict[str, Gtk.CheckButton] = {}

        new_btn = Gtk.Button()
        new_btn.set_icon_name("list-add-symbolic")
        new_btn.add_css_class("flat")
        new_btn.set_tooltip_text("New backend")
        new_btn.connect("clicked", self._on_new)
        self.append(page_header("Backends", end=new_btn))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.add_css_class("page-body")
        body.set_valign(Gtk.Align.START)

        hint = Gtk.Label(
            label="Backends are the concrete adapters hops bind to "
            "(which VPN, which Tor, which REALITY node).",
            wrap=True,
            xalign=0,
        )
        hint.add_css_class("muted")
        body.append(hint)

        self._empty = Gtk.Label(
            label="No backends yet.\nAdd a VPN, REALITY, Tor, or Proxy adapter.",
            justify=Gtk.Justification.CENTER,
        )
        self._empty.add_css_class("muted")
        self._empty.set_halign(Gtk.Align.CENTER)
        self._empty.set_margin_top(16)
        body.append(self._empty)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._list.add_css_class("profile-list")
        body.append(self._list)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions.set_halign(Gtk.Align.END)
        self._edit_btn = Gtk.Button(label="Edit")
        self._edit_btn.add_css_class("flat")
        self._edit_btn.set_sensitive(False)
        self._edit_btn.connect("clicked", self._on_edit)
        actions.append(self._edit_btn)
        self._del_btn = Gtk.Button(label="Delete")
        self._del_btn.add_css_class("flat")
        self._del_btn.set_sensitive(False)
        self._del_btn.connect("clicked", self._on_delete)
        actions.append(self._del_btn)
        body.append(actions)

        self.append(scroll_body(body, margin=12))
        self.reload()

    def reload(self) -> None:
        backends = self._services.backends.list()
        self._empty.set_visible(len(backends) == 0)
        self._list.set_visible(len(backends) > 0)
        clear_box(self._list)
        self._row_buttons.clear()

        group: Gtk.CheckButton | None = None
        for backend in backends:
            btn = self._make_row(backend, group)
            if group is None:
                group = btn
            else:
                btn.set_group(group)
            if self._selected_id == backend.id:
                btn.set_active(True)
            self._row_buttons[backend.id] = btn
            self._list.append(btn)

        has = self._selected_id is not None and self._selected_id in self._row_buttons
        if not has:
            self._selected_id = None
        self._edit_btn.set_sensitive(has)
        self._del_btn.set_sensitive(has)

    def _make_row(
        self, backend: Backend, group: Gtk.CheckButton | None
    ) -> Gtk.CheckButton:
        btn = Gtk.CheckButton()
        btn.add_css_class("profile-row")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        content.set_hexpand(True)
        title = Gtk.Label(label=backend.label(), xalign=0)
        title.add_css_class("profile-row-name")
        content.append(title)
        meta = f"{backend.kind} · {backend.status_line()}"
        if not backend.enabled:
            meta += " · disabled"
        detail = Gtk.Label(label=meta, xalign=0)
        detail.add_css_class("profile-row-hops")
        content.append(detail)
        btn.set_child(content)
        btn.connect("toggled", self._on_toggled, backend.id)
        click = Gtk.GestureClick()
        click.set_button(0)
        click.connect("pressed", self._on_pressed, backend.id)
        btn.add_controller(click)
        return btn

    def _on_toggled(self, button: Gtk.CheckButton, backend_id: str) -> None:
        if not button.get_active():
            return
        self._selected_id = backend_id
        self._edit_btn.set_sensitive(True)
        self._del_btn.set_sensitive(True)

    def _on_pressed(
        self, gesture: Gtk.GestureClick, n_press: int, _x: float, _y: float, backend_id: str
    ) -> None:
        if n_press >= 2:
            self._selected_id = backend_id
            self._on_edit()
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _toast(self, msg: str) -> None:
        if self._on_toast:
            self._on_toast(msg)

    def _on_new(self, *_a) -> None:
        dialog = BackendEditorDialog(
            self._parent_window,
            default_kind="VPN",
            on_save=self._create,
            on_error=self._toast,
        )
        dialog.present()

    def _create(self, payload: dict) -> None:
        try:
            b = self._services.backends.create(**payload)
        except ValueError as exc:
            self._toast(str(exc))
            return
        # Re-bind lonely hops of this kind if profiles needed one
        self._services.profiles.reconcile_backends(self._services.backends)
        self._selected_id = b.id
        self.reload()
        if self._on_changed:
            self._on_changed()
        status = "complete" if b.is_configured() else "incomplete draft"
        self._toast(
            self._services.with_reconnect_hint(f"Created “{b.name}” ({status})")
        )

    def _on_edit(self, *_a) -> None:
        if not self._selected_id:
            return
        backend = self._services.backends.get(self._selected_id)
        if backend is None:
            return
        dialog = BackendEditorDialog(
            self._parent_window,
            backend=backend,
            on_save=lambda p: self._update(backend.id, p),
            on_error=self._toast,
        )
        dialog.present()

    def _update(self, backend_id: str, payload: dict) -> None:
        try:
            b = self._services.backends.update(backend_id, **payload)
        except ValueError as exc:
            self._toast(str(exc))
            return
        if b is None:
            return
        self._services.profiles.reconcile_backends(self._services.backends)
        self.reload()
        if self._on_changed:
            self._on_changed()
        self._toast(self._services.with_reconnect_hint("Backend updated"))

    def _on_delete(self, *_a) -> None:
        if not self._selected_id:
            return
        backend = self._services.backends.get(self._selected_id)
        name = backend.name if backend else "backend"
        bid = self._selected_id
        dialog = Adw.MessageDialog(
            transient_for=self._parent_window,
            heading="Delete backend?",
            body=f"“{name}” will be removed and unbound from any profile hops.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d: Adw.MessageDialog, response: str) -> None:
            if response != "delete":
                return
            if not self._services.backends.delete(bid):
                return
            cleared = self._services.profiles.unbind_backend(bid)
            self._selected_id = None
            self.reload()
            if self._on_changed:
                self._on_changed()
            extra = f" · unbound {cleared} hop(s)" if cleared else ""
            self._toast(f"Deleted “{name}”{extra}")

        dialog.connect("response", on_response)
        dialog.present()
