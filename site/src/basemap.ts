import type { Map as MapLibreMap, StyleSpecification } from "maplibre-gl";

/**
 * OpenFreeMap vector basemaps (OpenStreetMap data, OpenMapTiles schema): open, no API key. Each style
 * brings its own attribution through its tile source. Both carry seafloor relief beneath their
 * coastlines and labels: darkened toward the slate palette at night, lightened by day.
 */
const STYLES = {
  dark: "https://tiles.openfreemap.org/styles/dark",
  light: "https://tiles.openfreemap.org/styles/positron",
} as const;

const RELIEF_PAINT = {
  dark: { "raster-saturation": -0.45, "raster-brightness-max": 0.5, "raster-contrast": 0.15 },
  light: { "raster-saturation": -0.7, "raster-brightness-min": 0.42, "raster-contrast": -0.1, "raster-opacity": 0.9 },
} as const;

export type Basemap = keyof typeof STYLES;

/**
 * NOAA NCEI's global DEM mosaic hillshade: GEBCO worldwide, with NOAA's higher-resolution coastal relief
 * models and multibeam surveys where they exist. NCEI renders each tile on request (public data, no key).
 */
const RELIEF_TILES =
  "https://gis.ngdc.noaa.gov/arcgis/rest/services/DEM_mosaics/DEM_global_mosaic_hillshade/ImageServer/exportImage" +
  "?bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=512,512&format=png&f=image";
const RELIEF_ATTRIBUTION =
  'Relief: <a href="https://www.ncei.noaa.gov/products/coastal-relief-model">NOAA NCEI</a> DEM mosaic, with ' +
  '<a href="https://www.gebco.net/">GEBCO</a>';

export function isBasemap(v: unknown): v is Basemap {
  return typeof v === "string" && v in STYLES;
}

export function styleUrl(kind: Basemap): string {
  document.documentElement.dataset.basemap = kind;
  return STYLES[kind];
}

/** A style with the relief raster inserted just above its water, painted for that style. */
const withRelief = (kind: Basemap) => (_previous: StyleSpecification | undefined, next: StyleSpecification): StyleSpecification => {
  const layers = [...next.layers];
  const water = layers.findIndex((l) => l.id === "water" || ("source-layer" in l && l["source-layer"] === "water"));
  layers.splice(water + 1, 0, {
    id: "relief",
    type: "raster",
    source: "relief",
    paint: { ...RELIEF_PAINT[kind], "raster-fade-duration": 200 },
  });
  return {
    ...next,
    sources: {
      ...next.sources,
      relief: { type: "raster", tiles: [RELIEF_TILES], tileSize: 512, maxzoom: 14, attribution: RELIEF_ATTRIBUTION },
    },
    layers,
  };
};

/** Load a basemap's style into the map, adding the relief as the style loads. */
export function applyBasemap(map: MapLibreMap, kind: Basemap): void {
  map.setStyle(styleUrl(kind), { transformStyle: withRelief(kind) });
}

/**
 * The data is drawn beneath the style's first label layer, so place names stay readable. Styles
 * differ in layer ids, so this is looked up on the loaded style each time layers are built.
 */
export function dataBefore(map: MapLibreMap): string | undefined {
  return map.getStyle()?.layers?.find((l) => l.type === "symbol")?.id;
}

/** Switch basemap; `onReady` runs once the new style has loaded (re-add the data layers there). */
export function setBasemap(map: MapLibreMap, kind: Basemap, onReady: () => void): void {
  map.once("style.load", onReady);
  applyBasemap(map, kind);
}
