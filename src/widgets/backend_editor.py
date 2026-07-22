"""Dialog to create or edit a backend (VPN, REALITY, Tor, Proxy)."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gio, Gtk

from core.backends import (
    BACKEND_KINDS,
    PROXY_PROTOCOLS,
    VPN_PROTOCOLS,
    Backend,
)
from widgets.choice_cards import Choice, ChoiceCards


class BackendEditorDialog(Adw.MessageDialog):
    def __init__(
        self,
        parent: Gtk.Window | None,
        *,
        backend: Backend | None = None,
        default_kind: str = "VPN",
        on_save: Callable[[dict], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        title = "Edit adapter" if backend else "New adapter"
        super().__init__(
            transient_for=parent,
            heading=title,
            body="Configure an adapter hops can use. "
            "Incomplete drafts are allowed — Connect requires complete adapters.",
        )
        self._on_save = on_save
        self._on_error = on_error
        self._backend = backend
        self._kind = backend.kind if backend else default_kind

        self.add_response("cancel", "Cancel")
        self.add_response("save", "Save")
        self.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("save")
        self.set_close_response("cancel")

        self._root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._root.set_margin_top(6)
        self._root.set_size_request(440, -1)

        self._name = Gtk.Entry()
        self._name.set_placeholder_text("Display name")
        self._name.set_text(backend.name if backend else "")
        self._name.connect("changed", self._refresh_save)
        self._name.connect("activate", lambda *_: self.response("save"))
        self._root.append(self._field("Name", self._name))

        kind_icons = {
            "VPN": "network-vpn-symbolic",
            "REALITY": "security-high-symbolic",
            "Tor": "network-workgroup-symbolic",
            "Proxy": "network-server-symbolic",
        }
        kind_subs = {
            "VPN": "WireGuard / OpenVPN underlay",
            "REALITY": "TLS camouflage hop",
            "Tor": "Onion routing",
            "Proxy": "SOCKS or HTTP",
        }
        kind0 = backend.kind if backend else default_kind
        if kind0 not in BACKEND_KINDS:
            kind0 = "VPN"
        self._kind_cards = ChoiceCards(
            [
                Choice(
                    k,
                    k,
                    kind_subs.get(k, ""),
                    kind_icons.get(k, "applications-system-symbolic"),
                )
                for k in BACKEND_KINDS
            ],
            selected=kind0,
            on_changed=self._on_kind_card,
            compact=True,
        )
        self._kind_cards.set_sensitive(backend is None)
        self._root.append(self._field("Kind", self._kind_cards))

        self._enabled = Gtk.CheckButton(label="Enabled")
        self._enabled.set_active(backend.enabled if backend else True)
        self._root.append(self._enabled)

        self._kind_fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._root.append(self._kind_fields)

        self._notes = Gtk.Entry()
        self._notes.set_placeholder_text("Optional notes")
        self._notes.set_text(backend.notes if backend else "")
        self._root.append(self._field("Notes", self._notes))

        self.set_extra_child(self._root)
        self.connect("response", self._on_response)
        self._rebuild_kind_fields()
        self._refresh_save()

    def _field(self, title: str, child: Gtk.Widget) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lab = Gtk.Label(label=title, xalign=0)
        lab.add_css_class("field-label")
        box.append(lab)
        box.append(child)
        return box

    def _refresh_save(self, *_a) -> None:
        self.set_response_enabled("save", bool(self._name.get_text().strip()))

    def _selected_kind(self) -> str:
        kid = getattr(self, "_kind_cards", None)
        if kid is not None and kid.selected_id in BACKEND_KINDS:
            return kid.selected_id
        return self._kind if self._kind in BACKEND_KINDS else "VPN"

    def _on_kind_card(self, kind_id: str) -> None:
        self._kind = kind_id
        self._rebuild_kind_fields()

    def _clear_kind_fields(self) -> None:
        child = self._kind_fields.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._kind_fields.remove(child)
            child = nxt

    def _rebuild_kind_fields(self) -> None:
        self._clear_kind_fields()
        b = self._backend
        kind = self._selected_kind()

        if kind == "VPN":
            self._root.set_size_request(440, -1)
            proto0 = b.vpn_protocol if b and b.vpn_protocol in VPN_PROTOCOLS else "WireGuard"
            self._vpn_proto_cards = ChoiceCards(
                [
                    Choice(p, p, "", "network-wired-symbolic")
                    for p in VPN_PROTOCOLS
                ],
                selected=proto0,
                compact=True,
            )
            self._kind_fields.append(self._field("Protocol", self._vpn_proto_cards))

            self._vpn_provider = Gtk.Entry()
            self._vpn_provider.set_placeholder_text("Provider name (optional)")
            self._vpn_provider.set_text(b.vpn_provider if b else "")
            self._kind_fields.append(self._field("Provider", self._vpn_provider))

            self._vpn_endpoint = Gtk.Entry()
            self._vpn_endpoint.set_placeholder_text("host:port or region")
            self._vpn_endpoint.set_text(b.vpn_endpoint if b else "")
            self._kind_fields.append(self._field("Endpoint / region", self._vpn_endpoint))

            cfg_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self._vpn_config = Gtk.Entry()
            self._vpn_config.set_placeholder_text("Path to WireGuard .conf")
            self._vpn_config.set_hexpand(True)
            self._vpn_config.set_text(b.vpn_config if b else "")
            cfg_row.append(self._vpn_config)
            browse = Gtk.Button(label="Browse…")
            browse.connect("clicked", self._browse_vpn_config)
            cfg_row.append(browse)
            self._kind_fields.append(self._field("Config file", cfg_row))
            hint = Gtk.Label(
                label="WireGuard: core runs wg-quick up on this file.",
                xalign=0,
                wrap=True,
            )
            hint.add_css_class("muted")
            self._kind_fields.append(hint)

        elif kind == "REALITY":
            from widgets.reality_diagram import RealityDiagramEditor
            from widgets.transitions import PANEL_MS, crossfade_stack

            # Keep dialog compact — diagram opens as an in-dialog sub-page
            self._root.set_size_request(440, -1)

            self._reality_editor = RealityDiagramEditor(
                on_changed=self._on_reality_fields_changed,
                show_import=True,
                show_advanced=True,
                layout="row",
            )
            self._reality_editor.import_btn.connect("clicked", self._import_vless)
            if b is not None:
                fp = (
                    (getattr(b, "reality_fingerprint", None) or "chrome").strip()
                    or "chrome"
                )
                self._reality_editor.set_values(
                    server=b.reality_server or "",
                    port=b.reality_port or 443,
                    uuid=getattr(b, "reality_uuid", "") or "",
                    public_key=b.reality_public_key or "",
                    short_id=b.reality_short_id or "",
                    sni=b.reality_sni or "",
                    fingerprint=fp,
                    flow=b.reality_flow or "xtls-rprx-vision",
                    spider_x=getattr(b, "reality_spider_x", "") or "",
                )
            # Shims for _collect / _import_vless
            self._r_link = self._reality_editor.vless_entry
            self._r_server = self._reality_editor.server
            self._r_port = self._reality_editor.port
            self._r_uuid = self._reality_editor.uuid
            self._r_pk = self._reality_editor.public_key
            self._r_sid = self._reality_editor.short_id
            self._r_sni = self._reality_editor.sni
            self._r_fp = self._reality_editor.fingerprint
            self._r_flow = self._reality_editor.flow
            self._r_spx = self._reality_editor.spider_x

            self._reality_stack = crossfade_stack(
                duration_ms=PANEL_MS,
                hhomogeneous=True,
                vhomogeneous=False,
                css_class="reality-editor-stack",
            )
            self._reality_stack.set_vexpand(False)
            self._reality_stack.set_hexpand(True)

            # ── Form page: import + summary + open diagram ───────────
            form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            form.add_css_class("reality-editor-form")

            imp = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            # Reuse editor's vless entry on form via mirror? Keep form-level
            # import that writes into the editor (editor has its own import row).
            self._r_form_link = Gtk.Entry()
            self._r_form_link.set_placeholder_text("Paste vless://… share link")
            self._r_form_link.set_hexpand(True)
            imp.append(self._r_form_link)
            imp_btn = Gtk.Button(label="Import")
            imp_btn.add_css_class("suggested-action")
            imp_btn.connect("clicked", self._import_vless_form)
            imp.append(imp_btn)
            form.append(self._field("Share link", imp))

            open_btn = Gtk.Button()
            open_btn.add_css_class("flat")
            open_btn.add_css_class("ready-summary-card")
            open_btn.add_css_class("reality-summary-card")
            open_btn.set_hexpand(True)
            open_btn.connect("clicked", self._show_reality_diagram)

            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            ic = Gtk.Image.new_from_icon_name("security-high-symbolic")
            ic.set_pixel_size(20)
            ic.set_valign(Gtk.Align.CENTER)
            row.append(ic)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            col.set_hexpand(True)
            t = Gtk.Label(label="Hop diagram", xalign=0)
            t.add_css_class("ready-summary-title")
            col.append(t)
            self._reality_summary_lab = Gtk.Label(
                label=self._reality_editor.summary_line(),
                xalign=0,
                wrap=True,
            )
            self._reality_summary_lab.add_css_class("ready-summary-sub")
            col.append(self._reality_summary_lab)
            row.append(col)
            chev = Gtk.Image.new_from_icon_name("go-next-symbolic")
            chev.set_pixel_size(14)
            chev.set_valign(Gtk.Align.CENTER)
            row.append(chev)
            open_btn.set_child(row)
            form.append(open_btn)

            hint = Gtk.Label(
                label="Open the diagram to set server, keys, and cover SNI. "
                "Requires xray-core on PATH.",
                xalign=0,
                wrap=True,
            )
            hint.add_css_class("muted")
            form.append(hint)

            self._reality_stack.add_named(form, "form")

            # ── Diagram sub-page ─────────────────────────────────────
            diag = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            diag.add_css_class("reality-editor-diagram")

            back_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            back = Gtk.Button()
            back.add_css_class("flat")
            back.add_css_class("circular")
            back.set_icon_name("go-previous-symbolic")
            back.set_tooltip_text("Back")
            back.connect("clicked", self._show_reality_form)
            back_row.append(back)
            back_lab = Gtk.Label(label="Hop diagram", xalign=0)
            back_lab.add_css_class("ready-summary-title")
            back_lab.set_hexpand(True)
            back_lab.set_valign(Gtk.Align.CENTER)
            back_row.append(back_lab)
            done = Gtk.Button(label="Done")
            done.add_css_class("suggested-action")
            done.connect("clicked", self._show_reality_form)
            back_row.append(done)
            diag.append(back_row)

            from widgets.scroll import scrolled_window

            scroll = scrolled_window(
                h_policy=Gtk.PolicyType.NEVER,
                v_policy=Gtk.PolicyType.AUTOMATIC,
                vexpand=False,
            )
            scroll.set_min_content_height(280)
            scroll.set_max_content_height(420)
            scroll.set_propagate_natural_height(True)
            scroll.set_child(self._reality_editor)
            diag.append(scroll)

            self._reality_stack.add_named(diag, "diagram")
            self._reality_stack.set_visible_child_name("form")
            self._kind_fields.append(self._reality_stack)

        elif kind == "Tor":
            self._root.set_size_request(440, -1)
            self._tor_system = Gtk.CheckButton(label="Use system Tor")
            self._tor_system.set_active(b.tor_use_system if b else True)
            self._kind_fields.append(self._tor_system)

            self._tor_host = Gtk.Entry()
            self._tor_host.set_text(b.tor_socks_host if b else "127.0.0.1")
            self._kind_fields.append(self._field("SOCKS host", self._tor_host))

            self._tor_port = Gtk.SpinButton.new_with_range(1, 65535, 1)
            self._tor_port.set_value(b.tor_socks_port if b else 9050)
            self._kind_fields.append(self._field("SOCKS port", self._tor_port))

            self._tor_ctrl = Gtk.SpinButton.new_with_range(0, 65535, 1)
            self._tor_ctrl.set_value(b.tor_control_port if b else 9051)
            self._kind_fields.append(self._field("Control port", self._tor_ctrl))

        elif kind == "Proxy":
            self._root.set_size_request(440, -1)
            self._p_proto = Gtk.DropDown.new_from_strings(list(PROXY_PROTOCOLS))
            if b and b.proxy_protocol in PROXY_PROTOCOLS:
                self._p_proto.set_selected(PROXY_PROTOCOLS.index(b.proxy_protocol))
            self._kind_fields.append(self._field("Protocol", self._p_proto))

            self._p_host = Gtk.Entry()
            self._p_host.set_placeholder_text("hostname or IP")
            self._p_host.set_text(b.proxy_host if b else "")
            self._kind_fields.append(self._field("Host", self._p_host))

            self._p_port = Gtk.SpinButton.new_with_range(1, 65535, 1)
            self._p_port.set_value(b.proxy_port if b else 1080)
            self._kind_fields.append(self._field("Port", self._p_port))

            self._p_user = Gtk.Entry()
            self._p_user.set_text(b.proxy_username if b else "")
            self._kind_fields.append(self._field("Username", self._p_user))

            self._p_pass = Gtk.PasswordEntry()
            self._p_pass.set_show_peek_icon(True)
            self._p_pass.set_text(b.proxy_password if b else "")
            self._kind_fields.append(self._field("Password", self._p_pass))

        else:
            self._root.set_size_request(440, -1)

        # Soft fade-in so kind swaps don't hard-cut after rebuild
        try:
            from widgets.transitions import soft_fade

            soft_fade(self._kind_fields, from_opacity=0.0, to_opacity=1.0)
        except Exception:
            self._kind_fields.set_opacity(1.0)

    def _dropdown_string(self, dd: Gtk.DropDown, fallback: str) -> str:
        model = dd.get_model()
        idx = int(dd.get_selected())
        if model is None or idx < 0:
            return fallback
        item = model.get_item(idx)
        return item.get_string() if item is not None else fallback  # type: ignore[attr-defined]

    def _on_reality_fields_changed(self, *_a) -> None:
        lab = getattr(self, "_reality_summary_lab", None)
        ed = getattr(self, "_reality_editor", None)
        if lab is not None and ed is not None:
            lab.set_text(ed.summary_line())

    def _show_reality_diagram(self, *_a) -> None:
        stack = getattr(self, "_reality_stack", None)
        if stack is not None:
            stack.set_visible_child_name("diagram")

    def _show_reality_form(self, *_a) -> None:
        stack = getattr(self, "_reality_stack", None)
        if stack is not None:
            stack.set_visible_child_name("form")
        self._on_reality_fields_changed()

    def _import_vless_form(self, *_a) -> None:
        """Import from the compact form-page paste field."""
        raw = ""
        form_link = getattr(self, "_r_form_link", None)
        if form_link is not None:
            raw = form_link.get_text().strip()
        if not raw:
            # Fall back to diagram import field
            link = getattr(self, "_r_link", None)
            if link is not None:
                raw = link.get_text().strip()
        if not raw:
            if self._on_error:
                self._on_error("Paste a vless:// link first")
            return
        # Sync into diagram import entry so _import_vless can reuse path
        if getattr(self, "_r_link", None) is not None:
            self._r_link.set_text(raw)
        self._import_vless()

    def _import_vless(self, *_a) -> None:
        from core.vless import parse_vless_uri

        raw = ""
        if getattr(self, "_r_link", None) is not None:
            raw = self._r_link.get_text().strip()
        if not raw and getattr(self, "_r_form_link", None) is not None:
            raw = self._r_form_link.get_text().strip()
        if not raw:
            if self._on_error:
                self._on_error("Paste a vless:// link first")
            return
        try:
            fields = parse_vless_uri(raw)
        except ValueError as exc:
            if self._on_error:
                self._on_error(str(exc))
            return
        if fields.get("name") and not self._name.get_text().strip():
            self._name.set_text(str(fields["name"]))
        editor = getattr(self, "_reality_editor", None)
        if editor is not None:
            editor.set_values(
                server=str(fields.get("reality_server") or ""),
                port=int(fields.get("reality_port") or 443),
                uuid=str(fields.get("reality_uuid") or ""),
                public_key=str(fields.get("reality_public_key") or ""),
                short_id=str(fields.get("reality_short_id") or ""),
                sni=str(fields.get("reality_sni") or ""),
                fingerprint=str(fields.get("reality_fingerprint") or "chrome"),
                flow=str(fields.get("reality_flow") or "xtls-rprx-vision"),
                spider_x=str(fields.get("reality_spider_x") or ""),
            )
            self._on_reality_fields_changed()
        else:
            self._r_server.set_text(str(fields.get("reality_server") or ""))
            if fields.get("reality_port"):
                self._r_port.set_value(int(fields["reality_port"]))
            self._r_uuid.set_text(str(fields.get("reality_uuid") or ""))
            self._r_pk.set_text(str(fields.get("reality_public_key") or ""))
            self._r_sid.set_text(str(fields.get("reality_short_id") or ""))
            self._r_sni.set_text(str(fields.get("reality_sni") or ""))
            self._r_fp.set_text(str(fields.get("reality_fingerprint") or "chrome"))
            self._r_flow.set_text(str(fields.get("reality_flow") or "xtls-rprx-vision"))
            self._r_spx.set_text(str(fields.get("reality_spider_x") or ""))
        if self._on_error:
            self._on_error("Imported REALITY fields onto the diagram")

    def _browse_vpn_config(self, *_a) -> None:
        dialog = Gtk.FileDialog(title="WireGuard config")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        conf = Gtk.FileFilter()
        conf.set_name("WireGuard config")
        conf.add_pattern("*.conf")
        conf.add_pattern("*.wg")
        filters.append(conf)
        anyf = Gtk.FileFilter()
        anyf.set_name("All files")
        anyf.add_pattern("*")
        filters.append(anyf)
        dialog.set_filters(filters)
        dialog.set_default_filter(conf)
        parent = self.get_transient_for()

        def on_open(dlg: Gtk.FileDialog, result) -> None:
            try:
                file = dlg.open_finish(result)
            except Exception:
                return
            if file is None:
                return
            path = file.get_path()
            if path:
                self._vpn_config.set_text(path)

        dialog.open(parent, None, on_open)

    def _collect(self) -> dict | None:
        name = self._name.get_text().strip()
        if not name:
            if self._on_error:
                self._on_error("Backend name is required")
            self._name.grab_focus()
            return None
        kind = self._selected_kind()
        data: dict = {
            "kind": kind,
            "name": name,
            "enabled": self._enabled.get_active(),
            "notes": self._notes.get_text().strip(),
        }
        if kind == "VPN":
            proto = "WireGuard"
            cards = getattr(self, "_vpn_proto_cards", None)
            if cards is not None and cards.selected_id:
                proto = cards.selected_id
            data.update(
                vpn_protocol=proto,
                vpn_provider=self._vpn_provider.get_text().strip(),
                vpn_endpoint=self._vpn_endpoint.get_text().strip(),
                vpn_config=self._vpn_config.get_text().strip(),
            )
        elif kind == "REALITY":
            data.update(
                reality_server=self._r_server.get_text().strip(),
                reality_port=int(self._r_port.get_value()),
                reality_uuid=self._r_uuid.get_text().strip(),
                reality_public_key=self._r_pk.get_text().strip(),
                reality_short_id=self._r_sid.get_text().strip(),
                reality_sni=self._r_sni.get_text().strip(),
                reality_flow=self._r_flow.get_text().strip(),
                reality_fingerprint=self._r_fp.get_text().strip() or "chrome",
                reality_spider_x=self._r_spx.get_text().strip(),
            )
        elif kind == "Tor":
            data.update(
                tor_use_system=self._tor_system.get_active(),
                tor_socks_host=self._tor_host.get_text().strip(),
                tor_socks_port=int(self._tor_port.get_value()),
                tor_control_port=int(self._tor_ctrl.get_value()),
            )
        elif kind == "Proxy":
            data.update(
                proxy_protocol=self._dropdown_string(self._p_proto, "SOCKS5"),
                proxy_host=self._p_host.get_text().strip(),
                proxy_port=int(self._p_port.get_value()),
                proxy_username=self._p_user.get_text().strip(),
                proxy_password=self._p_pass.get_text(),
            )
        return data

    def _on_response(self, _d: Adw.MessageDialog, response: str) -> None:
        if response != "save":
            return
        payload = self._collect()
        if payload is None:
            return
        if self._on_save is not None:
            self._on_save(payload)
