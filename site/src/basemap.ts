import { addProtocol, type Map as MapLibreMap, type StyleSpecification } from "maplibre-gl";

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
 * NOAA NCEI's global DEM mosaic: GEBCO worldwide, with NOAA's higher-resolution coastal relief models and
 * multibeam surveys where they exist. NCEI renders each tile on request (public data, no key).
 *
 * The relief is seafloor only. Each tile is NCEI's color hillshade, cut to the pixels of a second request
 * that masks the DEM to elevations at or below 0 m (NCEI's color hillshade preset ignores a mask fed into
 * it, so the cut is made here). Land comes back transparent, so it reads as one flat class. Lakes whose
 * floor lies above sea level, such as the Great Lakes, get no relief.
 */
const DEM = "https://gis.ngdc.noaa.gov/arcgis/rest/services/DEM_mosaics";
// The mask is hillshaded (its shading is discarded) because only an 8-bit result keeps the masked-out land
// transparent in the PNG; the masked elevation alone comes back opaque.
const SEAFLOOR_MASK = {
  rasterFunction: "Hillshade",
  rasterFunctionArguments: {
    Raster: { rasterFunction: "Mask", rasterFunctionArguments: { IncludedRanges: [-11000, 0], NoDataValues: [], Raster: "$$" } },
  },
};
const exportImage = (service: string, bbox: string, extra = "") =>
  `${DEM}/${service}/ImageServer/exportImage?bbox=${bbox}&bboxSR=3857&imageSR=3857&size=512,512&format=png&f=image${extra}`;

addProtocol("seafloor", async ({ url }, abort) => {
  const bbox = url.slice("seafloor://".length);
  const image = async (src: string) => {
    const res = await fetch(src, { signal: abort.signal });
    if (!res.ok) throw new Error(`relief ${res.status}`);
    return createImageBitmap(await res.blob());
  };
  const [shade, mask] = await Promise.all([
    image(exportImage("DEM_global_mosaic_hillshade", bbox)),
    image(exportImage("DEM_global_mosaic", bbox, `&renderingRule=${encodeURIComponent(JSON.stringify(SEAFLOOR_MASK))}`)),
  ]);
  const canvas = new OffscreenCanvas(shade.width, shade.height);
  const ctx = canvas.getContext("2d")!;
  ctx.drawImage(shade, 0, 0);
  ctx.globalCompositeOperation = "destination-in"; // keep the shade only where the mask has pixels
  ctx.drawImage(mask, 0, 0, shade.width, shade.height);
  return { data: await (await canvas.convertToBlob({ type: "image/png" })).arrayBuffer() };
});
const RELIEF_TILES = "seafloor://{bbox-epsg-3857}";

/**
 * What the basemap keeps of each OpenFreeMap style, by OpenMapTiles source layer: land (the background),
 * water, ice, country borders and the larger place and water names. Roads, rail, buildings, land use,
 * woods, rivers and airports are dropped, so the land reads as one class and the coastline carries the map.
 */
const KEEP_LAYERS = new Set(["water", "water_name", "landcover", "boundary", "place"]);
function keepLayer(layer: StyleSpecification["layers"][number]): boolean {
  if (layer.type === "background") return true;
  const source = "source-layer" in layer ? layer["source-layer"] : undefined;
  if (!source || !KEEP_LAYERS.has(source)) return false;
  if (source === "landcover") return /ice|glacier/.test(layer.id);
  if (source === "boundary") return /country|_2$/.test(layer.id) && !/disputed/.test(layer.id);
  if (source === "place") return !/village|suburb|other/.test(layer.id);
  return true;
}

/**
 * Land on the Day basemap: a warm parchment between lhzn-blue's light page (#faf7f0) and its map ground
 * (#efebe2), a step darker so the land no longer outshines the sea. Night keeps its style's own near-black.
 */
const LAND = { light: "#e9e4d8" } as const;

const COASTLINE = {
  dark: { "line-color": "#8a96a0", "line-opacity": 0.55 },
  light: { "line-color": "#5c6670", "line-opacity": 0.6 },
} as const;
const RELIEF_ATTRIBUTION =
  'Relief: <a href="https://www.ncei.noaa.gov/products/coastal-relief-model">NOAA NCEI</a> DEM mosaic, with ' +
  '<a href="https://www.gebco.net/">GEBCO</a>';

export function styleUrl(kind: Basemap): string {
  return STYLES[kind];
}

/**
 * A style trimmed to land, water and names (see `keepLayer`), with the seafloor relief inserted just above
 * its water and a thin coastline drawn over the relief, painted for that style.
 */
const withRelief = (kind: Basemap) => (_previous: StyleSpecification | undefined, next: StyleSpecification): StyleSpecification => {
  const layers = next.layers
    .filter(keepLayer)
    .map((l) =>
      l.type === "background" && kind === "light" ? { ...l, paint: { ...l.paint, "background-color": LAND.light } } : l,
    );
  const water = layers.findIndex((l) => l.id === "water" || ("source-layer" in l && l["source-layer"] === "water"));
  const source = (layers[water] as { source?: string } | undefined)?.source ?? "openmaptiles";
  layers.splice(
    water + 1,
    0,
    { id: "relief", type: "raster", source: "relief", paint: { ...RELIEF_PAINT[kind], "raster-fade-duration": 200 } },
    {
      id: "coastline",
      type: "line",
      source,
      "source-layer": "water",
      filter: ["==", ["get", "class"], "ocean"], // the sea's edge only, not every reservoir and river
      paint: { ...COASTLINE[kind], "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.4, 10, 1] },
    },
  );
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

/**
 * Map credits as a small (i) button: MapLibre's compact attribution opens on load, so close it once the
 * map is up. The credits stay one click away.
 */
export function collapseCredits(map: MapLibreMap): void {
  let touched = false; // once the viewer uses the (i), it is theirs to open and close
  map.getContainer().addEventListener(
    "click",
    (e) => {
      if ((e.target as Element).closest(".maplibregl-ctrl-attrib-button")) touched = true;
    },
    true,
  );
  const close = () => {
    if (touched) return;
    const el = map.getContainer().querySelector<HTMLElement>(".maplibregl-ctrl-attrib");
    el?.classList.remove("maplibregl-compact-show");
    el?.removeAttribute("open");
  };
  // MapLibre opens the compact credits when its first attribution arrives, which can be after load.
  map.on("styledata", close);
  map.on("load", close);
}
