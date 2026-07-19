"""Adapters — master–detail list of VPN / REALITY / Tor / Proxy backends."""

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
        self.add_css_class("master-detail-page")
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
        new_btn.set_tooltip_text("New adapter")
        new_btn.connect("clicked", self._on_new)
        self.append(
            page_header(
                "Adapters",
                subtitle="VPN, Tor, REALITY, proxy — what hops can use.",
                end=new_btn,
            )
        )

        split = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        split.add_css_class("master-detail")
        split.set_hexpand(True)
        split.set_vexpand(True)

        master = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        master.add_css_class("master-pane")
        master.set_size_request(260, -1)
        master.set_hexpand(False)
        master.set_vexpand(True)

        self._empty = Gtk.Label(
            label="No adapters yet.\nAdd a VPN, REALITY, Tor, or proxy.",
            justify=Gtk.Justification.CENTER,
        )
        self._empty.add_css_class("muted")
        self._empty.set_halign(Gtk.Align.CENTER)
        self._empty.set_margin_top(16)
        master.append(self._empty)

        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroll.set_vexpand(True)
        list_scroll.set_hexpand(True)
        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._list.add_css_class("profile-list")
        list_scroll.set_child(self._list)
        master.append(list_scroll)
        split.append(master)

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.add_css_class("master-detail-sep")
        split.append(sep)

        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        detail.add_css_class("detail-pane")
        detail.set_hexpand(True)
        detail.set_vexpand(True)

        self._detail_empty = Gtk.Label(
            label="Select an adapter, or create one.",
            justify=Gtk.Justification.CENTER,
        )
        self._detail_empty.add_css_class("muted")
        self._detail_empty.set_valign(Gtk.Align.CENTER)
        self._detail_empty.set_vexpand(True)
        detail.append(self._detail_empty)

        self._detail_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._detail_body.set_hexpand(True)
        self._detail_body.set_vexpand(True)
        self._detail_body.set_visible(False)

        self._detail_name = Gtk.Label(label="", xalign=0)
        self._detail_name.add_css_class("detail-title")
        self._detail_name.set_wrap(True)
        self._detail_body.append(self._detail_name)

        self._detail_tag = Gtk.Label(label="", xalign=0)
        self._detail_tag.add_css_class("detail-tag")
        self._detail_body.append(self._detail_tag)

        self._detail_kind = Gtk.Label(label="", xalign=0)
        self._detail_kind.add_css_class("detail-meta")
        self._detail_body.append(self._detail_kind)

        self._detail_status = Gtk.Label(label="", xalign=0, wrap=True)
        self._detail_status.add_css_class("muted")
        self._detail_body.append(self._detail_status)

        self._detail_notes = Gtk.Label(label="", xalign=0, wrap=True)
        self._detail_notes.add_css_class("muted")
        self._detail_body.append(self._detail_notes)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_halign(Gtk.Align.START)
        actions.set_margin_top(8)
        self._edit_btn = Gtk.Button(label="Edit…")
        self._edit_btn.add_css_class("suggested-action")
        self._edit_btn.set_sensitive(False)
        self._edit_btn.connect("clicked", self._on_edit)
        actions.append(self._edit_btn)
        self._del_btn = Gtk.Button(label="Delete")
        self._del_btn.add_css_class("flat")
        self._del_btn.set_sensitive(False)
        self._del_btn.connect("clicked", self._on_delete)
        actions.append(self._del_btn)
        self._detail_body.append(actions)

        detail.append(scroll_body(self._detail_body, margin=16))
        split.append(detail)
        self.append(split)
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

        if self._selected_id and self._selected_id not in self._row_buttons:
            self._selected_id = None
        if self._selected_id is None and backends:
            self._selected_id = backends[0].id
            btn = self._row_buttons.get(self._selected_id)
            if btn is not None:
                btn.set_active(True)
        self._show_detail(self._selected_id)

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

    def _show_detail(self, backend_id: str | None) -> None:
        backend = self._services.backends.get(backend_id) if backend_id else None
        has = backend is not None
        self._detail_empty.set_visible(not has)
        self._detail_body.set_visible(has)
        parent = self._detail_body.get_parent()
        while parent is not None and not isinstance(parent, Gtk.ScrolledWindow):
            parent = parent.get_parent()
        if parent is not None:
            parent.set_visible(has)

        self._edit_btn.set_sensitive(has)
        self._del_btn.set_sensitive(has)
        if not has or backend is None:
            return

        self._detail_name.set_text(backend.label())
        complete = backend.is_configured()
        tag = "Complete" if complete else "Incomplete draft"
        if not backend.enabled:
            tag = f"{tag} · disabled"
        self._detail_tag.set_text(tag)
        if complete and backend.enabled:
            self._detail_tag.remove_css_class("detail-tag-bad")
            self._detail_tag.add_css_class("detail-tag-ok")
        else:
            self._detail_tag.remove_css_class("detail-tag-ok")
            self._detail_tag.add_css_class("detail-tag-bad")

        self._detail_kind.set_text(f"{backend.kind}")
        self._detail_status.set_text(backend.status_line())
        notes = (backend.notes or "").strip()
        self._detail_notes.set_text(notes)
        self._detail_notes.set_visible(bool(notes))

    def _on_toggled(self, button: Gtk.CheckButton, backend_id: str) -> None:
        if not button.get_active():
            return
        self._selected_id = backend_id
        self._show_detail(backend_id)

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
        self._toast(self._services.with_reconnect_hint("Adapter updated"))

    def _on_delete(self, *_a) -> None:
        if not self._selected_id:
            return
        backend = self._services.backends.get(self._selected_id)
        name = backend.name if backend else "adapter"
        bid = self._selected_id
        dialog = Adw.MessageDialog(
            transient_for=self._parent_window,
            heading="Delete adapter?",
            body=f"“{name}” will be removed and unbound from any path hops.",
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
