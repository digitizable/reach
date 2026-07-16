"""Adw.Application subclass."""

from __future__ import annotations

from gi.repository import Adw, Gio, GLib, Gtk

from app_config import (
    APPLICATION_ICON,
    APPLICATION_ID,
    APPLICATION_NAME,
    APPLICATION_VERSION,
    project_root,
)
from core.client import CoreState
from services import Services
from theme import apply_theme
from window import SpectreWindow


class SpectreApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APPLICATION_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        GLib.set_application_name(APPLICATION_NAME)
        self._window: SpectreWindow | None = None
        self.services = Services.create()

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        apply_theme()
        self._install_actions()

    def do_activate(self) -> None:
        windows = self.get_windows()
        if windows:
            win = windows[0]
            win.present()
            if hasattr(win, "unminimize"):
                try:
                    win.unminimize()
                except Exception:
                    pass
            self._window = win  # type: ignore[assignment]
            return

        self._window = SpectreWindow(self, services=self.services)
        if self.services.config.start_minimized:
            self._window.present()
            self._window.minimize()
        else:
            self._window.present()

        # Desktop-side: attempt auto-connect after first paint if requested.
        if self.services.config.auto_connect:
            GLib.idle_add(self._auto_connect_once)

    def _auto_connect_once(self) -> bool:
        """Fire-and-forget connect after first paint if configured."""
        status, ready = self.services.connect_active()
        if self._window is not None:
            if not ready.ok:
                self._window.toast(f"Auto-connect: {ready.summary}")
            elif status is not None and status.state == CoreState.CONNECTED:
                proxy = status.local_proxy
                self._window.toast(
                    f"Auto-connect: protected" + (f" · {proxy}" if proxy else "")
                )
            elif status is not None and status.state == CoreState.UNAVAILABLE:
                self._window.toast("Auto-connect: Spectre core is offline")
            elif status is not None:
                self._window.toast(f"Auto-connect: {status.message}")
            self._window.refresh_all()
        return False  # do not repeat

    def do_shutdown(self) -> None:
        self.services.log("Application shutdown")
        Adw.Application.do_shutdown(self)

    def _install_actions(self) -> None:
        quit_a = Gio.SimpleAction.new("quit", None)
        quit_a.connect("activate", lambda *_: self.quit())
        self.add_action(quit_a)
        self.set_accels_for_action("app.quit", ["<primary>q"])

        about_a = Gio.SimpleAction.new("about", None)
        about_a.connect("activate", self._on_about)
        self.add_action(about_a)

    def _on_about(self, *_a) -> None:
        body = (
            "Linux desktop frontend for Spectre.\n"
            "Thin client: profiles, backends, and connection control over the "
            "local spectred core (Unix-socket API).\n\n"
            f"{project_root()}"
        )
        if hasattr(Adw, "AboutDialog"):
            dialog = Adw.AboutDialog(
                application_name=APPLICATION_NAME,
                application_icon=APPLICATION_ICON,
                developer_name="digitizable",
                version=APPLICATION_VERSION,
                comments=body,
                copyright="© 2026 digitizable",
                license_type=Gtk.License.GPL_3_0,
            )
            dialog.present(self.get_active_window())
            return
        dialog = Adw.AboutWindow(
            transient_for=self.get_active_window(),
            application_name=APPLICATION_NAME,
            application_icon=APPLICATION_ICON,
            developer_name="digitizable",
            version=APPLICATION_VERSION,
            comments=body,
            copyright="© 2026 digitizable",
            license_type=Gtk.License.GPL_3_0,
        )
        dialog.present()
