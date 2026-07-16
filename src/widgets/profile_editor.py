"""Dialog to create or edit a path profile with backend-bound hops."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

from core.backends import BackendStore
from core.path_compose import can_append_hop, composition_issues
from core.path_explain import explain_profile
from core.profiles import HOP_KINDS, Hop, Profile


class ProfileEditorDialog(Adw.MessageDialog):
    def __init__(
        self,
        parent: Gtk.Window | None,
        *,
        profile: Profile | None = None,
        backends: BackendStore | None = None,
        on_save: Callable[[dict], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        title = "Edit profile" if profile else "New profile"
        super().__init__(
            transient_for=parent,
            heading=title,
            body="Order hops and bind each to a configured backend.",
        )
        self._on_save = on_save
        self._on_error = on_error
        self._backends = backends or BackendStore()
        self._hops: list[Hop] = (
            [Hop(h.kind, h.backend_id) for h in profile.hops] if profile else []
        )

        self.add_response("cancel", "Cancel")
        self.add_response("save", "Save")
        self.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("save")
        self.set_close_response("cancel")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(8)
        box.set_margin_bottom(4)
        box.set_size_request(320, -1)

        self._name = Gtk.Entry()
        self._name.set_placeholder_text("Name")
        self._name.set_text(profile.name if profile else "")
        self._name.connect("changed", self._refresh_save)
        self._name.connect("activate", lambda *_: self._try_save())
        box.append(self._labeled("Name", self._name))

        self._summary = Gtk.Entry()
        self._summary.set_placeholder_text("Short description")
        self._summary.set_text(profile.summary if profile else "")
        box.append(self._labeled("Summary", self._summary))

        hops_head = Gtk.Label(label="Hops (order matters)", xalign=0)
        hops_head.add_css_class("field-label")
        box.append(hops_head)

        self._hops_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(self._hops_box)

        self._path_hint = Gtk.Label(label="", xalign=0, wrap=True)
        self._path_hint.add_css_class("muted")
        self._path_hint.add_css_class("profile-path-hint")
        self._path_hint.set_visible(False)
        box.append(self._path_hint)

        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._hop_kind = Gtk.DropDown.new_from_strings(list(HOP_KINDS))
        self._hop_kind.set_hexpand(True)
        add_row.append(self._hop_kind)
        add_btn = Gtk.Button(label="Add hop")
        add_btn.connect("clicked", self._on_add_hop)
        add_row.append(add_btn)
        box.append(add_row)

        self._notes = Gtk.Entry()
        self._notes.set_placeholder_text("Optional notes")
        self._notes.set_text(profile.notes if profile else "")
        box.append(self._labeled("Notes", self._notes))

        info_lab = Gtk.Label(label="Dashboard info (ⓘ)", xalign=0)
        info_lab.add_css_class("field-label")
        box.append(info_lab)
        info_hint = Gtk.Label(
            label="Shown on Home when you press info. Leave empty for "
            "built-in text (seed profiles) or “Custom configuration.”",
            xalign=0,
            wrap=True,
        )
        info_hint.add_css_class("muted")
        box.append(info_hint)
        info_scroll = Gtk.ScrolledWindow()
        info_scroll.set_min_content_height(72)
        info_scroll.set_hexpand(True)
        self._info_buf = Gtk.TextBuffer()
        self._info_buf.set_text(profile.info if profile else "")
        info_view = Gtk.TextView(buffer=self._info_buf)
        info_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        info_view.set_top_margin(4)
        info_view.set_bottom_margin(4)
        info_view.set_left_margin(4)
        info_view.set_right_margin(4)
        info_scroll.set_child(info_view)
        box.append(info_scroll)

        self._favorite = Gtk.CheckButton(label="Favorite")
        if profile and profile.favorite:
            self._favorite.set_active(True)
        box.append(self._favorite)

        self.set_extra_child(box)
        self.connect("response", self._on_response)
        self._rebuild_hops()
        self._refresh_save()

    def _labeled(self, title: str, child: Gtk.Widget) -> Gtk.Widget:
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lab = Gtk.Label(label=title, xalign=0)
        lab.add_css_class("field-label")
        wrap.append(lab)
        wrap.append(child)
        return wrap

    def _backend_choices(self, kind: str) -> list[tuple[str, str]]:
        """(id, label) including unbound option."""
        items: list[tuple[str, str]] = [("", "— not bound —")]
        for b in self._backends.list(kind=kind):
            if not b.enabled:
                mark = " (disabled)"
            elif not b.is_configured():
                mark = " (incomplete)"
            else:
                mark = ""
            items.append((b.id, f"{b.name}{mark}"))
        return items

    def _refresh_save(self, *_a) -> None:
        ok = bool(self._name.get_text().strip()) and bool(self._hops)
        self.set_response_enabled("save", ok)

    def _rebuild_hops(self) -> None:
        child = self._hops_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._hops_box.remove(child)
            child = nxt

        if not self._hops:
            empty = Gtk.Label(label="No hops yet — add at least one.", xalign=0)
            empty.add_css_class("muted")
            self._hops_box.append(empty)
            self._update_path_hint()
            self._refresh_save()
            return

        for i, hop in enumerate(self._hops):
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            row.add_css_class("profile-row")
            row.add_css_class("profile-row-static")

            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            idx = Gtk.Label(label=f"{i + 1}. {hop.kind}", xalign=0)
            idx.add_css_class("profile-row-name")
            idx.set_hexpand(True)
            head.append(idx)

            up = Gtk.Button()
            up.set_icon_name("go-up-symbolic")
            up.add_css_class("flat")
            up.set_sensitive(i > 0)
            up.connect("clicked", self._move_hop, i, -1)
            head.append(up)
            down = Gtk.Button()
            down.set_icon_name("go-down-symbolic")
            down.add_css_class("flat")
            down.set_sensitive(i < len(self._hops) - 1)
            down.connect("clicked", self._move_hop, i, 1)
            head.append(down)
            rm = Gtk.Button()
            rm.set_icon_name("user-trash-symbolic")
            rm.add_css_class("flat")
            rm.connect("clicked", self._remove_hop, i)
            head.append(rm)
            row.append(head)

            choices = self._backend_choices(hop.kind)
            labels = [c[1] for c in choices]
            dd = Gtk.DropDown.new_from_strings(labels)
            sel = 0
            for j, (bid, _) in enumerate(choices):
                if bid == hop.backend_id:
                    sel = j
                    break
            dd.set_selected(sel)
            dd.connect("notify::selected", self._on_backend_pick, i, choices)
            row.append(dd)

            if len(choices) == 1:
                hint = Gtk.Label(
                    label=f"No {hop.kind} backends — add one under Backends.",
                    xalign=0,
                    wrap=True,
                )
                hint.add_css_class("muted")
                row.append(hint)

            self._hops_box.append(row)
        self._update_path_hint()
        self._refresh_save()

    def _update_path_hint(self) -> None:
        hint = getattr(self, "_path_hint", None)
        if hint is None:
            return
        if not self._hops:
            hint.set_visible(False)
            hint.set_text("")
            return
        draft = Profile(id="draft", name="draft", hops=list(self._hops))
        issues = composition_issues(draft, self._backends)
        if issues:
            self._update_path_hint_error(issues[0].message)
            return
        explain = explain_profile(draft, self._backends)
        if explain.caption and (explain.rewritten or len(self._hops) > 1):
            hint.remove_css_class("profile-path-hint-error")
            hint.set_text(explain.caption)
            hint.set_visible(True)
        else:
            hint.remove_css_class("profile-path-hint-error")
            hint.set_text("")
            hint.set_visible(False)

    def _update_path_hint_error(self, message: str) -> None:
        hint = getattr(self, "_path_hint", None)
        if hint is None:
            return
        hint.add_css_class("profile-path-hint-error")
        hint.set_text(message)
        hint.set_visible(True)

    def _on_backend_pick(
        self,
        dd: Gtk.DropDown,
        _pspec,
        index: int,
        choices: list[tuple[str, str]],
    ) -> None:
        idx = int(dd.get_selected())
        if 0 <= index < len(self._hops) and 0 <= idx < len(choices):
            self._hops[index].backend_id = choices[idx][0]
            self._update_path_hint()

    def _on_add_hop(self, *_a) -> None:
        model = self._hop_kind.get_model()
        idx = int(self._hop_kind.get_selected())
        if model is None or idx < 0:
            return
        item = model.get_item(idx)
        if item is None:
            return
        kind = item.get_string()  # type: ignore[attr-defined]
        backend_id = ""
        # Prefer first complete enabled backend of this kind
        for b in self._backends.list(kind=kind):
            if b.enabled and b.is_configured():
                backend_id = b.id
                break
        if not backend_id:
            backends = self._backends.list(kind=kind)
            if backends:
                backend_id = backends[0].id
        # Block invalid nestings at add-time when backends are known.
        if backend_id:
            issue = can_append_hop(self._hops, kind, backend_id, self._backends)
            if issue is not None:
                if self._on_error:
                    self._on_error(issue.message)
                else:
                    self._update_path_hint_error(issue.message)
                return
        self._hops.append(Hop(kind=kind, backend_id=backend_id))
        self._rebuild_hops()

    def _remove_hop(self, _btn: Gtk.Button, index: int) -> None:
        if 0 <= index < len(self._hops):
            del self._hops[index]
            self._rebuild_hops()

    def _move_hop(self, _btn: Gtk.Button, index: int, delta: int) -> None:
        j = index + delta
        if 0 <= index < len(self._hops) and 0 <= j < len(self._hops):
            self._hops[index], self._hops[j] = self._hops[j], self._hops[index]
            self._rebuild_hops()

    def _payload(self) -> dict | None:
        name = self._name.get_text().strip()
        if not name:
            if self._on_error:
                self._on_error("Profile name is required")
            self._name.grab_focus()
            return None
        if not self._hops:
            if self._on_error:
                self._on_error("Add at least one hop")
            return None
        draft = Profile(id="draft", name=name, hops=list(self._hops))
        issues = composition_issues(draft, self._backends)
        if issues:
            msg = issues[0].message
            if self._on_error:
                self._on_error(msg)
            self._update_path_hint_error(msg)
            return None
        start = self._info_buf.get_start_iter()
        end = self._info_buf.get_end_iter()
        info = self._info_buf.get_text(start, end, False).strip()
        return {
            "name": name,
            "summary": self._summary.get_text().strip(),
            "hops": [
                {"kind": h.kind, "backend_id": h.backend_id} for h in self._hops
            ],
            "notes": self._notes.get_text().strip(),
            "info": info,
            "favorite": self._favorite.get_active(),
        }

    def _try_save(self) -> None:
        if self._payload() is None:
            return
        self.response("save")

    def _on_response(self, _d: Adw.MessageDialog, response: str) -> None:
        if response != "save":
            return
        payload = self._payload()
        if payload is None:
            return
        if self._on_save is not None:
            self._on_save(payload)
