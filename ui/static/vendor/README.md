# Vendored third-party files

Nothing here is loaded from a CDN. Files were downloaded from the npm registry
tarballs on 2026-10-12 and copied unchanged, except that the trailing
`sourceMappingURL` comment was removed from `leaflet/leaflet.js` (the map file is
not shipped).

| Library | Version | Files | License | Notes |
|---|---|---|---|---|
| Vue 3 | 3.5.13 | `vue.global.prod.js` | MIT (`LICENSE-vue`) | Global production build with the template compiler |
| Leaflet | 1.9.4 | `leaflet/leaflet.js`, `leaflet/leaflet.css`, `leaflet/images/*` | BSD-2-Clause (`LICENSE-leaflet`) | Marker and layer images included |
| Cytoscape.js | 3.30.4 | `cytoscape.min.js` | MIT (`LICENSE-cytoscape`) | |
| Inter (via `@fontsource/inter`) | 5.1.0 | `fonts/inter-latin-{400,500,600,700}-normal.woff2` | SIL Open Font License 1.1 (`LICENSE-inter`) | Latin subset; used with `font-display: swap` |

Map tiles are the one thing that needs the network: OpenStreetMap standard tiles
(`https://tile.openstreetmap.org/{z}/{x}/{y}.png`, (c) OpenStreetMap contributors,
ODbL). Offline, the map area says "Map tiles unavailable offline" and the shape
and coordinates are still drawn.
