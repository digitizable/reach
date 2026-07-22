# Reach

<p align="center">
  <img src="data/assets/app-icon.png" alt="Reach" width="128"/>
</p>

**Reach** is a Linux desktop application for building and operating multi-hop network paths. It provides the operator interface: path profiles, adapter configuration, connection control, split-tunnel app rules, and optional plugins.

The path engine is **[Spectre](https://github.com/digitizable/spectre)** (`spectred`). Reach talks to it over a local Unix socket.

**Platform:** Linux · **UI:** GTK 4 / libadwaita · **Version:** 0.6.1

---

## What it does

| Area | Description |
|------|-------------|
| **Paths** | Ordered hops (VPN, Tor, VLESS+REALITY, SOCKS/HTTP proxy) as named profiles |
| **Adapters** | Concrete backend instances (endpoints, credentials, WireGuard configs) |
| **Home** | Connection status, path diagram, optional Mullvad exit map, Connect / Disconnect |
| **Territories** | Region-oriented profiles and reverse / dial-out setups |
| **Apps** | Clearnet / exclude-list split tunneling via network namespaces |
| **Routing** | System routing and kill switch (implemented by Spectre) |
| **Plugins** | Optional lab tools and operator plugins without enlarging the default install |

---

## Plugins

Reach ships a small core. Extra capability is opt-in.

| Layer | Contents |
|-------|----------|
| **Default** | Paths, adapters, Home, territories, apps, tray, basic diagnostics |
| **Built-in packs** | Path fingerprint measurement; installer UI for companion tools (Settings → Plugins) |
| **Marketplace** | GitHub installs (`owner/repo`) into `~/.local/share/reach/plugins/` |

Official marketplace example: **[Hogwarts](https://github.com/digitizable/hogwarts)** — command-and-control desk (agents, control plane, listeners, playbooks).

Plugin format: [docs/PLUGIN_SPEC.md](docs/PLUGIN_SPEC.md). Template: [examples/reach-plugin-hello](examples/reach-plugin-hello).

**Posture (Settings → Plugins):**

1. **Privacy** — core path console only  
2. **Lab** — fingerprint pack + companion installers (no C2 rail)  
3. **Operate** — unlocks marketplace / operator plugins on the rail  

---

## Related components

These are separate repositories. Reach does not embed them unless you install them.

| Project | Role |
|---------|------|
| [Spectre](https://github.com/digitizable/spectre) | Path core: hops, local SOCKS, routing, kill switch |
| [Drift](https://github.com/digitizable/drift) | Dial-out reverse agent and accept side |
| [Mirage](https://github.com/digitizable/mirage) | REALITY cover in front of reverse accept |
| [Sounding](https://github.com/digitizable/sounding) | Measurement utilities for faces and residual paths |
| [Laminar](https://github.com/digitizable/laminar) | Multi-hop composition / fingerprint measurement |
| [Hogwarts](https://github.com/digitizable/hogwarts) | Optional C2 plugin for Reach |

```
Reach (desktop UI)
  └── spectred (Spectre) ── path, SOCKS, routing
  └── optional plugins / lab companions
```

---

## Install

**1. Spectre core**

```bash
cd /path/to/spectre && ./install.sh
```

**2. Reach**

```bash
sudo apt install -y \
  python3 python3-venv python3-gi python3-gi-cairo \
  gir1.2-gtk-4.0 gir1.2-adw-1 \
  libadwaita-1-0 libgtk-4-1

cd /path/to/reach
./install.sh
reach
```

**Development**

```bash
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
python src/main.py
```

| Item | Default |
|------|---------|
| Core socket | `$XDG_RUNTIME_DIR/spectre/spectre.sock` |
| Config | `~/.config/reach/config.json` |
| Data | `~/.local/share/reach/` |

Uninstall: `./uninstall.sh`.

---

## UI map

| Page | Purpose |
|------|---------|
| Home | Status, path graph, map, connect |
| Paths | Profiles and adapters |
| Territories | Regional / reverse setups |
| Apps | Split-tunnel exclusions |
| Tools | Diagnostics and enabled lab tools |
| Plugins | Marketplace catalog and installs |
| Settings | Core, network, privacy, plugins, updates |

Deep links include `paths:recipes`, `paths:adapters`, `settings:plugins`.

---

## Data layout

| Path | Contents |
|------|----------|
| `~/.config/reach/config.json` | App settings and plugin enable flags |
| `~/.local/share/reach/backends.json` | Adapters |
| `~/.local/share/reach/profiles.json` | Path profiles |
| `~/.local/share/reach/plugins/` | Installed marketplace plugins |
| `~/.local/share/reach/lab/` | Optional companion checkouts |
| `~/.local/share/reach/desktop.log` | Application log (if enabled) |

Updates: Settings → Updates checks [GitHub Releases](https://github.com/digitizable/reach/releases).

---

## Security

- Plugins load as in-process Python UI. Install only repositories you trust.
- Operator plugins (including Hogwarts) are for systems and engagements you are authorized to control.
- Network paths and kill-switch behavior depend on Spectre and host privileges; misconfiguration can block connectivity or leak traffic—verify with your own tests before relying on a setup.

---

## Project

- Homepage: [anguish.sh/projects/reach](https://anguish.sh/projects/reach)
- Third-party notices: [data/THIRD_PARTY_NOTICES.md](data/THIRD_PARTY_NOTICES.md)
- License: [GNU GPLv3](LICENSE)

### Donate

| | Address |
|--|---------|
| **BTC** | `sp1qqgly4w3je7m64xh72047u04hwyflyqqf3rfmxchmyht5dndpas4txqsuj9e0jc9l3yql9c3k5el3quyxfh6kr6c9zplmaavj59kuk5kny5jf7cjr` |
| **XMR** | `89SWJrVXxEgHNiVNSEdjWsXMtRzpzoUX7ebToit9x7iuQADTZh5BGVjTywoQ4gn3SuSEzDhXpCEybi17HpwgYs7v2Xfjdue` |
