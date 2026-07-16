"""Adw.Application subclass."""

from __future__ import annotations

from gi.repository import Adw, Gio, GLib, Gtk

from app_config import (
    APPLICATION_ICON,
    APPLICATION_ID,
    APPLICATION_NAME,
    APPLICATION_VERSION,
    GITHUB_URL,
    project_root,
)
from core.client import CoreState
from core.updates import (
    UpdateResult,
    check_for_updates_async,
    should_check_now,
    utc_now_iso,
)
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
        self._update_check_inflight = False

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

        # After first paint: nudge core online (short timeouts, non-fatal).
        GLib.idle_add(self._ensure_core_once)
        if self.services.config.auto_connect:
            GLib.idle_add(self._auto_connect_once)
        # Deferred automatic update check (Settings toggle; default on).
        GLib.timeout_add_seconds(4, self._maybe_auto_update_check)

    def _ensure_core_once(self) -> bool:
        """Try to start spectred without blocking the UI for long."""
        try:
            ok = self.services.core.ensure_running(try_start=True)
            if self._window is not None:
                self._window.refresh_all()
                if not ok:
                    # Quiet: only toast if user enabled auto-connect later
                    pass
        except Exception:
            pass
        return False

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

        updates_a = Gio.SimpleAction.new("check-updates", None)
        updates_a.connect("activate", self._on_check_updates_action)
        self.add_action(updates_a)

    def _maybe_auto_update_check(self) -> bool:
        cfg = self.services.config
        if should_check_now(
            enabled=cfg.check_for_updates,
            last_check_iso=cfg.last_update_check,
            interval_hours=cfg.update_check_interval_hours or 24,
        ):
            self.start_update_check(manual=False)
        return False  # one-shot timeout

    def _on_check_updates_action(self, *_a) -> None:
        self.start_update_check(manual=True)

    def start_update_check(self, *, manual: bool = False) -> None:
        """Background GitHub Releases check; UI feedback via toast/dialog."""
        if self._update_check_inflight:
            if manual and self._window is not None:
                self._window.toast("Update check already running…")
            return
        self._update_check_inflight = True
        if manual and self._window is not None:
            self._window.toast("Checking GitHub for updates…")

        def on_done(result: UpdateResult) -> None:
            GLib.idle_add(self._on_update_result, result, manual)

        check_for_updates_async(on_done, current_version=APPLICATION_VERSION)

    def _on_update_result(self, result: UpdateResult, manual: bool) -> bool:
        self._update_check_inflight = False
        cfg = self.services.config
        cfg.last_update_check = utc_now_iso()
        self.services.save_config()
        if self._window is not None:
            self._window.refresh_update_settings()

        self.services.log(
            f"Update check: {result.summary}",
            level="info" if result.ok else "warn",
        )

        if not result.ok:
            if manual and self._window is not None:
                self._window.toast(result.error or "Update check failed")
            return False

        if not result.update_available:
            if manual and self._window is not None:
                self._window.toast(result.message or "Up to date")
            return False

        # Skip re-prompt if user dismissed this version (auto only)
        dismissed = (cfg.dismissed_update_version or "").strip()
        if (
            not manual
            and dismissed
            and dismissed == (result.latest_version or "").strip()
        ):
            return False

        self._present_update_available(result)
        return False

    def _present_update_available(self, result: UpdateResult) -> None:
        parent = self.get_active_window()
        latest = result.latest_version or result.tag_name
        body = (
            f"Spectre Desktop {latest} is available "
            f"(you have {result.current_version}).\n\n"
            f"Open the GitHub release page to download and install."
        )
        dialog = Adw.MessageDialog(
            transient_for=parent,
            heading="Update available",
            body=body,
        )
        dialog.add_response("later", "Later")
        dialog.add_response("dismiss", "Skip this version")
        dialog.add_response("open", "Open release")
        dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("open")
        dialog.set_close_response("later")

        def on_response(_d: Adw.MessageDialog, response: str) -> None:
            if response == "open":
                url = result.release_url or f"{GITHUB_URL}/releases"
                try:
                    Gio.AppInfo.launch_default_for_uri(url, None)
                except Exception:
                    try:
                        Gtk.show_uri(parent, url, _gdk_current_time())
                    except Exception as exc:
                        if self._window is not None:
                            self._window.toast(f"Could not open browser: {exc}")
                        return
                if self._window is not None:
                    self._window.toast(f"Opening {latest} release…")
            elif response == "dismiss":
                self.services.config.dismissed_update_version = (
                    result.latest_version or ""
                )
                self.services.save_config()
                if self._window is not None:
                    self._window.toast(f"Won’t remind you about {latest}")

        dialog.connect("response", on_response)
        dialog.present()

    def _on_about(self, *_a) -> None:
        body = (
            "Linux desktop frontend for Spectre.\n"
            "Thin client: profiles, backends, and connection control over the "
            "local spectred core (Unix-socket API).\n\n"
            f"Updates: {GITHUB_URL}/releases\n"
            f"{project_root()}"
        )
        website = GITHUB_URL
        if hasattr(Adw, "AboutDialog"):
            dialog = Adw.AboutDialog(
                application_name=APPLICATION_NAME,
                application_icon=APPLICATION_ICON,
                developer_name="digitizable",
                version=APPLICATION_VERSION,
                comments=body,
                copyright="© 2026 digitizable",
                license_type=Gtk.License.GPL_3_0,
                website=website,
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
            website=website,
        )
        dialog.present()


def _gdk_current_time() -> int:
    """GDK_CURRENT_TIME without requiring a full Gdk import at module top."""
    try:
        from gi.repository import Gdk

        return int(Gdk.CURRENT_TIME)
    except Exception:
        return 0

