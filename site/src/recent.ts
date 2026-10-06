/** Recent activity: what NCEI published in the last day, week and month, and where boats have been. */
import "./style.css";

import { batchChart, dailyChart } from "./charts";
import { RECENT_BINS, binChips, binColor } from "./colors";
import { createMapView, whenLoaded } from "./mapview";
import { compact } from "./metrics";
import { column, epochs, hours, loadRecent, type Recent, type StripWindow, type WindowName } from "./recentdata";
import { loadManifest, type Cell, type LayerManifest } from "./tiles";

type MapWindow = "7d" | "30d" | "365d";
const WINDOW_TEXT: Record<MapWindow, string> = { "7d": "7 days", "30d": "30 days", "365d": "12 months" };

const state = { window: "30d" as MapWindow, manifest: null as LayerManifest | null };

const view = createMapView({
  container: "map",
  basemap: "dark",
  storageKey: "csb-census:basemap:recent",
  pool: () => state.manifest?.windows?.[state.window] ?? [],
  color: (d) => binColor(d.vessel_days, RECENT_BINS),
  colorKey: () => state.window,
  tooltip: (c: Cell) =>
    [
      `${c.vessel_days} vessel-day${c.vessel_days === 1 ? "" : "s"} in the last ${WINDOW_TEXT[state.window]}`,
      `${c.platforms} platform${c.platforms === 1 ? "" : "s"}`,
      `${compact(c.n_unique)} unique of ${compact(c.n_published)} soundings`,
    ].join("\n"),
});

function renderLegend(): void {
  document.getElementById("legend")!.innerHTML = `<div class="bins">${binChips(RECENT_BINS)}</div>
    <div class="ticks"><span>vessel-days per cell, collected in the last ${WINDOW_TEXT[state.window]}</span></div>`;
}

const STRIP_ROWS: [string, (w: StripWindow) => string][] = [
  ["Files", (w) => w.files.toLocaleString("en")],
  ["Soundings", (w) => compact(w.published)],
  ["Unique", (w) => compact(w.unique)],
  ["Platforms", (w) => w.platforms.toLocaleString("en")],
  ["New platforms", (w) => w.new_platforms.toLocaleString("en")],
  ["Provider labels", (w) => String(w.providers)],
  ["Median lag", (w) => hours(w.median_lag_h)],
];

function renderStrip(el: HTMLElement, r: Recent): void {
  const names: WindowName[] = ["24h", "7d", "30d", "all"];
  const head = `<tr><th></th>${names.map((n) => `<th>${n === "all" ? "All" : n.toUpperCase()}</th>`).join("")}</tr>`;
  const body = STRIP_ROWS.map(
    ([label, f]) => `<tr><th>${label}</th>${names.map((n) => `<td>${f(r.strip[n])}</td>`).join("")}</tr>`,
  ).join("");
  el.innerHTML = `<table class="strip"><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

function renderLag(el: HTMLElement, rows: [string, number][]): void {
  const max = Math.max(1, ...rows.map(([, n]) => n));
  el.innerHTML = rows
    .map(
      ([name, n]) => `<div class="hbar"><span class="hbar-label">${name}</span>
        <span class="hbar-track"><span class="hbar-fill" style="width:${(100 * n) / max}%"></span></span>
        <span class="hbar-value">${n.toLocaleString("en")}</span></div>`,
    )
    .join("");
}

function renderRuns(el: HTMLElement, r: Recent): void {
  const t = r.runs;
  if (!t.rows.length) {
    el.textContent = "No runs recorded yet.";
    return;
  }
  const at = (name: string) => t.columns.indexOf(name);
  const rows = t.rows.slice(0, 12).map((row) => {
    const kind = String(row[at("kind")]);
    const when = String(row[at("run_at")]).replace("T", " ");
    const detail =
      kind === "reconcile"
        ? `${row[at("removed")]} removed`
        : `${row[at("new_files")]} files, ${compact(Number(row[at("n_unique")]))} unique`;
    return `<tr><td>${when}</td><td>${kind}</td><td>${detail}</td></tr>`;
  });
  el.innerHTML = `<table class="runs"><tbody>${rows.join("")}</tbody></table>`;
}

const nonzero = (xs: number[]) => xs.map((v) => (v > 0 ? v : null)) as unknown as number[];

async function main(): Promise<void> {
  renderLegend();
  const [recent, manifest] = await Promise.all([loadRecent(), loadManifest()]);
  state.manifest = manifest;
  renderStrip(document.getElementById("strip")!, recent);

  const b = recent.batches;
  batchChart(document.getElementById("batches")!, epochs(b, "batch"), column(b, "published"), column(b, "unique"));

  const draw = dailyChart(document.getElementById("daily")!);
  const showDaily = (clock: "pub" | "coll") => {
    const t = clock === "pub" ? recent.daily_pub : recent.daily_coll;
    draw(epochs(t, "day"), nonzero(column(t, "published")), nonzero(column(t, "unique")), column(t, "platforms"));
  };
  showDaily("pub");
  for (const input of document.querySelectorAll<HTMLInputElement>('input[name="clock"]')) {
    input.addEventListener("change", () => showDaily(input.value as "pub" | "coll"));
  }

  const lagEl = document.getElementById("lag")!;
  renderLag(lagEl, recent.lag_hist.last_30d);
  for (const input of document.querySelectorAll<HTMLInputElement>('input[name="lag"]')) {
    input.addEventListener("change", () =>
      renderLag(lagEl, input.value === "all" ? recent.lag_hist.all : recent.lag_hist.last_30d),
    );
  }
  renderRuns(document.getElementById("runs")!, recent);
  document.getElementById("built")!.textContent = `Windows end ${recent.now.replace("T", " ")} UTC.`;

  for (const input of document.querySelectorAll<HTMLInputElement>('input[name="window"]')) {
    input.addEventListener("change", () => {
      state.window = input.value as MapWindow;
      renderLegend();
      void view.refresh();
    });
  }
  whenLoaded(view.map, () => void view.refresh());
}

main().catch((err: unknown) => {
  console.error(err);
  document.getElementById("strip")!.textContent = "Recent activity could not be loaded.";
});
