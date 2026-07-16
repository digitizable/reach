"""Home — status, path map, connect/disconnect only."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gtk

from core.client import CoreState
from services import Services
from widgets.chrome import clear_box, fit_body
from widgets.path_graph import path_graph
from widgets.state import kind_from_core


class HomePage(Gtk.Box):
    def __init__(
        self,
        services: Services,
        *,
        on_toast: Callable[[str], None] | None = None,
        on_state_changed: Callable[[], None] | None = None,
        on_navigate: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("page")
        self.add_css_class("home-page")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._services = services
        self._on_toast = on_toast
        self._on_state_changed = on_state_changed
        self._on_navigate = on_navigate

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.add_css_class("home-body")
        body.set_hexpand(True)
        body.set_vexpand(True)
        body.set_valign(Gtk.Align.CENTER)
        body.set_halign(Gtk.Align.CENTER)

        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status_row.add_css_class("home-status-line")
        status_row.set_halign(Gtk.Align.CENTER)

        self._dot = Gtk.Box()
        self._dot.add_css_class("state-dot")
        self._dot.add_css_class("home-status-dot")
        self._dot.set_valign(Gtk.Align.CENTER)
        status_row.append(self._dot)

        self._title = Gtk.Label(label="—")
        self._title.add_css_class("home-status-title")
        status_row.append(self._title)
        body.append(status_row)

        self._detail = Gtk.Label(label="", wrap=True)
        self._detail.add_css_class("home-status-detail")
        self._detail.set_halign(Gtk.Align.CENTER)
        self._detail.set_justify(Gtk.Justification.CENTER)
        self._detail.set_max_width_chars(28)
        body.append(self._detail)

        self._path_host = Gtk.Box()
        self._path_host.set_halign(Gtk.Align.CENTER)
        self._path_host.set_hexpand(False)
        body.append(self._path_host)

        self._profile_label = Gtk.Label(label="", xalign=0.5)
        self._profile_label.add_css_class("home-profile")
        self._profile_label.set_halign(Gtk.Align.CENTER)
        body.append(self._profile_label)

        cta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        cta_row.set_halign(Gtk.Align.CENTER)
        cta_row.set_hexpand(True)

        self._primary = Gtk.Button(label="Connect")
        self._primary.add_css_class("suggested-action")
        self._primary.add_css_class("home-cta")
        self._primary.set_size_request(160, -1)
        self._primary.connect("clicked", self._on_primary)
        cta_row.append(self._primary)
        body.append(cta_row)

        self.append(fit_body(body, margin=12))
        self.refresh()

    def _nav(self, page_id: str) -> None:
        if self._on_navigate is not None:
            self._on_navigate(page_id)

    def _on_primary(self, *_a) -> None:
        st = self._services.core.status()
        if st.state == CoreState.CONNECTED or st.state == CoreState.CONNECTING:
            self._services.disconnect()
            self.refresh()
            if self._on_toast:
                self._on_toast("Disconnected")
            if self._on_state_changed:
                self._on_state_changed()
            return

        status, ready = self._services.connect_active()
        self.refresh()
        if self._on_state_changed:
            self._on_state_changed()

        if not ready.ok:
            if self._on_toast:
                self._on_toast(ready.summary)
            # Guide user to fix bindings / backends
            low = ready.summary.lower()
            if "no profile" in low:
                self._nav("profiles")
            elif "incomplete" in low or "backend" in low or "hop" in low:
                # Incomplete backends → Backends; unbound → Profiles editor
                if "no backend" in low or "unbound" in low:
                    self._nav("profiles")
                else:
                    self._nav("backends")
            elif "profile" in low:
                self._nav("profiles")
            return

        if status is None:
            return
        if self._on_toast:
            if status.state == CoreState.CONNECTED:
                proxy = status.local_proxy
                if proxy:
                    self._on_toast(f"Connected · SOCKS {proxy}")
                else:
                    name = (
                        self._services.active_profile().name
                        if self._services.active_profile()
                        else "profile"
                    )
                    self._on_toast(f"Connected · {name}")
            elif status.state == CoreState.UNAVAILABLE:
                self._on_toast(status.message or "Spectre core is offline")
            elif status.state == CoreState.DISCONNECTED:
                self._on_toast(status.message or "Connect failed")
            else:
                self._on_toast(status.message or status.state.value)

    def refresh(self) -> None:
        st = self._services.core.status()
        kind = kind_from_core(st.state)
        profile = self._services.active_profile()
        ready = self._services.readiness()
        active = st.state == CoreState.CONNECTED

        titles = {
            CoreState.UNAVAILABLE: "Core offline",
            CoreState.DISCONNECTED: "Not connected",
            CoreState.CONNECTING: "Connecting…",
            CoreState.CONNECTED: "Protected",
        }

        if active:
            if st.local_proxy:
                detail = f"Path up · SOCKS {st.local_proxy}"
            else:
                detail = st.message or "Traffic is on the active path."
        elif st.state == CoreState.DISCONNECTED and st.message and st.message not in (
            "Ready",
            "Disconnected",
        ):
            # Show last core error (e.g. connect failure) when useful
            detail = st.message if not ready.ok else st.message
            if not ready.ok:
                detail = ready.summary
        elif not ready.ok:
            detail = ready.summary
        elif st.state == CoreState.UNAVAILABLE:
            detail = "Path configured. Core will start on Connect if installed."
        else:
            detail = {
                CoreState.DISCONNECTED: "Traffic is local until you connect.",
                CoreState.CONNECTING: "Building the path…",
            }.get(st.state, st.message)

        for k in ("offline", "idle", "busy", "live", "unknown", "bad"):
            self._dot.remove_css_class(f"state-{k}")
        self._dot.add_css_class(f"state-{kind.value}")
        self._title.set_text(titles.get(st.state, st.state.value))
        self._detail.set_text(detail)

        # Labels: prefer bound backend names when available
        labels: list[str] = []
        if profile is not None:
            for hop in profile.hops:
                backend = (
                    self._services.backends.get(hop.backend_id)
                    if hop.backend_id
                    else None
                )
                labels.append(backend.name if backend else hop.kind)
        hops_for_icons = profile.hop_kinds() if profile else []

        clear_box(self._path_host)
        self._path_host.append(
            path_graph(
                hops_for_icons,
                live=active,
                empty="Choose a profile",
                labels=labels or None,
            )
        )
        if profile is None:
            self._profile_label.set_text("")
        else:
            tag = "ready" if ready.ok else "incomplete"
            self._profile_label.set_text(f"{profile.name} · {tag}")

        if active or st.state == CoreState.CONNECTING:
            self._primary.set_label("Disconnect")
            self._primary.set_sensitive(True)
            self._primary.remove_css_class("suggested-action")
        else:
            self._primary.set_label("Connect")
            self._primary.set_sensitive(True)
            self._primary.add_css_class("suggested-action")
