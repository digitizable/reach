"""Paths — master–detail list of path recipes (profiles)."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

from core.path_explain import explain_profile
from core.readiness import profile_readiness
from services import Services
from widgets.chrome import clear_box, page_header, scroll_body
from widgets.path_graph import path_graph
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
        embedded: bool = False,
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
        self._embedded = embedded

        if not embedded:
            new_btn = Gtk.Button()
            new_btn.set_icon_name("list-add-symbolic")
            new_btn.add_css_class("flat")
            new_btn.set_tooltip_text("New path")
            new_btn.connect("clicked", self._on_new)
            self.append(
                page_header(
                    "Paths",
                    subtitle="Recipes of hops. Each hop uses an adapter.",
                    end=new_btn,
                )
            )

        split = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        split.add_css_class("master-detail")
        split.set_hexpand(True)
        split.set_vexpand(True)

        # ── Master (list) ─────────────────────────────────────────
        master = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        master.add_css_class("master-pane")
        master.set_size_request(260, -1)
        master.set_hexpand(False)
        master.set_vexpand(True)

        self._empty = Gtk.Label(
            label="No paths yet.\nCreate one, or open Territories.",
            justify=Gtk.Justification.CENTER,
        )
        self._empty.add_css_class("muted")
        self._empty.set_halign(Gtk.Align.CENTER)
        self._empty.set_margin_top(16)
        master.append(self._empty)

        from widgets.scroll import scrolled_window

        list_scroll = scrolled_window(
            h_policy=Gtk.PolicyType.NEVER,
            v_policy=Gtk.PolicyType.AUTOMATIC,
        )
        self._list = ProfileList(
            on_selected=self._picked,
            on_activate=self._edit_id,
        )
        list_scroll.set_child(self._list)
        master.append(list_scroll)
        split.append(master)

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.add_css_class("master-detail-sep")
        split.append(sep)

        # ── Detail ────────────────────────────────────────────────
        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        detail.add_css_class("detail-pane")
        detail.set_hexpand(True)
        detail.set_vexpand(True)

        from widgets.transitions import PANEL_MS, crossfade_stack

        self._detail_stack = crossfade_stack(
            duration_ms=PANEL_MS,
            hhomogeneous=True,
            vhomogeneous=True,
            css_class="detail-stack",
        )

        self._detail_empty = Gtk.Label(
            label="Select a path, or create one.",
            justify=Gtk.Justification.CENTER,
        )
        self._detail_empty.add_css_class("muted")
        self._detail_empty.set_valign(Gtk.Align.CENTER)
        self._detail_empty.set_vexpand(True)
        self._detail_stack.add_named(self._detail_empty, "empty")

        self._detail_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._detail_body.add_css_class("detail-inner")
        self._detail_body.set_hexpand(True)
        self._detail_body.set_vexpand(True)
        self._detail_body.set_halign(Gtk.Align.CENTER)
        self._detail_body.set_valign(Gtk.Align.CENTER)
        self._detail_body.set_size_request(360, -1)

        self._detail_name = Gtk.Label(label="", xalign=0.5)
        self._detail_name.add_css_class("detail-title")
        self._detail_name.set_halign(Gtk.Align.CENTER)
        self._detail_name.set_justify(Gtk.Justification.CENTER)
        self._detail_name.set_wrap(True)
        self._detail_body.append(self._detail_name)

        self._detail_tag = Gtk.Label(label="", xalign=0.5)
        self._detail_tag.add_css_class("detail-tag")
        self._detail_tag.set_halign(Gtk.Align.CENTER)
        self._detail_body.append(self._detail_tag)

        self._detail_summary = Gtk.Label(label="", xalign=0.5, wrap=True)
        self._detail_summary.add_css_class("muted")
        self._detail_summary.set_halign(Gtk.Align.CENTER)
        self._detail_summary.set_justify(Gtk.Justification.CENTER)
        self._detail_body.append(self._detail_summary)

        path_well = Gtk.Box()
        path_well.add_css_class("detail-path-well")
        path_well.set_halign(Gtk.Align.CENTER)
        self._path_host = Gtk.Box()
        self._path_host.set_halign(Gtk.Align.CENTER)
        path_well.append(self._path_host)
        self._detail_body.append(path_well)

        self._detail_hops = Gtk.Label(label="", xalign=0.5, wrap=True)
        self._detail_hops.add_css_class("detail-meta")
        self._detail_hops.set_halign(Gtk.Align.CENTER)
        self._detail_hops.set_justify(Gtk.Justification.CENTER)
        self._detail_body.append(self._detail_hops)

        self._detail_ready = Gtk.Label(label="", xalign=0.5, wrap=True)
        self._detail_ready.add_css_class("detail-ready")
        self._detail_ready.set_halign(Gtk.Align.CENTER)
        self._detail_ready.set_justify(Gtk.Justification.CENTER)
        self._detail_body.append(self._detail_ready)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        actions.set_halign(Gtk.Align.CENTER)
        actions.set_margin_top(8)
        self._edit_btn = Gtk.Button(label="Edit…")
        self._edit_btn.add_css_class("suggested-action")
        self._edit_btn.set_sensitive(False)
        self._edit_btn.connect("clicked", self._on_edit)
        actions.append(self._edit_btn)
        self._use_btn = Gtk.Button(label="Use on Home")
        self._use_btn.add_css_class("flat")
        self._use_btn.set_sensitive(False)
        self._use_btn.set_tooltip_text("Set as active path for Connect")
        self._use_btn.connect("clicked", self._on_use)
        actions.append(self._use_btn)
        self._del_btn = Gtk.Button(label="Delete")
        self._del_btn.add_css_class("flat")
        self._del_btn.set_sensitive(False)
        self._del_btn.connect("clicked", self._on_delete)
        actions.append(self._del_btn)
        self._detail_body.append(actions)

        self._detail_stack.add_named(scroll_body(self._detail_body, margin=16), "body")
        self._detail_stack.set_visible_child_name("empty")
        detail.append(self._detail_stack)
        split.append(detail)
        self.append(split)

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
        pid = self._list.selected_id() or self._services.config.last_profile_id
        self._show_detail(pid if pid else None)

    def _show_detail(self, profile_id: str | None) -> None:
        profile = self._services.profiles.get(profile_id) if profile_id else None
        has = profile is not None
        stack = getattr(self, "_detail_stack", None)
        if stack is not None:
            stack.set_visible_child_name("body" if has else "empty")
        else:
            self._detail_empty.set_visible(not has)
            self._detail_body.set_visible(has)

        self._edit_btn.set_sensitive(has)
        self._del_btn.set_sensitive(has)
        self._use_btn.set_sensitive(has)
        if not has or profile is None:
            return

        backends = self._services.backends
        explain = explain_profile(profile, backends)
        routing = getattr(self._services.config, "routing_mode", "system") or "system"
        ready = profile_readiness(
            profile,
            backends,
            routing_mode=str(routing),
            live=False,
        )
        self._detail_name.set_text(profile.name)
        tag = "Ready" if ready.ok else "Incomplete"
        if profile.favorite:
            tag = f"★ {tag}"
        self._detail_tag.set_text(tag)
        if ready.ok:
            self._detail_tag.remove_css_class("detail-tag-bad")
            self._detail_tag.add_css_class("detail-tag-ok")
        else:
            self._detail_tag.remove_css_class("detail-tag-ok")
            self._detail_tag.add_css_class("detail-tag-bad")

        summary = (profile.summary or "").strip() or (profile.notes or "").strip()
        self._detail_summary.set_text(summary)
        self._detail_summary.set_visible(bool(summary))

        clear_box(self._path_host)
        self._path_host.append(
            path_graph(
                explain.kinds if explain.hops else [],
                live=False,
                empty="No hops",
                labels=explain.labels or None,
                roles=explain.roles or None,
                sublabels=explain.sublabels or None,
                caption=explain.caption,
            )
        )
        self._detail_hops.set_text(explain.hops_line or "No hops")
        if ready.ok:
            self._detail_ready.set_text("Ready to Connect from Home.")
        else:
            self._detail_ready.set_text(ready.summary)

    def _picked(self, profile_id: str | None) -> None:
        if not profile_id:
            self._show_detail(None)
            return
        prev = self._services.config.last_profile_id
        if self._services.set_active_profile(profile_id) is None:
            return
        self._show_detail(profile_id)
        if self._on_changed is not None:
            self._on_changed()
        if prev != profile_id and self._services.is_path_connected():
            p = self._services.profiles.get(profile_id)
            name = p.name if p else "path"
            self._toast(
                self._services.with_reconnect_hint(f"Active path → {name}")
            )

    def _on_use(self, *_a) -> None:
        pid = self._list.selected_id()
        if not pid:
            return
        self._services.set_active_profile(pid)
        p = self._services.profiles.get(pid)
        self._toast(f"Active path → {p.name if p else 'path'}")
        if self._on_changed:
            self._on_changed()

    def open_new(self) -> None:
        """Public entry for parent hubs (Paths → New path)."""
        self._on_new()

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
        self._toast(
            self._services.with_reconnect_hint(f"Created “{profile.name}”")
        )

    def _edit_id(self, profile_id: str) -> None:
        profile = self._services.profiles.get(profile_id)
        if profile is None:
            return
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
        self._toast(self._services.with_reconnect_hint("Path updated"))

    def _on_delete(self, *_a) -> None:
        pid = self._list.selected_id()
        if not pid:
            return
        profile = self._services.profiles.get(pid)
        name = profile.name if profile else "path"
        dialog = Adw.MessageDialog(
            transient_for=self._parent_window,
            heading="Delete path?",
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
