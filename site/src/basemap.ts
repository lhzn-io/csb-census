import type { Map as MapLibreMap, StyleSpecification } from "maplibre-gl";
import { ESRI_ATTRIBUTION } from "./config";

const ESRI = "https://services.arcgisonline.com/ArcGIS/rest/services/Ocean";

/** Esri World Ocean Base under the data, Esri Ocean Reference labels above it. */
export const style: StyleSpecification = {
  version: 8,
  sources: {
    ocean: {
      type: "raster",
      tiles: [`${ESRI}/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}`],
      tileSize: 256,
      maxzoom: 16,
      attribution: ESRI_ATTRIBUTION,
    },
    labels: {
      type: "raster",
      tiles: [`${ESRI}/World_Ocean_Reference/MapServer/tile/{z}/{y}/{x}`],
      tileSize: 256,
      maxzoom: 16,
    },
  },
  layers: [
    { id: "background", type: "background", paint: { "background-color": "#d6e4ec" } },
    { id: "ocean", type: "raster", source: "ocean" },
    { id: "labels", type: "raster", source: "labels" },
  ],
};

/** Layer the data is drawn beneath, so place names stay readable. */
export const DATA_BEFORE = "labels";

/**
 * If the basemap keeps failing (service down, blocked network), hide it rather than show a
 * patchwork of broken tiles: the hexagons stay readable on the plain background.
 */
export function watchBasemap(map: MapLibreMap, maxErrors = 12): void {
  let errors = 0;
  map.on("error", (e) => {
    const source = (e as { sourceId?: string }).sourceId;
    if (source !== "ocean" && source !== "labels") return;
    errors += 1;
    if (errors === maxErrors) {
      for (const id of ["ocean", "labels"]) map.setLayoutProperty(id, "visibility", "none");
      console.warn("basemap unavailable; showing data on a plain background");
    }
  });
}
