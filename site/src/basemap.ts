import type { Map as MapLibreMap } from "maplibre-gl";

/**
 * OpenFreeMap vector basemaps (OpenStreetMap data, OpenMapTiles schema): open, no API key, no
 * bathymetric detail. Each style brings its own attribution through its tile source.
 */
const STYLES = {
  dark: "https://tiles.openfreemap.org/styles/dark",
  light: "https://tiles.openfreemap.org/styles/positron",
} as const;

export type Basemap = keyof typeof STYLES;

export function isBasemap(v: unknown): v is Basemap {
  return typeof v === "string" && v in STYLES;
}

export function styleUrl(kind: Basemap): string {
  document.documentElement.dataset.basemap = kind;
  return STYLES[kind];
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
  map.setStyle(styleUrl(kind));
}
