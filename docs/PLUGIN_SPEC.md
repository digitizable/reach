# Reach plugin specification

Plugins extend Reach with optional UI pages and tools. Core Reach stays a **path console**; marketplace plugins add lab surfaces and C2 (e.g. **Hogwarts**) without shipping them to everyone by default.

## Install layout

```
~/.local/share/reach/plugins/<id-with-dots-as-__>/
  reach-plugin.json     # required
  ui.py                 # or entry.module path
  …
```

Install via **Plugins** marketplace (sidebar) → paste `owner/repo`, or clone manually into the plugins directory.

## Manifest: `reach-plugin.json`

Required at the **repository root** (or one level down).

```json
{
  "schema": 1,
  "id": "com.example.hello",
  "name": "Hello",
  "version": "0.1.0",
  "description": "Example Reach plugin",
  "author": "you",
  "homepage": "https://github.com/example/reach-plugin-hello",
  "license": "GPL-3.0-or-later",
  "category": "tool",
  "official": false,
  "entry": {
    "kind": "python",
    "module": "ui",
    "create": "create_page"
  },
  "nav": {
    "title": "Hello",
    "icon": "emblem-favorite-symbolic",
    "tooltip": "Hello plugin page",
    "icon_file": "icon.svg",
    "icon_symbolic": "icon-symbolic.svg"
  },
  "permissions": ["path_status", "toast"],
  "requires_reach": ">=0.5.0"
}
```

| Field | Meaning |
|-------|---------|
| `schema` | Must be `1` |
| `id` | Reverse-DNS unique id (`com.vendor.name`) |
| `name` / `version` | Display + semver-ish string |
| `category` | `lab` \| `operator` \| `tool` |
| `official` | Reserved for digitizable catalog (do not set true yourself) |
| `entry.kind` | Only `python` in Reach 0.6 |
| `entry.module` | Module name: `ui` → `ui.py`, or `pkg.page` |
| `entry.create` | Callable: `create_page(ctx) -> Gtk.Widget` |
| `nav` | If present, a sidebar button is added under **Plugins** |
| `nav.icon` | Theme symbolic fallback if custom files missing |
| `nav.icon_file` | Full-color SVG/PNG for **marketplace** (edge-to-edge ok) |
| `nav.icon_symbolic` | Optional white-on-transparent mark for **left rail** (themed monochrome). Prefer this over recoloring a filled plate |
| `permissions` | Declared intents (documented for users; not a sandbox yet) |

### Icons

| Surface | Behavior |
|---------|----------|
| **Plugins marketplace** | Full-color `icon_file` (fills the badge; official brand marks welcome) |
| **Left sidebar** | Prefer `icon_symbolic` (white-on-transparent mark, themed to the rail). Falls back to recoloring `icon_file` if symbolic is missing |

### Permissions (advisory)

| Permission | Intent |
|------------|--------|
| `path_status` | Read Spectre connection status |
| `socks` | Use local SOCKS URL |
| `toast` | Show toasts |
| `network` | Outbound network from plugin code |
| `filesystem` | Read/write under plugin data dir |

Reach does **not** fully sandbox plugins yet. Only install code you trust.

## Python entry

```python
# ui.py
from gi.repository import Gtk

def create_page(ctx):
    """
    ctx.plugin_id, ctx.plugin_dir, ctx.manifest
    ctx.services   — Reach Services (path, config, …)
    ctx.toast(msg)
    ctx.navigate(page_id)
    ctx.data_path(*parts)  — writable per-plugin data
    """
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.add_css_class("page")
    box.set_margin_top(24)
    box.set_margin_start(24)
    title = Gtk.Label(label=ctx.manifest.name, xalign=0)
    title.add_css_class("pane-header-title")
    box.append(title)
    return box
```

## Sidebar

| Section | Contents |
|---------|----------|
| **Run** | Home |
| **Path** | Paths, Territories |
| **Workspace** | Apps, Tools |
| **Operate** | Marketplace + installed plugins that declare `nav` (**gated**) |
| **System** | Settings |

Toggle **expand** at the bottom of the rail to show section labels and titles.

**Operate** is off by default (Privacy/Lab posture). Enable via **Settings → Plugins → Enable Operate** or the **Operate** preset. Path pages stay primary either way.

## Official vs community

- **Built-in packs**: Path fingerprint + Lab companions (Settings → Plugins; Privacy / Lab / Operate presets).
- **Operate marketplace**: Hogwarts (C2), Hello template, other digitizable catalog entries — only on the rail when Operate is on.
- **Community**: any GitHub repo with a valid manifest; marked unofficial in the marketplace.

Legacy Settings ids `reachback`, `face_probe`, and `egress` are stripped on load — C2 lives in **Hogwarts**.

## Publishing checklist

1. Repo root contains `reach-plugin.json` (schema 1).
2. `entry.module` file implements `create_page`.
3. GPL-compatible license recommended (Reach is GPLv3).
4. Do not claim `"official": true`.
5. Document permissions in your README.
6. Users install via Reach → Plugins → `owner/repo`.

## Example

See `examples/reach-plugin-hello/` in the Reach tree.
