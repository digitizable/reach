"""Load installed plugin UI pages into Reach (out-of-process-friendly API).

Plugins export::

    def create_page(ctx: PluginContext) -> Gtk.Widget:
        ...

``PluginContext`` is a small façade — no unrestricted import of Reach internals
beyond what we pass here.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.plugin_manifest import PluginManifest
from core.plugin_store import InstalledPlugin, list_installed


@dataclass
class PluginContext:
    """Facade handed to plugin ``create_page``."""

    plugin_id: str
    plugin_dir: Path
    manifest: PluginManifest
    # services is the Reach Services object (read/write path state carefully)
    services: Any
    toast: Callable[[str], None] | None = None
    navigate: Callable[[str], None] | None = None

    def data_path(self, *parts: str) -> Path:
        """Writable data dir for this plugin under user data."""
        from app_config import user_data_dir

        p = Path(user_data_dir()) / "plugin-data" / self.plugin_id.replace(".", "__")
        p.mkdir(parents=True, exist_ok=True)
        return p.joinpath(*parts) if parts else p

    def sensitive_ops_allowed(self) -> bool:
        """True if Operate/agent work is allowed (path connected or policy opt-out)."""
        svc = self.services
        if svc is not None and hasattr(svc, "sensitive_ops_allowed"):
            return bool(svc.sensitive_ops_allowed())
        return True

    def ensure_sensitive_ops(self, *, toast_if_blocked: bool = True) -> bool:
        """Return False when sensitive ops are gated; optionally toast the reason."""
        if self.sensitive_ops_allowed():
            return True
        if toast_if_blocked and self.toast is not None:
            msg = ""
            svc = self.services
            if svc is not None and hasattr(svc, "sensitive_ops_block_message"):
                msg = str(svc.sensitive_ops_block_message() or "")
            self.toast(
                msg
                or "Connect a path before using this plugin"
            )
        return False


def _load_module(manifest: PluginManifest, root: Path):
    mod_name = manifest.entry.module.replace("/", ".").replace("\\", ".")
    # Support "ui" -> ui.py or "pkg.ui"
    parts = mod_name.split(".")
    if len(parts) == 1:
        file_path = root / f"{parts[0]}.py"
        package_init = root / parts[0] / "__init__.py"
        if file_path.is_file():
            spec = importlib.util.spec_from_file_location(
                f"reach_plugin_{manifest.id.replace('.', '_')}",
                file_path,
            )
        elif package_init.is_file():
            # Add root to path and import package
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            return importlib.import_module(parts[0])
        else:
            raise FileNotFoundError(f"module file not found: {file_path}")
    else:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        return importlib.import_module(mod_name)

    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def create_plugin_page(
    inst: InstalledPlugin,
    *,
    services: Any,
    toast: Callable[[str], None] | None = None,
    navigate: Callable[[str], None] | None = None,
) -> Any:
    """Import plugin and call create_page. Returns Gtk.Widget or raises."""
    m = inst.manifest
    root = Path(m.install_path or inst.path)
    module = _load_module(m, root)
    create = getattr(module, m.entry.create, None)
    if create is None or not callable(create):
        raise AttributeError(
            f"{m.entry.module} has no callable {m.entry.create}()"
        )
    ctx = PluginContext(
        plugin_id=m.id,
        plugin_dir=root,
        manifest=m,
        services=services,
        toast=toast,
        navigate=navigate,
    )
    page = create(ctx)
    if page is None:
        raise RuntimeError("create_page returned None")
    return page


def pages_for_nav(
    *,
    services: Any,
    toast: Callable[[str], None] | None = None,
    navigate: Callable[[str], None] | None = None,
) -> list[tuple[str, PluginManifest, Any]]:
    """Load installed + *enabled* plugins that declare nav.

    Disabled plugins stay on disk but are omitted from the rail/stack.
    """
    out: list[tuple[str, PluginManifest, Any]] = []
    active_check = None
    if services is not None and hasattr(services, "installed_plugin_active"):
        active_check = services.installed_plugin_active

    for inst in list_installed():
        if not inst.has_nav:
            continue
        if active_check is not None and not active_check(inst.id):
            continue
        page_id = f"plugin:{inst.id}"
        try:
            widget = create_plugin_page(
                inst, services=services, toast=toast, navigate=navigate
            )
        except Exception as exc:
            widget = _error_page(inst.manifest, str(exc))
        out.append((page_id, inst.manifest, widget))
    return out


def _error_page(manifest: PluginManifest, err: str) -> Any:
    from gi.repository import Gtk

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.add_css_class("page")
    box.set_margin_top(24)
    box.set_margin_start(24)
    box.set_margin_end(24)
    t = Gtk.Label(label=f"Plugin failed: {manifest.name}", xalign=0)
    t.add_css_class("pane-header-title")
    box.append(t)
    e = Gtk.Label(label=err, xalign=0, wrap=True)
    e.add_css_class("muted")
    box.append(e)
    return box
