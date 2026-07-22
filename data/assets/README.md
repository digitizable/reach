# Assets

| File | Use |
|------|-----|
| `banner-readme.png` / `banner-readme.svg` | Legacy README hero (unused; README uses app-icon) |
| `app-icon.png` | README / social icon (rounded Creation of Adam plate) |
| `mark.svg` / `mark-light.svg` | In-app sidebar + loading mark (white hands outline) |
| `../icons/hicolor/scalable/apps/com.digitizable.reach.svg` | Desktop app icon |
| `globe.svg` | Reach territories sidebar nav (continents globe, white) |
| `paths.svg` | Paths sidebar — multi-hop chain mark (white) |
| `recipes.svg` | Paths → Recipes pane — open recipe book (Lucide `book-open`, white stroke) |
| `map-cn.svg` / `mainland-china.svg` | China silhouette (mask for flag fill on Territories) |
| `map-ir.svg` `map-ru.svg` `map-tr.svg` `map-cu.svg` `map-ae.svg` | Territory silhouettes (flag-filled at runtime) |
| `spectre.svg` / `spectre.png` | Spectre core mark (Game Icons “spectre” by Lorc, CC BY 3.0); recolored in Settings |
| Other files | Path marks (Tor/REALITY/Mullvad), etc. |

**Mark:** [path-distance](https://game-icons.net/1x1/delapouite/path-distance.html) by Delapouite ([CC BY 3.0](https://creativecommons.org/licenses/by/3.0/)).  
**paths.svg:** in-house multi-hop chain (three nodes + links), fill/stroke `#ffffff` for dark UI.  
**recipes.svg:** [Lucide](https://lucide.dev/) `book-open` ([ISC](https://github.com/lucide-icons/lucide/blob/main/LICENSE)); stroke `#ffffff` for dark UI.  
**globe.svg:** [Bootstrap Icons](https://icons.getbootstrap.com/) `globe-americas` ([MIT](https://github.com/twbs/icons/blob/main/LICENSE)); continents + sphere, fill `#ffffff` for dark UI.  
Country outlines: [djaiss/mapsicon](https://github.com/djaiss/mapsicon). White fills used as alpha masks; Territories paints national flags through them at runtime (`core/territory_flags.py`). Full flag SVGs may live under `flags/` (e.g. Iran) when hand-drawn emblems would look wrong.  
**Map geometry** (Home Mullvad map; bake with `scripts/bake_map_geo.py` from [Natural Earth](https://www.naturalearthdata.com/) public-domain vectors):

| File | Role |
|------|------|
| `world-landmass.json` | Continuous continents + islands (fill) |
| `world-lakes.json` | Major inland water — cut out of land so ocean shows through |
| `world-borders.json` | Admin-0 political boundary linework |
| `world-countries.json` | ISO2 closed rings + bbox/centroid for flag fills and camera focus |
| `world-land.json` | Legacy equirectangular land (fallback only) |

Runtime: `core/map_geo.py` + `widgets/mullvad_map.py`. Flags via `core/map_country_flags.py` (bundled SVGs under `flags/` or cached PNGs from [flagcdn.com](https://flagcdn.com/)).
