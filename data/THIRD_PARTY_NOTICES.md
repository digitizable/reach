# Third-party notices

## App icon & mark — Creation of Adam hands

- **App icon:** User-provided plate (`for-grok/app-icon-2.jpg`) — hands on green–blue wash, outer white removed, rounded transparent plate
- **Sidebar / loading mark:** User-provided outline (`for-grok/hands-outline.jpg`) rendered white on transparent (`data/assets/mark.svg`)
- **Use:** `data/icons/hicolor/scalable/apps/com.digitizable.reach.svg`, `data/assets/mark.svg`, `data/assets/app-icon.png` (README)

## Paths / Recipes marks

- **paths.svg:** In-house multi-hop chain (three nodes + links) for the Paths sidebar
- **recipes.svg:** [Lucide `book-open`](https://lucide.dev/icons/book-open) — **ISC License** ([lucide-icons/lucide](https://github.com/lucide-icons/lucide)); open recipe-book glyph for the Recipes pane

## Spectre mark (monogram)

- **Mark:** Spectre monogram (S with dual arrows) — same art as Spectre / [anguish.sh Spectre](https://anguish.sh/projects/spectre)
- **Source asset:** `spectre/docs/assets/spectre-icon-v2.png` (white monogram on black rounded plate)
- **Reach processing:** black plate removed → transparent; glyph recolored to UI blue-ish (`#c7d4ee`) at load
- **Use:** `data/assets/spectre.png` on Settings → Spectre core and Tools → Core status

## Tray locks (connected / disconnected / connecting)

- **Design:** In-house basic shapes (body rect + stroke shackle + keyhole), matched proportions across states
- **Use:** `spectre-tray-{locked,unlocked,connecting}.svg`

## Project X / Xray logo

- **Work:** Project X logo (`logo-dark.svg` / `logo-light.svg`)
- **Source:** [xtls.github.io](https://xtls.github.io/) (Project X official site)
- **Project:** [XTLS / Xray-core](https://github.com/XTLS/Xray-core), [REALITY](https://github.com/XTLS/REALITY)

Used in the path diagram for REALITY / Xray hops (`data/assets/reality.svg`).
Project X, Xray, and REALITY names and marks belong to their respective owners.

## mapsicon country outlines

- **Source:** [djaiss/mapsicon](https://github.com/djaiss/mapsicon)
- **Use:** Territory silhouettes under `data/assets/map-*.svg` (alpha masks for flag fills)

## National flag of Iran (SVG)

- **Source:** [Wikimedia Commons — Flag of Iran.svg](https://commons.wikimedia.org/wiki/File:Flag_of_Iran.svg)
- **License:** Public domain (national flag)
- **Use:** `data/assets/flags/ir.svg` — painted through the Iran map silhouette on Territories

## Mullvad VPN

- **Client:** [mullvadvpn-app](https://github.com/mullvad/mullvadvpn-app) — **GPL-3.0**
- **Use:** Reach drives the installed `mullvad` CLI for status, connect/disconnect, and relay location. The animated map uses Mullvad’s public relay location API (`api.mullvad.net`) for city coordinates. Reach does not ship Mullvad’s proprietary branding map assets; dots are generated in-app.

## World land outlines (map)

- **Source:** [johan/world.geo.json](https://github.com/johan/world.geo.json) (simplified for equirectangular display)
- **Use:** `data/assets/world-land.json` land fill on the Home Mullvad map
