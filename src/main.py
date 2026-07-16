#!/usr/bin/env python3
"""Spectre Desktop — entry point."""

from __future__ import annotations

import sys

from app_config import APPLICATION_ID, ensure_import_path

ensure_import_path()

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import GLib  # noqa: E402

# Stable process/WM identity for Cinnamon snap/taskbar (not "main.py").
GLib.set_prgname(APPLICATION_ID)

from application import SpectreApplication  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    return SpectreApplication().run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    sys.exit(main())
