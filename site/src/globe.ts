/**
 * The landing globe: every H3 r4 cell of the archive on a spinning globe. Water is slate and land is the
 * page color, as on the ocean square, so only the ocean and the data show. Drag to turn it; click to open
 * the archive chart at that spot. Loaded only when the globe view is on.
 */
import { cellToBoundary, latLngToCell } from "h3-js";
import { AttributionControl, Map as MapLibreMap, setWorkerUrl, type GeoJSONSource, type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-csp-worker.js?url";

import type { RGBA } from "./colors";
import type { Cell } from "./tiles";

setWorkerUrl(workerUrl);

const OCEAN = "#22374a";
const LAND = "#111111";
const SPIN_DEG_PER_S = 3;

const STYLE: StyleSpecification = {
  version: 8,
  projection: { type: "globe" },
  sources: {
    omt: { type: "vector", url: "https://tiles.openfreemap.org/planet" },
    // promoteId: the hover outline is feature-state, which the GPU applies without re-tiling the source.
    cells: { type: "geojson", data: { type: "FeatureCollection", features: [] }, promoteId: "i" },
  },
  layers: [
    { id: "land", type: "background", paint: { "background-color": LAND } },
    { id: "water", type: "fill", source: "omt", "source-layer": "water", paint: { "fill-color": OCEAN } },
    {
      id: "cells",
      type: "fill",
      source: "cells",
      paint: { "fill-color": ["get", "color"], "fill-opacity": ["get", "alpha"], "fill-antialias": false },
    },
    {
      id: "hover",
      type: "line",
      source: "cells",
      paint: {
        "line-color": "#decf03",
        "line-width": 1.5,
        "line-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 1, 0],
      },
    },
  ],
};

export interface Globe {
  map: MapLibreMap;
  /** Recolor every cell (after the metric changes). */
  recolor: (color: (c: Cell) => RGBA) => void;
  /** Turn the globe to face a center, as [lon, lat]. */
  face: (center: [number, number]) => void;
}

/** The zoom at which the globe fills most of a square container (its circumference is 512 * 2^zoom px). */
function fitZoom(el: HTMLElement): number {
  const side = Math.max(120, Math.min(el.clientWidth, el.clientHeight));
  return Math.log2((side * 0.92 * Math.PI) / 512);
}

/** H3 boundaries as GeoJSON rings, with cells across the antimeridian kept on one side. */
function ring(h3: string): [number, number][] {
  const pts = cellToBoundary(h3, true) as [number, number][];
  const lons = pts.map(([lon]) => lon);
  if (Math.max(...lons) - Math.min(...lons) > 180) return pts.map(([lon, lat]) => [lon < 0 ? lon + 360 : lon, lat]);
  return pts;
}

export function createGlobe(opts: {
  container: string;
  cells: Cell[];
  center: [number, number];
  color: (c: Cell) => RGBA;
  tooltip: (c: Cell, touch: boolean) => string;
  tip: HTMLElement;
}): Globe {
  const el = document.getElementById(opts.container)!;
  const map = new MapLibreMap({
    container: opts.container,
    style: STYLE,
    center: opts.center,
    zoom: fitZoom(el),
    maxZoom: 5,
    attributionControl: false,
    renderWorldCopies: false,
  });
  if (import.meta.env.DEV) (window as unknown as { csbGlobe: MapLibreMap }).csbGlobe = map; // console debugging
  map.addControl(new AttributionControl({ compact: true })); // the tile source carries its own credits
  const features = opts.cells.map((c, i) => ({
    type: "Feature" as const,
    properties: { i, color: "", alpha: 0 },
    geometry: { type: "Polygon" as const, coordinates: [[...ring(c.h3), ring(c.h3)[0]]] },
  }));
  const paint = (color: (c: Cell) => RGBA) => {
    features.forEach((f, i) => {
      const [r, g, b, a] = color(opts.cells[i]);
      f.properties.color = `rgb(${r},${g},${b})`;
      f.properties.alpha = Math.min(1, (a / 255) * 1.15);
    });
    (map.getSource("cells") as GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features });
  };

  // A slow spin until someone takes hold of it (and never for people who ask for less motion).
  let spinning = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const spin = () => {
    if (!spinning || map.isMoving()) return;
    const c = map.getCenter();
    map.easeTo({ center: [c.lng + SPIN_DEG_PER_S, c.lat], duration: 1000, easing: (t) => t });
  };
  const stop = () => {
    spinning = false;
  };
  map.on("mousedown", stop);
  map.on("touchstart", stop);
  map.on("wheel", stop);
  map.on("moveend", spin);
  // Keep the globe fitted to its square as the page resizes, until someone zooms in themselves.
  let zoomed = false;
  map.on("zoomstart", (e) => {
    if (e.originalEvent) zoomed = true;
  });
  map.on("resize", () => {
    map.setMinZoom(fitZoom(el) - 0.5);
    if (!zoomed) map.setZoom(fitZoom(el));
  });

  let hover = -1;
  const show = (i: number, x: number, y: number, touch: boolean) => {
    if (i !== hover) {
      if (hover >= 0) map.setFeatureState({ source: "cells", id: hover }, { hover: false });
      if (i >= 0) map.setFeatureState({ source: "cells", id: i }, { hover: true });
      hover = i;
      map.getCanvas().style.cursor = i >= 0 ? "pointer" : "grab";
      if (i >= 0) opts.tip.innerHTML = opts.tooltip(opts.cells[i], touch);
    }
    if (i < 0) {
      opts.tip.hidden = true;
      return;
    }
    const box = opts.tip.parentElement!.getBoundingClientRect();
    const canvas = map.getCanvas().getBoundingClientRect();
    opts.tip.style.left = `${Math.max(0, Math.min(canvas.left - box.left + x + 14, box.width - 220))}px`;
    opts.tip.style.top = `${canvas.top - box.top + y + 14}px`;
    opts.tip.hidden = false;
  };
  // The cell under the cursor by arithmetic: its r4 index, looked up, instead of querying rendered shapes.
  const index = new Map(opts.cells.map((c, i) => [c.h3, i]));
  const cellAt = (lngLat: { lng: number; lat: number }) =>
    index.get(latLngToCell(lngLat.lat, ((((lngLat.lng + 180) % 360) + 360) % 360) - 180, 4)) ?? -1;
  const open = (lon: number, lat: number) => {
    location.href = `archive.html#6/${lat.toFixed(3)}/${lon.toFixed(3)}`;
  };
  // At most one hover update per frame, however fast the mouse moves.
  let pending: { lng: number; lat: number; x: number; y: number } | null = null;
  map.on("mousemove", (e) => {
    const first = pending === null;
    pending = { lng: e.lngLat.lng, lat: e.lngLat.lat, x: e.point.x, y: e.point.y };
    if (!first) return;
    requestAnimationFrame(() => {
      const p = pending;
      pending = null;
      if (p) show(cellAt(p), p.x, p.y, false);
    });
  });
  map.on("mouseout", () => {
    pending = null;
    show(-1, 0, 0, false);
  });
  // Mouse: click opens. Touch has no hover: the first tap shows the cell, a second tap on it opens.
  map.on("click", (e) => {
    const touch = e.originalEvent instanceof PointerEvent && e.originalEvent.pointerType !== "mouse";
    const i = cellAt(e.lngLat);
    if (touch && i >= 0 && i !== hover) return show(i, e.point.x, e.point.y, true);
    const c = i >= 0 ? opts.cells[i] : null;
    if (c) {
      const lons = ring(c.h3).map(([lon]) => lon);
      const lats = ring(c.h3).map(([, lat]) => lat);
      return open(lons.reduce((a, b) => a + b) / lons.length, lats.reduce((a, b) => a + b) / lats.length);
    }
    open(e.lngLat.lng, e.lngLat.lat);
  });

  map.on("load", () => {
    paint(opts.color);
    spin();
  });
  return {
    map,
    recolor: paint,
    face: (center) => {
      stop();
      map.flyTo({ center, zoom: map.getZoom(), duration: 1600 });
    },
  };
}
