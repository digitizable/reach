"""Background asset preload so the window can open immediately."""

from __future__ import annotations

import threading
from collections.abc import Callable


def preload_assets(
    *,
    on_done: Callable[[], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> None:
    """Load map land + Mullvad catalog off the GTK thread."""

    def worker() -> None:
        err = ""
        try:
            from widgets.mullvad_map import load_land_polygons

            load_land_polygons()
        except Exception as exc:
            err = f"map land: {exc}"
        try:
            from core import mullvad as mv

            if mv.cli_path():
                mv.clear_catalog_cache()
                mv.load_catalog()
        except Exception as exc:
            if err:
                err += f"; catalog: {exc}"
            else:
                err = f"catalog: {exc}"

        def finish() -> bool:
            if err and on_error:
                on_error(err)
            if on_done:
                on_done()
            return False

        try:
            from gi.repository import GLib

            GLib.idle_add(finish)
        except Exception:
            if on_done:
                on_done()

    threading.Thread(target=worker, name="reach-preload", daemon=True).start()
