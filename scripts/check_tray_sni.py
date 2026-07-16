#!/usr/bin/env python3
"""Verify StatusNotifierItem exposes a theme IconName at /StatusNotifierItem."""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gi.repository import GLib  # noqa: E402

from tray import SpectreTray  # noqa: E402


def _busctl_get(bus_name: str, prop: str) -> str:
    r = subprocess.run(
        [
            "busctl",
            "--user",
            "get-property",
            bus_name,
            "/StatusNotifierItem",
            "org.kde.StatusNotifierItem",
            prop,
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip() or "busctl failed")
    line = r.stdout.strip()
    if line.startswith(("s ", "o ")):
        return line[2:].strip().strip('"')
    return line


def main() -> int:
    tray = SpectreTray()
    if not tray.start():
        print("FAIL start")
        return 1

    bus_name = tray._bus_name
    result: dict[str, object] = {"ok": False}
    loop = GLib.MainLoop()

    def worker() -> None:
        try:
            # Give the bus name a moment to fully register with the watcher.
            import time

            time.sleep(0.5)
            icon = _busctl_get(bus_name, "IconName")
            menu = _busctl_get(bus_name, "Menu")
            print("IconName=", icon)
            print("Menu=", menu)
            result["ok"] = bool(icon) and (
                str(icon).startswith("spectre-tray-")
                or str(icon).endswith(".png")
                or str(icon).startswith("/")
            )
            if menu != "/NO_DBUSMENU":
                print("WARN Menu expected /NO_DBUSMENU")
            r = subprocess.run(
                [
                    "busctl",
                    "--user",
                    "get-property",
                    "org.kde.StatusNotifierWatcher",
                    "/StatusNotifierWatcher",
                    "org.kde.StatusNotifierWatcher",
                    "RegisteredStatusNotifierItems",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            print("Registered=", r.stdout.strip())
            if bus_name in r.stdout and "/StatusNotifierItem" in r.stdout:
                print("registration path form OK")
        except Exception as exc:
            print("check failed:", exc)
        finally:
            GLib.idle_add(lambda: (tray.stop(), loop.quit(), False)[2])

    threading.Thread(target=worker, daemon=True).start()
    loop.run()
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
