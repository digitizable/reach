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
        title = "Edit backend" if backend else "New backend"
        super().__init__(
            transient_for=parent,
            heading=title,
            body="Configure an adapter Spectre can use on a path hop. "
            "Incomplete drafts are allowed — Connect requires complete backends.",
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
        self._root.set_size_request(320, -1)

        self._name = Gtk.Entry()
        self._name.set_placeholder_text("Display name")
        self._name.set_text(backend.name if backend else "")
        self._name.connect("changed", self._refresh_save)
        self._name.connect("activate", lambda *_: self.response("save"))
        self._root.append(self._field("Name", self._name))

        self._kind_dd = Gtk.DropDown.new_from_strings(list(BACKEND_KINDS))
        if backend and backend.kind in BACKEND_KINDS:
            self._kind_dd.set_selected(BACKEND_KINDS.index(backend.kind))
        elif default_kind in BACKEND_KINDS:
            self._kind_dd.set_selected(BACKEND_KINDS.index(default_kind))
        self._kind_dd.set_sensitive(backend is None)
        self._kind_dd.connect("notify::selected", self._on_kind)
        self._root.append(self._field("Kind", self._kind_dd))

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
        model = self._kind_dd.get_model()
        idx = int(self._kind_dd.get_selected())
        if model is None or idx < 0:
            return "VPN"
        item = model.get_item(idx)
        return item.get_string() if item is not None else "VPN"  # type: ignore[attr-defined]

    def _on_kind(self, *_a) -> None:
        self._kind = self._selected_kind()
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
            self._vpn_proto = Gtk.DropDown.new_from_strings(list(VPN_PROTOCOLS))
            if b and b.vpn_protocol in VPN_PROTOCOLS:
                self._vpn_proto.set_selected(VPN_PROTOCOLS.index(b.vpn_protocol))
            self._kind_fields.append(self._field("Protocol", self._vpn_proto))

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
            # Import share link
            import_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self._r_link = Gtk.Entry()
            self._r_link.set_placeholder_text("Paste vless://… share link")
            self._r_link.set_hexpand(True)
            import_row.append(self._r_link)
            imp = Gtk.Button(label="Import")
            imp.connect("clicked", self._import_vless)
            import_row.append(imp)
            self._kind_fields.append(self._field("Share link", import_row))

            self._r_server = Gtk.Entry()
            self._r_server.set_placeholder_text("hostname or IP")
            self._r_server.set_text(b.reality_server if b else "")
            self._kind_fields.append(self._field("Server", self._r_server))

            self._r_port = Gtk.SpinButton.new_with_range(1, 65535, 1)
            self._r_port.set_value(b.reality_port if b else 443)
            self._kind_fields.append(self._field("Port", self._r_port))

            self._r_uuid = Gtk.Entry()
            self._r_uuid.set_placeholder_text("VLESS UUID")
            self._r_uuid.set_text(getattr(b, "reality_uuid", "") if b else "")
            self._kind_fields.append(self._field("UUID", self._r_uuid))

            self._r_pk = Gtk.Entry()
            self._r_pk.set_placeholder_text("Public key (pbk)")
            self._r_pk.set_text(b.reality_public_key if b else "")
            self._kind_fields.append(self._field("Public key", self._r_pk))

            self._r_sid = Gtk.Entry()
            self._r_sid.set_placeholder_text("shortId (optional)")
            self._r_sid.set_text(b.reality_short_id if b else "")
            self._kind_fields.append(self._field("Short ID", self._r_sid))

            self._r_sni = Gtk.Entry()
            self._r_sni.set_placeholder_text("SNI / serverName (e.g. www.microsoft.com)")
            self._r_sni.set_text(b.reality_sni if b else "")
            self._kind_fields.append(self._field("SNI", self._r_sni))

            self._r_fp = Gtk.Entry()
            self._r_fp.set_placeholder_text("chrome")
            fp = "chrome"
            if b is not None:
                fp = (getattr(b, "reality_fingerprint", None) or "chrome").strip() or "chrome"
            self._r_fp.set_text(fp)
            self._kind_fields.append(self._field("Fingerprint", self._r_fp))

            self._r_flow = Gtk.Entry()
            self._r_flow.set_text(b.reality_flow if b else "xtls-rprx-vision")
            self._kind_fields.append(self._field("Flow", self._r_flow))

            self._r_spx = Gtk.Entry()
            self._r_spx.set_placeholder_text("spiderX path (optional)")
            self._r_spx.set_text(getattr(b, "reality_spider_x", "") if b else "")
            self._kind_fields.append(self._field("SpiderX", self._r_spx))

            hint = Gtk.Label(
                label="Requires xray-core on PATH (scripts/install-xray.sh).",
                xalign=0,
                wrap=True,
            )
            hint.add_css_class("muted")
            self._kind_fields.append(hint)

        elif kind == "Tor":
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

    def _dropdown_string(self, dd: Gtk.DropDown, fallback: str) -> str:
        model = dd.get_model()
        idx = int(dd.get_selected())
        if model is None or idx < 0:
            return fallback
        item = model.get_item(idx)
        return item.get_string() if item is not None else fallback  # type: ignore[attr-defined]

    def _import_vless(self, *_a) -> None:
        from core.vless import parse_vless_uri

        raw = self._r_link.get_text().strip()
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
            self._on_error("Imported REALITY fields from link")

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
            data.update(
                vpn_protocol=self._dropdown_string(self._vpn_proto, "WireGuard"),
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
