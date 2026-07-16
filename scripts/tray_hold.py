#!/usr/bin/env python3
"""Hold a tray item for a few seconds so another process can inspect it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from core.client import CoreState
from tray import SpectreTray


def main() -> None:
    app = Gtk.Application(application_id="com.digitizable.spectre-tray-hold")

    def activate(_a: Gtk.Application) -> None:
        tray = SpectreTray()
        assert tray.start()
        tray.update_state(CoreState.DISCONNECTED)
        Path("/tmp/spectre-tray-busname").write_text(tray._bus_name, encoding="utf-8")
        Path("/tmp/spectre-tray-icon").write_text(tray._icon_file or "", encoding="utf-8")

        def stop() -> bool:
            tray.stop()
            _a.quit()
            return False

        GLib.timeout_add(3000, stop)

    app.connect("activate", activate)
    app.run([])


if __name__ == "__main__":
    main()
