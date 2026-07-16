"""Home — status, path map, connect/disconnect only."""

from __future__ import annotations

import threading
from collections.abc import Callable

from gi.repository import Adw, GLib, Gtk

from core.client import CoreState, CoreStatus
from core.path_explain import explain_live, explain_profile
from core.path_info import path_info_text
from core.readiness import Readiness, profile_uses_mullvad_app_socks
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
        self._action_busy = False

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

        # Profile name + info (i) button
        profile_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        profile_row.add_css_class("home-profile-row")
        profile_row.set_halign(Gtk.Align.CENTER)
        profile_row.set_valign(Gtk.Align.CENTER)

        self._profile_label = Gtk.Label(label="", xalign=0.5)
        self._profile_label.add_css_class("home-profile")
        self._profile_label.set_halign(Gtk.Align.CENTER)
        profile_row.append(self._profile_label)

        self._info_btn = Gtk.Button()
        self._info_btn.add_css_class("flat")
        self._info_btn.add_css_class("circular")
        self._info_btn.add_css_class("home-info-btn")
        self._info_btn.set_icon_name("help-about-symbolic")
        self._info_btn.set_tooltip_text("What does this path do?")
        self._info_btn.set_valign(Gtk.Align.CENTER)
        self._info_btn.connect("clicked", self._on_info)
        profile_row.append(self._info_btn)
        body.append(profile_row)

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

    def _root_window(self) -> Gtk.Window | None:
        w = self.get_root()
        return w if isinstance(w, Gtk.Window) else None

    def _on_info(self, *_a) -> None:
        st = self._services.core.status()
        profile = self._services.active_profile()
        routing = getattr(self._services.config, "routing_mode", "system") or "system"
        heading, body = path_info_text(
            profile,
            self._services.backends,
            routing_mode=str(routing),
            connected=st.state == CoreState.CONNECTED,
        )
        parent = self._root_window()
        dialog = Adw.MessageDialog(
            transient_for=parent,
            heading=heading,
            body=body,
        )
        dialog.add_response("ok", "Got it")
        if profile is not None:
            dialog.add_response("edit", "Edit…")
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")

        def on_response(_d: Adw.MessageDialog, response: str) -> None:
            if response == "edit" and profile is not None:
                self._edit_profile_info(profile.id)

        dialog.connect("response", on_response)
        dialog.present()

    def _edit_profile_info(self, profile_id: str) -> None:
        from core.path_info import resolve_profile_info

        profile = self._services.profiles.get(profile_id)
        if profile is None:
            return
        parent = self._root_window()
        dialog = Adw.MessageDialog(
            transient_for=parent,
            heading=f"Edit info — {profile.name}",
            body=(
                "This text appears when you press ⓘ on the dashboard. "
                "Clear it to restore the built-in default (seed profiles) "
                "or “Custom configuration.” (your own profiles)."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(160)
        scrolled.set_min_content_width(320)
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        buf = Gtk.TextBuffer()
        # Show effective text for editing (including defaults resolved in).
        buf.set_text(resolve_profile_info(profile))
        view = Gtk.TextView(buffer=buf)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_top_margin(6)
        view.set_bottom_margin(6)
        view.set_left_margin(6)
        view.set_right_margin(6)
        scrolled.set_child(view)
        box.append(scrolled)
        dialog.set_extra_child(box)

        def on_response(_d: Adw.MessageDialog, response: str) -> None:
            if response != "save":
                return
            start = buf.get_start_iter()
            end = buf.get_end_iter()
            text = buf.get_text(start, end, False)
            try:
                self._services.profiles.update(profile_id, info=text)
            except ValueError as exc:
                if self._on_toast:
                    self._on_toast(str(exc))
                return
            if self._on_toast:
                self._on_toast("Path info saved")
            # Re-open info so the user sees the result
            self._on_info()

        dialog.connect("response", on_response)
        dialog.present()

    def _on_primary(self, *_a) -> None:
        # Connect/Disconnect can block for tens of seconds (Mullvad ensure,
        # live readiness, spectred + nft). Never run that on the GTK thread.
        if self._action_busy:
            return
        st = self._services.core.status()
        disconnecting = st.state in (CoreState.CONNECTED, CoreState.CONNECTING)

        self._action_busy = True
        self._primary.set_sensitive(False)
        self._primary.set_label("Disconnecting…" if disconnecting else "Connecting…")
        if self._on_toast:
            self._on_toast(
                "Disconnecting…" if disconnecting else "Connecting…"
            )

        def worker() -> None:
            err: str | None = None
            status: CoreStatus | None = None
            ready: Readiness | None = None
            toast = ""
            try:
                if disconnecting:
                    status, toast = self._services.disconnect()
                else:
                    status, ready = self._services.connect_active()
            except Exception as exc:
                err = str(exc) or repr(exc)

            def done() -> bool:
                self._finish_primary(
                    disconnecting=disconnecting,
                    status=status,
                    ready=ready,
                    toast=toast,
                    err=err,
                )
                return False

            GLib.idle_add(done)

        threading.Thread(
            target=worker,
            name="spectre-home-connect",
            daemon=True,
        ).start()

    def _finish_primary(
        self,
        *,
        disconnecting: bool,
        status: CoreStatus | None,
        ready: Readiness | None,
        toast: str,
        err: str | None,
    ) -> None:
        self._action_busy = False
        self.refresh(force_core=True)
        if self._on_state_changed:
            self._on_state_changed()

        if err is not None:
            if self._on_toast:
                self._on_toast(err)
            return

        if disconnecting:
            if self._on_toast:
                self._on_toast(toast or "Disconnected")
            return

        if ready is not None and not ready.ok:
            if self._on_toast:
                self._on_toast(ready.summary)
            # Guide user to fix bindings / backends / external deps
            low = ready.summary.lower()
            if "no profile" in low:
                self._nav("profiles")
            elif "incomplete" in low or "backend" in low or "hop" in low:
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
                # Non-blocking product warning (e.g. Mullvad apps-only myth)
                if ready is not None and ready.warnings:
                    w0 = ready.warnings[0]
                    self._on_toast(w0[:180] + ("…" if len(w0) > 180 else ""))
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
                # Prefer the exit/rewrite warning over generic Mullvad full-tunnel.
                w0 = ready.warnings[0]
                if "Exit is" in w0 or "exit is" in w0.lower():
                    # Already covered by path caption; keep detail short.
                    pass
                elif "Mullvad" in w0:
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

        # Path roles: who is actually the public exit (not just hop order).
        backends = self._services.backends
        hop_details = getattr(st, "hop_details", None)
        if active and st.hops:
            explain = explain_live(
                list(st.hops),
                hop_details=hop_details if isinstance(hop_details, list) else None,
                profile=profile,
                backends=backends,
            )
        elif profile is not None:
            explain = explain_profile(profile, backends)
        else:
            explain = explain_profile(None, backends)

        # Mullvad CLI status — only when it matters, and never as if it were the exit.
        show_mv = False
        if profile_uses_mullvad_app_socks(profile, backends):
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

        clear_box(self._path_host)
        self._path_host.append(
            path_graph(
                explain.kinds if explain.hops else [],
                live=active,
                empty="Choose a profile",
                labels=explain.labels or None,
                roles=explain.roles or None,
                sublabels=explain.sublabels or None,
                # Always name the public exit when we know it.
                caption=explain.caption,
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
        # Info is always useful (explains empty state too).
        self._info_btn.set_sensitive(True)
        self._info_btn.set_tooltip_text(
            f"What does “{profile.name}” do?"
            if profile is not None
            else "What does this path do?"
        )

        if self._action_busy:
            # Worker owns the CTA label/sensitivity until finish.
            self._primary.set_sensitive(False)
            return

        if active or st.state == CoreState.CONNECTING:
            self._primary.set_label("Disconnect")
            self._primary.set_sensitive(True)
            self._primary.remove_css_class("suggested-action")
        else:
            self._primary.set_label("Connect")
            self._primary.set_sensitive(True)
            self._primary.add_css_class("suggested-action")
