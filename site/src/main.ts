import { H3HexagonLayer } from "@deck.gl/geo-layers";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { AttributionControl, Map as MapLibreMap, NavigationControl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";

import { DATA_BEFORE, style, watchBasemap } from "./basemap";
import { loadSeries, renderChart } from "./charts";
import { dupColor, legendGradient, uniqueColor } from "./colors";
import { LIS_BBOX, RES_FOR_ZOOM, SHOW_PROVIDERS } from "./config";
import { loadLis, renderLisSummary } from "./lis";
import { compact, loadMeta, renderMetrics } from "./metrics";
import { cellsFor, loadManifest, visibleTiles, type Cell, type LayerManifest, type Tile } from "./tiles";

type Metric = "unique" | "dup";

const state = {
  metric: "unique" as Metric,
  provider: "",
  lis: null as Cell[] | null,
  manifest: null as LayerManifest | null,
  request: 0,
};

const map = new MapLibreMap({
  container: "map",
  style,
  center: [-40, 30],
  zoom: 1.6,
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

function tooltip(c: Cell): string {
  const years = c.first_year === c.last_year ? `${c.first_year}` : `${c.first_year}-${c.last_year}`;
  return [
    `${compact(c.n_unique)} unique of ${compact(c.n_published)} published`,
    `duplicate share ${(100 * c.dup_share).toFixed(1)}%`,
    `${c.n_providers} provider${c.n_providers === 1 ? "" : "s"}, collected ${years}`,
  ].join("\n");
}

/** Colour scale stretches to the densest cell typical of each resolution. */
const MAX_EXP: Record<number, number> = { 4: 8, 6: 7, 8: 6, 9: 5 };

// MapboxOverlay (interleaved) reads `beforeId` from layer props; deck's base layer types omit it.
const UNDER_LABELS = { beforeId: DATA_BEFORE } as object;

function hexLayer(id: string, data: Cell[], res: number) {
  return new H3HexagonLayer<Cell>({
    ...UNDER_LABELS,
    id,
    data,
    getHexagon: (d) => d.h3,
    getFillColor: (d) => (state.metric === "unique" ? uniqueColor(d.n_unique, MAX_EXP[res]) : dupColor(d.dup_share)),
    extruded: false,
    stroked: false,
    highPrecision: res >= 8,
    pickable: true,
    updateTriggers: { getFillColor: [state.metric] },
  });
}

function currentTiles(): Tile[] {
  const m = state.manifest;
  if (!m) return [];
  if (state.provider && m.providers?.[state.provider]) return m.providers[state.provider];
  return m.tiles;
}

async function refresh(): Promise<void> {
  if (!state.manifest) return;
  const request = ++state.request;
  const zoom = map.getZoom();
  const b = map.getBounds();
  const view: [number, number, number, number] = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  let res: number = RES_FOR_ZOOM(zoom);
  const pool = currentTiles();
  if (!pool.some((t) => t.res === res)) res = Math.max(...pool.map((t) => t.res).filter((r) => r <= res), 4);
  const cells = await cellsFor(visibleTiles(pool, res, view));
  if (request !== state.request) return; // a newer view superseded this one
  const layers = [hexLayer(`hex-r${res}`, cells, res)];
  if (state.lis && zoom >= 9 && !state.provider) layers.push(hexLayer("lis-r9", state.lis, 9));
  overlay.setProps({
    layers,
    getTooltip: ({ object }) => (object ? { text: tooltip(object as Cell) } : null),
  });
}

function renderLegend(): void {
  const el = document.getElementById("legend")!;
  const [lo, hi] = state.metric === "unique" ? ["1", "10M+"] : ["0%", "100%"];
  el.innerHTML = `<div class="ramp" style="background:${legendGradient(state.metric)}"></div>
    <div class="ticks"><span>${lo}</span><span>${state.metric === "unique" ? "unique soundings per cell (log)" : "duplicate share"}</span><span>${hi}</span></div>`;
}

let timer = 0;
map.on("moveend", () => {
  window.clearTimeout(timer);
  timer = window.setTimeout(() => void refresh(), 120);
});

for (const input of document.querySelectorAll<HTMLInputElement>('input[name="metric"]')) {
  input.addEventListener("change", () => {
    state.metric = input.value as Metric;
    renderLegend();
    void refresh();
  });
}

document.getElementById("lis")!.addEventListener("click", () => {
  map.fitBounds(LIS_BBOX, { padding: 24, duration: 1200 });
});

async function main(): Promise<void> {
  renderLegend();
  const [meta, manifest, series] = await Promise.all([loadMeta(), loadManifest(), loadSeries()]);
  state.manifest = manifest;
  renderMetrics(document.getElementById("metrics")!, meta);
  renderChart(document.getElementById("chart")!, series.community);
  document.getElementById("built")!.textContent =
    `Data through ${meta.last_ingested?.slice(0, 16).replace("T", " ") ?? "?"} UTC, state generation ${meta.generation}.`;

  if (SHOW_PROVIDERS && manifest.providers) {
    const row = document.getElementById("provider-row")!;
    const select = document.getElementById("provider") as HTMLSelectElement;
    for (const name of Object.keys(manifest.providers)) select.add(new Option(name, name));
    row.hidden = false;
    select.addEventListener("change", () => {
      state.provider = select.value;
      const rows = state.provider ? series.providers?.[state.provider] : undefined;
      renderChart(document.getElementById("chart")!, rows ?? series.community);
      void refresh();
    });
  }

  const lis = await loadLis();
  if (lis) {
    state.lis = lis.cells;
    const el = document.getElementById("lis-summary")!;
    renderLisSummary(el, lis.summary);
    el.hidden = false;
  }
  if (map.loaded()) void refresh();
  else map.once("load", () => void refresh());
}

main().catch((err: unknown) => {
  console.error(err);
  document.getElementById("metrics")!.textContent = "Census data could not be loaded.";
});
