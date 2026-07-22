"""Background asset preload so the window can open immediately."""

from __future__ import annotations

import threading
from collections.abc import Callable


def preload_assets(
    *,
    on_done: Callable[[], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> None:
    """Load map land + Mullvad catalog off the GTK thread.

    Order: land geometry → disk map cities (instant paint) → network refresh
    → CLI relay list for dropdowns. UI thread never waits on the public API.
    """

    def worker() -> None:
        err = ""
        try:
            from core.map_geo import warm_map_geo
            from widgets.mullvad_map import land_latlon_drag

            # Landmass + lakes + borders + country shapes (flags/focus)
            warm_map_geo()
            land_latlon_drag()  # strided rings for pan
        except Exception as exc:
            err = f"map geo: {exc}"
        try:
            from core import mullvad as mv

            # Instant markers from disk before any network
            mv.load_map_cities_disk()
            # Refresh map cities from network (writes disk cache)
            try:
                mv.fetch_map_cities(timeout=8.0)
            except Exception as exc:
                # Disk/memory still usable
                if not mv.get_map_cities(allow_network=False):
                    err = (err + "; " if err else "") + f"map cities: {exc}"
            if mv.cli_path():
                # Prefer warm CLI list; avoid wipe-then-reload when already cached
                # (clear still OK on first run). Disk map cities stay independent.
                mv.clear_catalog_cache()
                mv.load_catalog()
            # Prefetch + decode flag textures for server countries (off UI)
            try:
                from core.map_country_flags import warm_flag_surfaces

                codes = {
                    (c.country_code or "").lower()
                    for c in mv.get_map_cities(allow_network=False)
                    if c.country_code
                }
                warm_flag_surfaces(codes, limit=60)
            except Exception:
                pass
        except Exception as exc:
            if err:
                err += f"; catalog: {exc}"
            else:
                err = f"catalog: {exc}"

        # Warm page imports off the UI thread so finish_bootstrap does not
        # hitch while the spinner is still visible.
        try:
            import importlib

            for mod in (
                "pages.home",
                "pages.paths_hub",
                "pages.apps",
                "pages.china_ingress",
                "pages.tools",
                "pages.marketplace",
                "pages.settings",
                "pages.nav",
                "widgets.mullvad_map",
                "widgets.path_graph",
                "core.plugin_host",
            ):
                try:
                    importlib.import_module(mod)
                except Exception:
                    pass
        except Exception as exc:
            err = (err + "; " if err else "") + f"imports: {exc}"

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
