import type { Layer } from "@deck.gl/core";
import { H3HexagonLayer } from "@deck.gl/geo-layers";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { AttributionControl, Map as MapLibreMap, NavigationControl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import { DATA_BEFORE, setBasemap, styleFor, watchBasemap, type Basemap } from "./basemap";
import type { RGBA } from "./colors";
import { RES_FOR_ZOOM } from "./config";
import { cellsFor, visibleTiles, type Cell, type Tile } from "./tiles";

export interface MapViewOptions {
  container: string;
  center?: [number, number];
  zoom?: number;
  /** Basemap when the viewer has not chosen one on this page before. */
  basemap: Basemap;
  /** Remembers the viewer's basemap choice per page. */
  storageKey: string;
  /** Tiles currently on show (archive, a provider, or a recent window). */
  pool: () => Tile[];
  color: (cell: Cell, res: number) => RGBA;
  tooltip: (cell: Cell) => string;
  /** Changes whenever `color` would give different results, so deck.gl recolours. */
  colorKey: () => unknown;
  /** Extra layers drawn above the hexagons (for example the Long Island Sound r9 layer). */
  extra?: (zoom: number, hex: (id: string, data: Cell[], res: number) => Layer) => Layer[];
}

export interface MapView {
  map: MapLibreMap;
  refresh: () => Promise<void>;
}

// MapboxOverlay (interleaved) reads `beforeId` from layer props; deck's base layer types omit it.
const UNDER_LABELS = { beforeId: DATA_BEFORE } as object;

function savedBasemap(key: string, fallback: Basemap): Basemap {
  try {
    const v = localStorage.getItem(key);
    return v === "dark" || v === "ocean" ? v : fallback;
  } catch {
    return fallback;
  }
}

/** A MapLibre map with Esri basemaps and a deck.gl H3 overlay that loads only the tiles in view. */
export function createMapView(opts: MapViewOptions): MapView {
  const initial = savedBasemap(opts.storageKey, opts.basemap);
  const map = new MapLibreMap({
    container: opts.container,
    style: styleFor(initial),
    center: opts.center ?? [-40, 30],
    zoom: opts.zoom ?? 1.6,
    attributionControl: false,
    hash: true,
  });
  map.addControl(new NavigationControl({ showCompass: false }), "top-right");
  map.addControl(
    new AttributionControl({
      compact: true,
      customAttribution: "Census: Long Horizon Observatory, from NOAA NCEI / IHO DCDB data. Not for navigation.",
    }),
  );
  watchBasemap(map);
  const overlay = new MapboxOverlay({ interleaved: true, layers: [] });
  map.addControl(overlay);

  // Basemap radios (name="basemap"); the choice is a per-viewer convenience kept when storage allows.
  for (const input of document.querySelectorAll<HTMLInputElement>('input[name="basemap"]')) {
    input.checked = input.value === initial;
    input.addEventListener("change", () => {
      const kind = input.value as Basemap;
      setBasemap(map, kind);
      try {
        localStorage.setItem(opts.storageKey, kind);
      } catch {
        // storage unavailable (private window, blocked site data): the choice lasts for this visit
      }
    });
  }

  const hex = (id: string, data: Cell[], res: number): Layer =>
    new H3HexagonLayer<Cell>({
      ...UNDER_LABELS,
      id,
      data,
      getHexagon: (d) => d.h3,
      getFillColor: (d) => opts.color(d, res),
      extruded: false,
      stroked: false,
      highPrecision: res >= 8,
      pickable: true,
      updateTriggers: { getFillColor: [opts.colorKey()] },
    });

  let request = 0;
  async function refresh(): Promise<void> {
    const pool = opts.pool();
    const mine = ++request;
    const zoom = map.getZoom();
    const b = map.getBounds();
    const view: [number, number, number, number] = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
    let res: number = RES_FOR_ZOOM(zoom);
    if (!pool.some((t) => t.res === res)) {
      const coarser = pool.map((t) => t.res).filter((r) => r <= res);
      res = coarser.length ? Math.max(...coarser) : 4;
    }
    const cells = await cellsFor(visibleTiles(pool, res, view));
    if (mine !== request) return; // a newer view superseded this one
    overlay.setProps({
      layers: [hex(`hex-r${res}`, cells, res), ...(opts.extra?.(zoom, hex) ?? [])],
      getTooltip: ({ object }) => (object ? { text: opts.tooltip(object as Cell) } : null),
    });
  }

  let timer = 0;
  map.on("moveend", () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => void refresh(), 120);
  });
  return { map, refresh };
}

/** Run `fn` once the map style has loaded (immediately if it already has). */
export function whenLoaded(map: MapLibreMap, fn: () => void): void {
  if (map.loaded()) fn();
  else map.once("load", fn);
}
