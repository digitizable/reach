# Spectre Desktop

<p align="center">
  <img src="data/assets/banner-readme.png" alt="Spectre Desktop" width="980"/>
</p>

GTK 4 + libadwaita frontend for [Spectre](https://github.com/digitizable/spectre): manage **backends** and **path profiles**, then Connect through the local **spectred** core.

Linux only for now. macOS/Windows would be separate frontends against the same core API.

## Status (0.2.0)

Routing modes (system / apps-only), connect preflight (Mullvad/Tor/proxy/tools), auto-discovered Apps launcher, GitHub update checks, and policy handoff to spectred for kill switch + system routing.

## What it is

Spectre Desktop is a **thin operator shell**, not the tunnel itself:

| Responsibility | Owner |
|----------------|--------|
| Backend catalog (VPN, REALITY, Tor, Proxy drafts) | Desktop |
| Profiles (ordered hops + which backend each hop uses) | Desktop |
| Readiness (“can we hand this to the core?”) | Desktop |
| Settings / policy (routing mode, kill switch, DNS, …) | Desktop stores; core enforces system routing + kill switch when connected |
| Live path, SOCKS entry, adapter processes | **spectred** core |

That split answers “which VPN do I use?”: define it under **Backends**, bind it on a hop under **Profiles**, then **Connect**.

## Pages

| Page | Role |
|------|------|
| **Home** | Status, path diagram, Connect / Disconnect, local SOCKS when up |
| **Profiles** | Path recipes; hop order; bind each hop to a backend |
| **Backends** | Concrete adapters (fill provider/config/UUID/etc.) |
| **Apps** | Applications that launch through the active path |
| **Settings** | Core socket, API token, network/privacy policy, **updates** (GitHub), logging |

### App routing

**Settings → Routing mode** chooses how traffic uses the path:

| Mode | Behavior |
|------|----------|
| **Entire system** (default) | Core redirects machine TCP/DNS through the path after Connect |
| **Selected apps only** | Only **Apps** launcher / SOCKS clients use Spectre; rest of the OS stays clearnet *from Spectre’s point of view* |

Under **Apps**, installed desktop applications are **detected automatically**. **Launch** sets `ALL_PROXY` / `HTTP(S)_PROXY` → Spectre SOCKS. Optional **proxychains** mode needs `proxychains-ng`.

### Mullvad and “selected apps only”

The **Mullvad app does not support include-only split tunneling** (only *exclude* apps from the VPN). Mullvad’s FAQ states they do not plan inverse split tunneling — VPN should be default, not exception.

So:

- **Mullvad app Connected** ⇒ the **whole system** is already on Mullvad (unless you exclude apps *out* of the tunnel).
- Spectre’s **Mullvad SOCKS** hop (`10.64.0.1:1080`) only exists while that tunnel is up — it cannot offer “only Firefox through Mullvad, everything else clearnet.”
- Spectre **apps-only** mode does not undo Mullvad’s full tunnel; it only skips Spectre’s own system redirect.

**True selected-apps-through-Mullvad + rest clearnet:**

1. Disconnect the **Mullvad app**
2. Add a **VPN** backend with a Mullvad **WireGuard `.conf`**
3. Spectre **Routing mode → Selected apps only**
4. Connect, then **Apps → Launch** the apps you want on Mullvad

The home screen warns when a profile uses Mullvad app SOCKS in a way that conflicts with apps-only expectations.

### Connect preflight

**Connect** runs live checks before talking to the core (so the UI fails fast with a clear toast instead of a hung/half-applied path):

| Hop / policy | Blocked when |
|--------------|----------------|
| **Mullvad SOCKS** (`10.64.0.1`) | Mullvad CLI reports Disconnected, or tunnel SOCKS is closed |
| **Tor** | SOCKS host:port not accepting connections |
| **Proxy** | Host:port unreachable |
| **WireGuard VPN** | `.conf` missing/unreadable, or `wg-quick` not installed |
| **REALITY** | Incomplete fields, `xray` missing, or server name won’t resolve |
| **System routing** | `spectre-nft` helper not set up (`spectre setup-killswitch`) |

Structural checks (backend bound, enabled, complete) always run; list “ready” tags stay structural-only so scrolling Profiles stays snappy.

CLI:

```bash
spectre run -- curl https://am.i.mullvad.net/json
```

Defaults seed example backends (draft VPN, draft REALITY, **System Tor**) and sample profiles. **Tor only** is ready out of the box if Tor is listening on `9050`.

## How Connect works

1. Desktop checks readiness (every hop has an **enabled, complete** backend).
2. If the core is offline, it tries `systemctl --user start spectred`, then `spectre start`, then a sibling/repo `spectred` binary.
3. `POST /v1/connect` with the full payload: profile, hops, backend objects, policy.
4. UI shows core state; when connected, the detail line includes **SOCKS `host:port`**.

Incomplete backends (e.g. WireGuard without a `.conf`, REALITY without UUID) stay drafts — Connect stays blocked until fixed.

### Completeness rules (desktop)

| Kind | Ready when |
|------|------------|
| **Tor** | System Tor, or custom SOCKS host/port |
| **Proxy** | Host + port |
| **VPN (WireGuard)** | Path to a `.conf` (Browse… in the editor) |
| **REALITY** | Server + public key + **UUID** |

## Core dependency

Install and run the core first (or let the desktop start it):

```bash
cd ../spectre   # or clone digitizable/spectre
./install.sh    # → ~/.local/bin + systemd --user unit
spectre health
```

| Default | Value |
|---------|--------|
| Socket | `$XDG_RUNTIME_DIR/spectre/spectre.sock` |
| Override | Settings → Socket path, or `SPECTRE_SOCKET` |

See the core README and [API docs](https://github.com/digitizable/spectre/blob/main/docs/API.md) for hop behavior. Apps use the local SOCKS entry; with **kill switch** on, spectred installs nftables rules so clearnet cannot bypass the path (needs `nft` privileges once — `spectre` repo `scripts/setup-killswitch-privs.sh`).

## Whonix

Install and run Spectre Desktop on **Whonix-Workstation** (not the Gateway):

1. Gateway / `sys-whonix` online with Tor connected  
2. Core installed on the Workstation (`spectre start`)  
3. Desktop seed backend becomes **Whonix Gateway Tor** (Gateway SOCKS)  
4. Use the **Tor only** profile (or any path that binds that Tor backend)

VPN hops are blocked by the core on Workstation unless `SPECTRE_ALLOW_VPN_ON_WHONIX=1` (not recommended). Home shows a Whonix hint when the core reports `environment.whonix`.

More: [Whonix notes in spectre](https://github.com/digitizable/spectre/blob/main/docs/WHONIX.md).

## Install & run

Dependencies (Debian/Ubuntu/Mint-style):

```bash
sudo apt install -y \
  python3 python3-venv python3-gi python3-gi-cairo \
  gir1.2-gtk-4.0 gir1.2-adw-1 \
  libadwaita-1-0 libgtk-4-1
```

```bash
./install.sh
spectre-desktop
```

Dev without install:

```bash
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
python src/main.py
```

`./install.sh --check` only verifies GTK/Adw bindings.

## Data

| Path | Contents |
|------|----------|
| `~/.local/share/spectre-desktop/backends.json` | Backend catalog |
| `~/.local/share/spectre-desktop/profiles.json` | Profiles + hop bindings |
| `~/.local/share/spectre-desktop/apps.json` | Custom apps + mode/hide overrides for discovered apps |
| `~/.config/spectre-desktop/config.json` | Settings + last active profile + update-check prefs |

### Updates

Settings → **Updates** (on by default) polls [GitHub Releases](https://github.com/digitizable/spectre-desktop/releases) on a schedule (default every 24 hours). There is no auto-install — you get a dialog with a link to the release page. Manual check: menu **Check for updates…** or Settings → **Check**.
| `~/.local/share/spectre-desktop/desktop.log` | Optional desktop log |

Uninstall: `./uninstall.sh`.

## Project layout

```
src/
  main.py application.py window.py   # app shell
  services.py                        # config + stores + connect_active
  core/                              # backends, profiles, readiness, client
  pages/                             # home, profiles, backends, settings
  widgets/                           # editors, path graph, chrome
data/assets/                         # brand + Tor/REALITY marks
```

## Related

- [Project page](https://anguish.sh/projects/spectre-desktop) — anguish.sh  
- [Spectre core](https://github.com/digitizable/spectre) — `spectred` + CLI  

## Donate

Optional support for development:

| | Address |
|--|---------|
| **BTC** | `sp1qqgly4w3je7m64xh72047u04hwyflyqqf3rfmxchmyht5dndpas4txqsuj9e0jc9l3yql9c3k5el3quyxfh6kr6c9zplmaavj59kuk5kny5jf7cjr` |
| **XMR** | `89SWJrVXxEgHNiVNSEdjWsXMtRzpzoUX7ebToit9x7iuQADTZh5BGVjTywoQ4gn3SuSEzDhXpCEybi17HpwgYs7v2Xfjdue` |

## License

[GNU GPLv3](LICENSE)
