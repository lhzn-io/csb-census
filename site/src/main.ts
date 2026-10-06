/** Full archive page: every sounding DCDB has published, by cell. */
import "./style.css";

import { loadSeries, renderChart } from "./charts";
import { ARCHIVE_BINS, binChips, binColor, dupColor, legendGradient, uniqueColor } from "./colors";
import { LIS_BBOX, SHOW_PROVIDERS } from "./config";
import { loadLis, renderLisSummary } from "./lis";
import { createMapView, whenLoaded } from "./mapview";
import { compact, loadMeta, renderMetrics } from "./metrics";
import { loadManifest, type Cell, type LayerManifest } from "./tiles";

type Metric = "unique" | "vessels" | "dup";

const state = {
  metric: "unique" as Metric,
  provider: "",
  lis: null as Cell[] | null,
  manifest: null as LayerManifest | null,
};

/** Colour scale stretches to the densest cell typical of each resolution. */
const MAX_EXP: Record<number, number> = { 4: 8, 6: 7, 8: 6, 9: 5 };

function tooltip(c: Cell): string {
  const years = c.first_year === c.last_year ? `${c.first_year}` : `${c.first_year}-${c.last_year}`;
  return [
    `${compact(c.n_unique)} unique of ${compact(c.n_published)} published`,
    `${compact(c.vessel_days ?? 0)} vessel-days, ${compact(c.platforms ?? 0)} platform${c.platforms === 1 ? "" : "s"}`,
    `duplicate share ${(100 * (c.dup_share ?? 0)).toFixed(1)}%`,
    `${c.n_providers} provider${c.n_providers === 1 ? "" : "s"}, collected ${years}`,
  ].join("\n");
}

const view = createMapView({
  container: "map",
  basemap: "dark",
  storageKey: "csb-census:basemap:archive",
  pool: () => {
    const m = state.manifest;
    if (!m) return [];
    if (state.provider && m.providers?.[state.provider]) return m.providers[state.provider];
    return m.tiles;
  },
  color: (d, res) =>
    state.metric === "unique"
      ? uniqueColor(d.n_unique, MAX_EXP[res])
      : state.metric === "vessels"
        ? binColor(d.vessel_days ?? 0, ARCHIVE_BINS)
        : dupColor(d.dup_share ?? 0),
  colorKey: () => state.metric,
  tooltip,
  extra: (zoom, hex) => (state.lis && zoom >= 9 && !state.provider ? [hex("lis-r9", state.lis, 9)] : []),
});

function renderLegend(): void {
  const el = document.getElementById("legend")!;
  if (state.metric === "vessels") {
    el.innerHTML = `<div class="bins">${binChips(ARCHIVE_BINS)}</div>
      <div class="ticks"><span>${ARCHIVE_BINS.label}: distinct (platform, collection day) pairs</span></div>`;
    return;
  }
  const [lo, hi] = state.metric === "unique" ? ["1", "10M+"] : ["0%", "100%"];
  const kind = state.metric === "unique" ? "unique" : "dup";
  el.innerHTML = `<div class="ramp" style="background:${legendGradient(kind)}"></div>
    <div class="ticks"><span>${lo}</span><span>${kind === "unique" ? "unique soundings per cell (log)" : "duplicate share"}</span><span>${hi}</span></div>`;
}

for (const input of document.querySelectorAll<HTMLInputElement>('input[name="metric"]')) {
  input.addEventListener("change", () => {
    state.metric = input.value as Metric;
    renderLegend();
    void view.refresh();
  });
}

document.getElementById("lis")!.addEventListener("click", () => {
  view.map.fitBounds(LIS_BBOX, { padding: 24, duration: 1200 });
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
      void view.refresh();
    });
  }

  const lis = await loadLis();
  if (lis) {
    state.lis = lis.cells;
    const el = document.getElementById("lis-summary")!;
    renderLisSummary(el, lis.summary);
    el.hidden = false;
  }
  whenLoaded(view.map, () => void view.refresh());
}

main().catch((err: unknown) => {
  console.error(err);
  document.getElementById("metrics")!.textContent = "Census data could not be loaded.";
});
