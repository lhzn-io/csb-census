import type { Layer } from "@deck.gl/core";
import { H3HexagonLayer } from "@deck.gl/geo-layers";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { AttributionControl, Map as MapLibreMap, NavigationControl, setWorkerUrl, type IControl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-csp-worker.js?url";

import { applyBasemap, dataBefore, isBasemap, setBasemap, styleUrl, type Basemap } from "./basemap";
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
  /** Changes whenever `color` would give different results, so deck.gl recolors. */
  colorKey: () => unknown;
  /** Extra layers drawn above the hexagons (for example the Long Island Sound r9 layer). */
  extra?: (zoom: number, hex: (id: string, data: Cell[], res: number) => Layer) => Layer[];
}

export interface MapView {
  map: MapLibreMap;
  refresh: () => Promise<void>;
}

// Vector basemaps parse tiles in MapLibre's web worker; under Vite the worker must be its own file.
setWorkerUrl(workerUrl);

const OPACITY_KEY = "csb-census:opacity";
/** Data opacity on the relief basemap until the viewer picks their own, so the seafloor shows through. */
const RELIEF_OPACITY = 0.6;

function savedOpacity(): number | null {
  try {
    const v = Number(localStorage.getItem(OPACITY_KEY));
    return v >= 0.2 && v <= 1 ? v : null;
  } catch {
    return null;
  }
}

/** A small "Data" opacity slider under the zoom buttons. */
class OpacityControl implements IControl {
  private el: HTMLDivElement | null = null;
  readonly input = document.createElement("input");
  constructor(value: number, onInput: (v: number) => void) {
    Object.assign(this.input, { type: "range", min: "0.2", max: "1", step: "0.05", value: String(value) });
    this.input.setAttribute("aria-label", "Data opacity");
    this.input.addEventListener("input", () => onInput(Number(this.input.value)));
  }
  onAdd(): HTMLElement {
    this.el = document.createElement("div");
    this.el.className = "maplibregl-ctrl maplibregl-ctrl-group opacity-ctrl";
    this.el.title = "Data opacity";
    const label = document.createElement("span");
    label.textContent = "Data";
    this.el.append(label, this.input);
    return this.el;
  }
  onRemove(): void {
    this.el?.remove();
  }
}

function savedBasemap(key: string, fallback: Basemap): Basemap {
  try {
    const v = localStorage.getItem(key);
    return isBasemap(v) ? v : fallback;
  } catch {
    return fallback;
  }
}

/** A MapLibre map with OpenFreeMap basemaps and a deck.gl H3 overlay that loads only the tiles in view. */
export function createMapView(opts: MapViewOptions): MapView {
  const initial = savedBasemap(opts.storageKey, opts.basemap);
  const map = new MapLibreMap({
    container: opts.container,
    style: styleUrl(initial),
    center: opts.center ?? [-40, 30],
    zoom: opts.zoom ?? 1.6,
    attributionControl: false,
    hash: true,
  });
  if (initial === "relief") applyBasemap(map, "relief"); // the constructor cannot transform a style
  if (import.meta.env.DEV) (window as unknown as { csbMap: MapLibreMap }).csbMap = map; // console debugging
  map.addControl(new NavigationControl({ showCompass: false }), "top-right");
  // Data opacity: the viewer's own choice when they have made one, else see-through on relief.
  let chosen = savedOpacity();
  let opacity = chosen ?? (initial === "relief" ? RELIEF_OPACITY : 1);
  const slider = new OpacityControl(opacity, (v) => {
    opacity = chosen = v;
    try {
      localStorage.setItem(OPACITY_KEY, String(v));
    } catch {
      // storage unavailable: the choice lasts for this visit
    }
    void refresh();
  });
  map.addControl(slider, "top-right");
  map.addControl(
    new AttributionControl({
      compact: true,
      customAttribution:
        'Census: <a href="https://longhorizon.eco/">Long Horizon Observatory</a>, from <a href="https://www.ncei.noaa.gov/">NOAA NCEI</a> / <a href="https://iho.int/en/data-centre-for-digital-bathymetry">IHO DCDB</a> data. Not for navigation.',
    }),
  );
  const overlay = new MapboxOverlay({ interleaved: true, layers: [] });
  map.addControl(overlay);

  // Basemap radios (name="basemap"); the choice is a per-viewer convenience kept when storage allows.
  for (const input of document.querySelectorAll<HTMLInputElement>('input[name="basemap"]')) {
    input.checked = input.value === initial;
    input.addEventListener("change", () => {
      const kind = input.value as Basemap;
      if (chosen === null) {
        opacity = kind === "relief" ? RELIEF_OPACITY : 1;
        slider.input.value = String(opacity);
      }
      setBasemap(map, kind, () => void refresh());
      try {
        localStorage.setItem(opts.storageKey, kind);
      } catch {
        // storage unavailable (private window, blocked site data): the choice lasts for this visit
      }
    });
  }

  const hex = (id: string, data: Cell[], res: number): Layer =>
    new H3HexagonLayer<Cell>({
      // MapboxOverlay (interleaved) reads beforeId from layer props; deck's base layer types omit it.
      ...({ beforeId: dataBefore(map) } as object),
      id,
      data,
      getHexagon: (d) => d.h3,
      opacity,
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
