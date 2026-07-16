"""Adw.Application subclass."""

from __future__ import annotations

import os

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
from tray import SpectreTray
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
        self._tray: SpectreTray | None = None
        self._tray_timer: int | None = None
        self._quitting = False
        self._force_exit_id: int | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        apply_theme()
        self._install_actions()
        self._setup_tray()

    def do_activate(self) -> None:
        # Remote activations (menu click while already running) and recovery
        # after a partial quit must re-create the tray if it is missing.
        if not self._quitting:
            self._ensure_tray()

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
        if self.services.config.start_minimized or (
            self.services.config.close_to_tray
            and self._tray is not None
            and self._tray.available
            and self.services.config.start_minimized
        ):
            self._window.present()
            if self.services.config.start_minimized:
                self._window.minimize()
        else:
            self._window.present()

        # After first paint: nudge core online (short timeouts, non-fatal).
        GLib.idle_add(self._ensure_core_once)
        if self.services.config.auto_connect:
            GLib.idle_add(self._auto_connect_once)
        # Deferred automatic update check (Settings toggle; default on).
        GLib.timeout_add_seconds(4, self._maybe_auto_update_check)
        self._refresh_tray()

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
        import threading

        def worker() -> None:
            err: str | None = None
            status = None
            ready = None
            try:
                status, ready = self.services.connect_active()
            except Exception as exc:
                err = str(exc) or repr(exc)

            def done() -> bool:
                if self._window is not None:
                    if err is not None:
                        self._window.toast(f"Auto-connect: {err}")
                    elif ready is not None and not ready.ok:
                        self._window.toast(f"Auto-connect: {ready.summary}")
                    elif status is not None and status.state == CoreState.CONNECTED:
                        proxy = status.local_proxy
                        self._window.toast(
                            "Auto-connect: protected"
                            + (f" · {proxy}" if proxy else "")
                        )
                    elif status is not None and status.state == CoreState.UNAVAILABLE:
                        self._window.toast("Auto-connect: Spectre core is offline")
                    elif status is not None:
                        self._window.toast(f"Auto-connect: {status.message}")
                    self._window.refresh_all()
                self._refresh_tray()
                return False

            GLib.idle_add(done)

        threading.Thread(
            target=worker, name="spectre-auto-connect", daemon=True
        ).start()
        return False  # do not repeat the idle callback

    def do_shutdown(self) -> None:
        self._quitting = True
        self.stop_tray()
        self.services.log("Application shutdown")
        Adw.Application.do_shutdown(self)

    def _tray_quit(self) -> None:
        """Quit from the tray menu — drop the panel icon first, then exit."""
        if self._quitting:
            return
        self._quitting = True
        self.services.log("Quit from tray")
        # Remove SNI / bus name before tearing down the app so Cinnamon does
        # not keep a ghost lock after the process is gone or half-quit.
        self.stop_tray()
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None
        # Defer quit one tick so D-Bus unregistration can finish.
        GLib.timeout_add(50, self._finish_quit)

    def _finish_quit(self) -> bool:
        try:
            self.quit()
        except Exception:
            pass
        # GApplication sometimes logs shutdown but never leaves the main loop
        # (half-quit zombies keep com.digitizable.spectre-desktop and block the
        # tray). Force-exit if we are still alive shortly after quit().
        if self._force_exit_id is None:
            self._force_exit_id = GLib.timeout_add(400, self._force_exit)
        return False

    def _force_exit(self) -> bool:
        self._force_exit_id = None
        try:
            self.services.log("Force exit after quit (main loop did not leave)")
        except Exception:
            pass
        # Hard exit: release D-Bus name and stop zombie remotes piling up.
        os._exit(0)

    def _ensure_tray(self) -> None:
        """Start tray when enabled and not currently available."""
        if not self.services.config.tray_enabled:
            return
        if self._tray is not None and self._tray.available:
            return
        # Drop a dead tray object so start() can re-register cleanly.
        if self._tray is not None:
            self.stop_tray()
        self._setup_tray()

    def _setup_tray(self) -> None:
        if not self.services.config.tray_enabled:
            return
        if self._tray is not None and self._tray.available:
            return
        tray = SpectreTray(
            on_show=self._tray_show_window,
            on_connect=self._tray_connect,
            on_disconnect=self._tray_disconnect,
            on_disconnect_quit=self._tray_disconnect_quit,
            on_quit=self._tray_quit,
        )
        if tray.start():
            self._tray = tray
            if self._tray_timer is None:
                self._tray_timer = GLib.timeout_add_seconds(2, self._tray_tick)
            self.services.log("Tray applet started")
            self._refresh_tray()
        else:
            self.services.log("Tray applet unavailable", level="warn")

    def stop_tray(self) -> None:
        """Tear down the tray icon completely (panel should drop it)."""
        if self._tray_timer is not None:
            GLib.source_remove(self._tray_timer)
            self._tray_timer = None
        if self._tray is not None:
            self._tray.stop()
            self._tray = None
            self.services.log("Tray applet stopped")

    def apply_tray_settings(self) -> None:
        """Start/stop tray when Settings → Show tray icon changes."""
        want = bool(self.services.config.tray_enabled)
        have = self._tray is not None and self._tray.available
        if want and not have:
            self._setup_tray()
        elif not want and have:
            self.stop_tray()

    def _tray_tick(self) -> bool:
        self._refresh_tray()
        return True

    def _refresh_tray(self, *, force: bool = True) -> None:
        if self._tray is None or not self._tray.available:
            return
        try:
            # force=True when tray timer fires alone; False when window poll
            # already refreshed (avoids double /v1/status and flicker).
            st = self.services.core.status(force=force)
            if st.state == CoreState.CONNECTED:
                detail = st.path_summary or "Protected"
                if st.local_proxy:
                    detail = (
                        f"{detail} · SOCKS {st.local_proxy}"
                        if st.path_summary
                        else f"SOCKS {st.local_proxy}"
                    )
            else:
                detail = st.message or st.state.value
            self._tray.update_state(st.state, detail)
        except Exception:
            pass

    def _tray_show_window(self) -> None:
        if self._window is None:
            self.activate()
            return
        self._window.present()
        if hasattr(self._window, "unminimize"):
            try:
                self._window.unminimize()
            except Exception:
                pass

    def _tray_connect(self) -> None:
        # Same as Home Connect: never block the GTK main loop.
        if getattr(self, "_tray_net_busy", False):
            return
        self._tray_net_busy = True

        def worker() -> None:
            err: str | None = None
            status = None
            ready = None
            try:
                status, ready = self.services.connect_active()
            except Exception as exc:
                err = str(exc) or repr(exc)

            def done() -> bool:
                self._tray_net_busy = False
                if self._window is not None:
                    if err is not None:
                        self._window.toast(err)
                    elif ready is not None and not ready.ok:
                        self._window.toast(ready.summary)
                    elif status is not None and status.state == CoreState.CONNECTED:
                        self._window.toast("Connected")
                    elif status is not None:
                        self._window.toast(status.message or "Connect failed")
                    self._window.refresh_all()
                self._refresh_tray()
                return False

            GLib.idle_add(done)

        import threading

        threading.Thread(
            target=worker, name="spectre-tray-connect", daemon=True
        ).start()

    def _tray_disconnect(self) -> None:
        if getattr(self, "_tray_net_busy", False):
            return
        self._tray_net_busy = True

        def worker() -> None:
            err: str | None = None
            toast = "Disconnected"
            try:
                _status, toast = self.services.disconnect()
            except Exception as exc:
                err = str(exc) or repr(exc)

            def done() -> bool:
                self._tray_net_busy = False
                if self._window is not None:
                    self._window.toast(err or toast or "Disconnected")
                    self._window.refresh_all()
                self._refresh_tray()
                return False

            GLib.idle_add(done)

        import threading

        threading.Thread(
            target=worker, name="spectre-tray-disconnect", daemon=True
        ).start()

    def _tray_disconnect_quit(self) -> None:
        """Disconnect the path, then quit the desktop (tray + window)."""

        def worker() -> None:
            try:
                self.services.disconnect()
            except Exception:
                pass

            def done() -> bool:
                self._tray_quit()
                return False

            GLib.idle_add(done)

        import threading

        threading.Thread(
            target=worker, name="spectre-tray-disconnect-quit", daemon=True
        ).start()

    def should_close_to_tray(self) -> bool:
        return bool(
            self.services.config.close_to_tray
            and self._tray is not None
            and self._tray.available
        )

    def _install_actions(self) -> None:
        quit_a = Gio.SimpleAction.new("quit", None)
        # Same path as tray Quit — always tear down the panel icon first.
        quit_a.connect("activate", lambda *_: self._tray_quit())
        self.add_action(quit_a)
        self.set_accels_for_action("app.quit", ["<primary>q"])

        about_a = Gio.SimpleAction.new("about", None)
        about_a.connect("activate", self._on_about)
        self.add_action(about_a)

        updates_a = Gio.SimpleAction.new("check-updates", None)
        updates_a.connect("activate", self._on_check_updates_action)
        self.add_action(updates_a)

        show_a = Gio.SimpleAction.new("show-window", None)
        show_a.connect("activate", lambda *_: self._tray_show_window())
        self.add_action(show_a)

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

