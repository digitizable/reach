# Reach

<p align="center">
  <img src="data/assets/app-icon.png" alt="Reach" width="160"/>
</p>

<p align="center">
  <strong>Path console for restricted networks.</strong><br/>
  Lab packs for measurement · Hogwarts for C2.
</p>

**Reach** is a GTK 4 + libadwaita shell for composing multi-hop privacy paths and territory doors. It drives the [Spectre](https://github.com/digitizable/spectre) core (`spectred`). Optional **plugins** unlock research tools and C2 without bloating the default install.

Linux only for now · **v0.6.0**

**Formerly Spectre Desktop** (config under `~/.config/spectre-desktop` migrates to `reach` on first run).

---

## Why plugins?

| Profile | What you get |
|---------|----------------|
| **Core (default)** | Paths, adapters, territories, Mullvad map, kill switch, basic Tools |
| **+ Lab packs** | Path fingerprint + lab companions (Settings → Plugins) |
| **+ Marketplace** | **Hogwarts** (C2), Hello template, community `owner/repo` installs |

### Marketplace & rail

- **Sidebar → Plugins** — catalog, install from `owner/repo`, enable/disable, remove
- **Expand the rail** (chevron at the bottom) — labels **Official** vs **Plugins**
- Installed plugins that declare `nav` get their own sidebar page (GTK frontend)

Plugin format: [docs/PLUGIN_SPEC.md](docs/PLUGIN_SPEC.md) · template: [examples/reach-plugin-hello](examples/reach-plugin-hello)

Built-in lab packs toggle under **Settings → Plugins** (Privacy / Lab presets). Marketplace packages install into `~/.local/share/reach/plugins/`.

---

## Features

### Core — path console

- Multi-hop **profiles** bound to **adapters** (VPN, REALITY, Tor, proxy)
- **Home**: status, path graph, Mullvad country/city map, Connect
- **Territories**: host-in-region or peer dial-out (Inverse Snowflake class)
- **Apps**: exclude-list split tunnel / clearnet netns
- System routing + kill switch (via Spectre), tray lock, preflight

### Built-in packs — Settings → Plugins

| Pack | Role |
|------|------|
| **Path fingerprint** | Lab ΔRTT / path latency on live SOCKS (Laminar F2) |
| **Lab companions** | Install surface for Drift, Mirage, Sounding, Laminar |

### Marketplace — install when needed

| Plugin | Role |
|--------|------|
| **[Hogwarts](https://github.com/digitizable/reach-plugin-hogwarts)** | **C2 for Reach** — channels, listeners, egress, playbooks |
| **Hello** | Plugin template (`examples/reach-plugin-hello`) |

Research engines stay separate; Reach orchestrates:

| Project | Role |
|---------|------|
| [Spectre](https://github.com/digitizable/spectre) | Multi-hop path core |
| [Drift](https://github.com/digitizable/drift) | Inverse Snowflake / reverse pathing |
| [Mirage](https://github.com/digitizable/mirage) | Probe-resistant cover |
| [Sounding](https://github.com/digitizable/sounding) | Measurement lab |
| [Laminar](https://github.com/digitizable/laminar) | Composition fingerprint measure |
| [Hogwarts](https://github.com/digitizable/reach-plugin-hogwarts) | C2 console plugin |

---

## Architecture

| Responsibility | Owner |
|----------------|--------|
| UI, backends, profiles, plugins, readiness | **Reach** |
| Live path, SOCKS, routing, kill switch | **spectred** (Spectre) |
| Reverse / cover / measure engines | Lab companions (optional packs) |
| C2 | **Hogwarts** (marketplace plugin) |

```
Reach (shell)
  ├── Spectre core API
  ├── Core Tools (always)
  ├── Built-in packs → fingerprint · lab companions
  └── Marketplace → Hogwarts (C2) · community plugins
```

---

## Install & run

```bash
# Spectre core first
cd ../spectre && ./install.sh

# Reach
cd ../reach   # or spectre-desktop checkout
sudo apt install -y \
  python3 python3-venv python3-gi python3-gi-cairo \
  gir1.2-gtk-4.0 gir1.2-adw-1 \
  libadwaita-1-0 libgtk-4-1
./install.sh
reach
```

Dev:

```bash
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
python src/main.py
```

| Default | Value |
|---------|--------|
| Socket | `$XDG_RUNTIME_DIR/spectre/spectre.sock` |
| Config | `~/.config/reach/config.json` (`plugins_enabled`) |
| Data | `~/.local/share/reach/` |

### Enable lab packs & C2 (posture)

1. **Settings → Plugins** — choose a preset, then **Save**:
   - **Privacy** — core path console only (default; firewall / concealment users)
   - **Lab** — path fingerprint + lab companions (still no C2 rail)
   - **Operate** — lab packs + **Operate** rail (marketplace, Hogwarts, …)
2. Or toggle **Enable Operate** alone to unlock marketplace without changing packs  
3. **Operate → Plugins** — install **Hogwarts** for C2 (`digitizable/reach-plugin-hogwarts`)

---

## Pages

| Page | Role |
|------|------|
| **Home** | Status, path, Mullvad map, Connect |
| **Paths** | Recipes + adapters (in-page panes) |
| **Territories** | Region reach / peer dial-out |
| **Apps** | Clearnet carve-outs |
| **Tools** | Core diagnostics + built-in lab tools |
| **Plugins** | Marketplace — official catalog & GitHub install |
| **Settings** | **Hub** — plugins packs · core · network · privacy · … |

Deep links: `paths:recipes`, `paths:adapters`, `settings:plugins`, …

---

## Security note

Plugins run as in-process Python UI. Only install repositories you trust. Hogwarts is dual-use C2 for infrastructure and engagements you control — not for unauthorized access.

---

## Data

| Path | Contents |
|------|----------|
| `~/.config/reach/config.json` | Settings, `plugins_enabled`, window, updates |
| `~/.local/share/reach/backends.json` | Adapters |
| `~/.local/share/reach/profiles.json` | Paths |
| `~/.local/share/reach/plugins/` | Marketplace plugin installs |
| `~/.local/share/reach/lab/` | Companion checkouts |
| `~/.local/share/reach/desktop.log` | Optional log |

Updates: Settings → Updates polls [GitHub Releases](https://github.com/digitizable/reach/releases).

Uninstall: `./uninstall.sh`.

---

## Brand

App icon: Creation of Adam hands. See [THIRD_PARTY_NOTICES.md](data/THIRD_PARTY_NOTICES.md).

Homepage: [anguish.sh/projects/reach](https://anguish.sh/projects/reach)

---

## Donate

| | Address |
|--|---------|
| **BTC** | `sp1qqgly4w3je7m64xh72047u04hwyflyqqf3rfmxchmyht5dndpas4txqsuj9e0jc9l3yql9c3k5el3quyxfh6kr6c9zplmaavj59kuk5kny5jf7cjr` |
| **XMR** | `89SWJrVXxEgHNiVNSEdjWsXMtRzpzoUX7ebToit9x7iuQADTZh5BGVjTywoQ4gn3SuSEzDhXpCEybi17HpwgYs7v2Xfjdue` |

## License

[GNU GPLv3](LICENSE)
