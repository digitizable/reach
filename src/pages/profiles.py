"""Profiles — create, select, edit, delete path recipes."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

from services import Services
from widgets.chrome import fit_body, page_header
from widgets.profile_editor import ProfileEditorDialog
from widgets.profile_list import ProfileList


class ProfilesPage(Gtk.Box):
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

        new_btn = Gtk.Button()
        new_btn.set_icon_name("list-add-symbolic")
        new_btn.add_css_class("flat")
        new_btn.set_tooltip_text("New profile")
        new_btn.connect("clicked", self._on_new)
        self.append(page_header("Profiles", end=new_btn))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.add_css_class("page-body")
        body.set_valign(Gtk.Align.START)
        body.set_vexpand(True)

        self._empty = Gtk.Label(
            label="No profiles yet.\nCreate one to build a Spectre path.",
            justify=Gtk.Justification.CENTER,
        )
        self._empty.add_css_class("muted")
        self._empty.set_halign(Gtk.Align.CENTER)
        self._empty.set_margin_top(24)
        body.append(self._empty)

        self._list = ProfileList(
            on_selected=self._picked,
            on_activate=self._edit_id,
        )
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

        self.append(fit_body(body, margin=12))
        self.reload()

    def set_parent_window(self, window: Gtk.Window | None) -> None:
        self._parent_window = window

    def reload(self) -> None:
        profiles = self._services.profiles.list()
        self._empty.set_visible(len(profiles) == 0)
        self._list.set_visible(len(profiles) > 0)
        self._list.set_profiles(
            profiles,
            selected_id=self._services.config.last_profile_id or None,
            backends=self._services.backends,
        )
        has = self._list.selected_id() is not None
        self._edit_btn.set_sensitive(has)
        self._del_btn.set_sensitive(has)

    def _picked(self, profile_id: str | None) -> None:
        if not profile_id:
            self._edit_btn.set_sensitive(False)
            self._del_btn.set_sensitive(False)
            return
        if self._services.set_active_profile(profile_id) is None:
            return
        self._edit_btn.set_sensitive(True)
        self._del_btn.set_sensitive(True)
        if self._on_changed is not None:
            self._on_changed()

    def _on_new(self, *_a) -> None:
        dialog = ProfileEditorDialog(
            self._parent_window,
            backends=self._services.backends,
            on_save=self._create_from_payload,
            on_error=self._toast,
        )
        dialog.present()

    def _toast(self, msg: str) -> None:
        if self._on_toast:
            self._on_toast(msg)

    def _create_from_payload(self, payload: dict) -> None:
        try:
            profile = self._services.profiles.create(**payload)
        except ValueError as exc:
            self._toast(str(exc))
            return
        self._services.set_active_profile(profile.id)
        self.reload()
        if self._on_changed:
            self._on_changed()
        self._toast(f"Created “{profile.name}”")

    def _edit_id(self, profile_id: str) -> None:
        profile = self._services.profiles.get(profile_id)
        if profile is None:
            return
        # Ensure selection follows double-click
        self._services.set_active_profile(profile_id)
        dialog = ProfileEditorDialog(
            self._parent_window,
            profile=profile,
            backends=self._services.backends,
            on_save=lambda payload: self._update_from_payload(profile.id, payload),
            on_error=self._toast,
        )
        dialog.present()

    def _on_edit(self, *_a) -> None:
        pid = self._list.selected_id()
        if pid:
            self._edit_id(pid)

    def _update_from_payload(self, profile_id: str, payload: dict) -> None:
        try:
            profile = self._services.profiles.update(profile_id, **payload)
        except ValueError as exc:
            self._toast(str(exc))
            return
        if profile is None:
            return
        if self._services.config.last_profile_id == profile_id:
            self._services.core.set_selected_profile(profile.name)
            self._services.save_config()
        self.reload()
        if self._on_changed:
            self._on_changed()
        self._toast("Profile updated")

    def _on_delete(self, *_a) -> None:
        pid = self._list.selected_id()
        if not pid:
            return
        profile = self._services.profiles.get(pid)
        name = profile.name if profile else "profile"
        dialog = Adw.MessageDialog(
            transient_for=self._parent_window,
            heading="Delete profile?",
            body=f"“{name}” will be removed. This cannot be undone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d: Adw.MessageDialog, response: str) -> None:
            if response != "delete":
                return
            if not self._services.profiles.delete(pid):
                return
            if self._services.config.last_profile_id == pid:
                remaining = self._services.profiles.list()
                if remaining:
                    self._services.set_active_profile(remaining[0].id)
                else:
                    self._services.config.last_profile_id = ""
                    self._services.core.set_selected_profile(None)
                    self._services.save_config()
            self.reload()
            if self._on_changed:
                self._on_changed()
            self._toast(f"Deleted “{name}”")

        dialog.connect("response", on_response)
        dialog.present()
