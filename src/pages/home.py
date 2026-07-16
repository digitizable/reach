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
            _status, toast = self._services.disconnect()
            self.refresh()
            if self._on_toast:
                self._on_toast(toast or "Disconnected")
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
            # Guide user to fix bindings / backends / external deps
            low = ready.summary.lower()
            if "no profile" in low:
                self._nav("profiles")
            elif "mullvad" in low:
                # External app — stay on home with clear toast
                pass
            elif "tor socks" in low or "tor " in low:
                pass
            elif "incomplete" in low or "backend" in low or "hop" in low:
                if "no backend" in low or "unbound" in low:
                    self._nav("profiles")
                else:
                    self._nav("backends")
            elif "profile" in low:
                self._nav("profiles")
            elif "setup-killswitch" in low or "nft helper" in low:
                pass
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
                # Non-blocking product warning (e.g. Mullvad apps-only myth)
                if ready.warnings:
                    self._on_toast(ready.warnings[0][:180] + ("…" if len(ready.warnings[0]) > 180 else ""))
            elif status.state == CoreState.UNAVAILABLE:
                self._on_toast(status.message or "Spectre core is offline")
            elif status.state == CoreState.DISCONNECTED:
                self._on_toast(status.message or "Connect failed")
            else:
                self._on_toast(status.message or status.state.value)

    def refresh(self, *, live: bool = False, force_core: bool = False) -> None:
        """Update home chrome from the live core.

        force_core=True skips the CoreClient status TTL (used by the poller).
        live=True runs connect preflight probes (expensive; only on Connect).
        """
        st = self._services.core.status(force=force_core)
        kind = kind_from_core(st.state)
        profile = self._services.active_profile()
        # Structural readiness for display; live TCP/CLI probes stay off the
        # page-switch path so the sidebar stays responsive.
        active = st.state == CoreState.CONNECTED
        ready = self._services.readiness(live=live and not active)

        titles = {
            CoreState.UNAVAILABLE: "Core offline",
            CoreState.DISCONNECTED: "Not connected",
            CoreState.CONNECTING: "Connecting…",
            CoreState.CONNECTED: "Protected",
        }

        env = st.environment or {}
        whonix = bool(env.get("whonix"))
        whonix_role = str(env.get("whonix_role") or "")

        if active:
            # Prefer core path_summary so CLI/tray connect matches the UI.
            if st.path_summary and st.path_summary not in ("No path", ""):
                detail = st.path_summary
                if st.local_proxy:
                    detail = f"{detail} · SOCKS {st.local_proxy}"
            elif st.local_proxy:
                detail = f"Path up · SOCKS {st.local_proxy}"
            else:
                detail = st.message or "Traffic is on the active path."
            # Routing mode / kill switch status from core
            if getattr(st, "routing_active", None):
                detail += " · system routing"
            elif (getattr(st, "routing_mode", None) or "").lower() == "apps":
                detail += " · apps only"
            if st.kill_switch_active:
                detail += " · kill switch"
            elif st.kill_switch is True and st.kill_switch_active is False:
                if st.kill_switch_detail and "apps-only" not in (
                    st.kill_switch_detail or ""
                ):
                    detail += f" · KS off ({st.kill_switch_detail})"
            if ready.warnings:
                # Keep short on the home line; full text is toasted on connect
                if "Mullvad" in ready.warnings[0]:
                    detail += " · Mullvad full-tunnel"
            if whonix and whonix_role == "workstation":
                detail += " · Whonix"
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
            if whonix:
                detail = "Whonix detected · start spectred, then Connect"
        else:
            detail = {
                CoreState.DISCONNECTED: "Traffic is local until you connect.",
                CoreState.CONNECTING: "Building the path…",
            }.get(st.state, st.message)
            if whonix and whonix_role == "workstation" and st.state == CoreState.DISCONNECTED:
                socks = env.get("tor_socks_host")
                port = env.get("tor_socks_port")
                if socks and port:
                    detail = f"Whonix-Workstation · Gateway Tor {socks}:{port}"

        # Only surface Mullvad status when the *live* path uses it (or the
        # planned profile, when disconnected). Core always reports CLI state.
        from core.readiness import profile_uses_mullvad_app_socks

        show_mv = False
        if active and st.hops:
            show_mv = any("mullvad" in str(h).lower() for h in st.hops)
        elif profile_uses_mullvad_app_socks(profile, self._services.backends):
            show_mv = True
        if show_mv:
            mv = getattr(st, "mullvad", None)
            if isinstance(mv, dict) and mv.get("available") and mv.get("summary"):
                summary = str(mv.get("summary") or "")
                if summary and summary not in detail:
                    detail = f"{detail} · {summary}" if detail else summary

        for k in ("offline", "idle", "busy", "live", "unknown", "bad"):
            self._dot.remove_css_class(f"state-{k}")
        self._dot.add_css_class(f"state-{kind.value}")
        self._title.set_text(titles.get(st.state, st.state.value))
        self._detail.set_text(detail)

        # Path graph: when live, trust the core hop list (CLI/tray may differ
        # from the desktop's selected profile). When idle, show the planned profile.
        labels: list[str] = []
        hops_for_icons: list[str] = []
        if active and st.hops:
            labels = list(st.hops)
            hops_for_icons = list(st.hops)
        elif profile is not None:
            for hop in profile.hops:
                backend = (
                    self._services.backends.get(hop.backend_id)
                    if hop.backend_id
                    else None
                )
                labels.append(backend.name if backend else hop.kind)
            hops_for_icons = profile.hop_kinds()

        clear_box(self._path_host)
        self._path_host.append(
            path_graph(
                hops_for_icons,
                live=active,
                empty="Choose a profile",
                labels=labels or None,
            )
        )
        if active:
            name = (st.active_profile or "").strip() or (
                profile.name if profile is not None else ""
            )
            self._profile_label.set_text(f"{name} · live" if name else "live path")
        elif profile is None:
            self._profile_label.set_text("")
        else:
            tag = "ready" if ready.ok else "incomplete"
            self._profile_label.set_text(f"{profile.name} · {tag} · planned")

        if active or st.state == CoreState.CONNECTING:
            self._primary.set_label("Disconnect")
            self._primary.set_sensitive(True)
            self._primary.remove_css_class("suggested-action")
        else:
            self._primary.set_label("Connect")
            self._primary.set_sensitive(True)
            self._primary.add_css_class("suggested-action")
