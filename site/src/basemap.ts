import type { Map as MapLibreMap, StyleSpecification } from "maplibre-gl";
import { ESRI_ATTRIBUTION, ESRI_DARK_ATTRIBUTION } from "./config";

const ESRI = "https://services.arcgisonline.com/ArcGIS/rest/services";

export type Basemap = "ocean" | "dark";

/** Base and label raster layers for each basemap; the data is drawn between them. */
const LAYERS: Record<Basemap, { base: string; labels: string; background: string }> = {
  ocean: { base: "ocean", labels: "labels", background: "#d6e4ec" },
  dark: { base: "dark", labels: "dark-labels", background: "#1e2229" },
};

function raster(path: string, attribution?: string) {
  return {
    type: "raster" as const,
    tiles: [`${ESRI}/${path}/MapServer/tile/{z}/{y}/{x}`],
    tileSize: 256,
    maxzoom: 16,
    ...(attribution ? { attribution } : {}),
  };
}

/**
 * Esri World Ocean or Esri Dark Gray Canvas under the data, with each one's reference labels above
 * it. Both are in the style; only the visible basemap's tiles are requested.
 */
export function styleFor(kind: Basemap): StyleSpecification {
  const show = (name: Basemap) => ({ layout: { visibility: name === kind ? ("visible" as const) : ("none" as const) } });
  current = kind;
  document.documentElement.dataset.basemap = kind;
  return {
    version: 8,
    sources: {
      ocean: raster("Ocean/World_Ocean_Base", ESRI_ATTRIBUTION),
      labels: raster("Ocean/World_Ocean_Reference"),
      dark: raster("Canvas/World_Dark_Gray_Base", ESRI_DARK_ATTRIBUTION),
      "dark-labels": raster("Canvas/World_Dark_Gray_Reference"),
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": LAYERS[kind].background } },
      { id: "ocean", type: "raster", source: "ocean", ...show("ocean") },
      { id: "dark", type: "raster", source: "dark", ...show("dark") },
      { id: "labels", type: "raster", source: "labels", ...show("ocean") },
      { id: "dark-labels", type: "raster", source: "dark-labels", ...show("dark") },
    ],
  };
}

/** Layer the data is drawn beneath (the lowest label layer), so place names stay readable. */
export const DATA_BEFORE = "labels";

let current: Basemap = "dark";
let failed = false;

export function setBasemap(map: MapLibreMap, kind: Basemap): void {
  current = kind;
  for (const [name, ids] of Object.entries(LAYERS) as [Basemap, (typeof LAYERS)[Basemap]][]) {
    const visibility = name === kind && !failed ? "visible" : "none";
    map.setLayoutProperty(ids.base, "visibility", visibility);
    map.setLayoutProperty(ids.labels, "visibility", visibility);
  }
  map.setPaintProperty("background", "background-color", LAYERS[kind].background);
  document.documentElement.dataset.basemap = kind;
}

/**
 * If the basemap keeps failing (service down, blocked network), hide it rather than show a
 * patchwork of broken tiles: the hexagons stay readable on the plain background.
 */
export function watchBasemap(map: MapLibreMap, maxErrors = 12): void {
  // Base tiles only: the reference-label services answer empty areas with errors (no CORS header),
  // which is normal and must not take the whole basemap down.
  const sources = new Set(Object.values(LAYERS).map((l) => l.base));
  let errors = 0;
  map.on("error", (e) => {
    const source = (e as { sourceId?: string }).sourceId;
    if (!source || !sources.has(source)) return;
    errors += 1;
    if (errors === maxErrors) {
      failed = true;
      setBasemap(map, current);
      console.warn("basemap unavailable; showing data on a plain background");
    }
  });
}
