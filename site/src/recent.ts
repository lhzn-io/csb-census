/** Recent activity: what NCEI published lately, and where boats have been. */
import { $, frameChart, renderUpdated, renderColumns, renderHorizon, renderLadder, renderReadouts, renderReflection, setFavicon, table, wireLogbook, YELLOW } from "./chartroom";
import { AGE_STEPS, RECENT_BINS, binColor, freshColor, type RGBA } from "./colors";
import { createMapView, whenLoaded } from "./mapview";
import { compact, loadMeta } from "./metrics";
import { column, epochs, hours, loadRecent, type Recent, type WindowName } from "./recentdata";
import { loadManifest, type Cell, type LayerManifest } from "./tiles";

/** "24h" is by publication (what NCEI released); the others are by collection day. */
type MapWindow = "24h" | "7d" | "30d" | "365d";
const WINDOW_TEXT: Record<MapWindow, string> = {
  "24h": "published in the last 24 hours",
  "7d": "collected in the last 7 days",
  "30d": "collected in the last 30 days",
  "365d": "collected in the last 12 months",
};
/** One soundings scale for every level of the 24h map, so the legend holds at any zoom. */
const MAX_EXP_24H = 5;

const state = {
  window: "24h" as MapWindow,
  fade: true,
  manifest: null as LayerManifest | null,
  recent: null as Recent | null,
};

function collectedText(c: Cell): string {
  if (!c.first_day) return "no valid collection date";
  return c.first_day === c.last_day ? `collected ${c.first_day}` : `collected ${c.first_day} to ${c.last_day}`;
}

const view = createMapView({
  container: "map",
  basemap: "dark",
  storageKey: "csb-census:basemap:recent",
  pool: () => state.manifest?.windows?.[state.window] ?? [],
  color: (d) =>
    state.window === "24h" ? freshColor(d.n_unique, MAX_EXP_24H, d.mean_age_d, state.fade) : binColor(d.vessel_days ?? 0, RECENT_BINS),
  colorKey: () => `${state.window}:${state.fade}`,
  tooltip: (c: Cell) =>
    (state.window === "24h"
      ? [
          `${compact(c.n_unique)} unique of ${compact(c.n_published)} soundings, ${WINDOW_TEXT["24h"]}`,
          `${c.platforms} platform${c.platforms === 1 ? "" : "s"}`,
          collectedText(c),
        ]
      : [
          `${c.vessel_days} vessel-day${c.vessel_days === 1 ? "" : "s"}, ${WINDOW_TEXT[state.window]}`,
          `${c.platforms} platform${c.platforms === 1 ? "" : "s"}`,
          `${compact(c.n_unique)} unique of ${compact(c.n_published)} soundings`,
        ]
    ).join("\n"),
});

/** The 24h fade key, each step with its share of the release (undated soundings count with the oldest). */
function fadeKey(): { title: string; steps: [RGBA, string][] } {
  const got = new Map(state.recent?.last24h?.collected ?? []);
  const total = [...got.values()].reduce((a, b) => a + b, 0);
  const share = (labels: string[]): string => {
    if (!total) return "";
    const n = labels.reduce((a, l) => a + (got.get(l) ?? 0), 0);
    const pct = (100 * n) / total;
    return ` <span class="pct">${pct > 0 && pct < 1 ? "<1" : Math.round(pct)}%</span>`;
  };
  const [r, g, b] = freshColor(1000, MAX_EXP_24H, 0, true);
  const toggle = `<button type="button" class="ladder-toggle" id="fade-toggle" aria-pressed="${state.fade}">Fade ${state.fade ? "on" : "off"}</button>`;
  return {
    title: `Collected ${toggle}`,
    steps: AGE_STEPS.map((s, i) => [
      [r, g, b, state.fade ? s.alpha : 235] as RGBA, // with the fade off, the map (and so the key) is solid
      `${s.label}${share(i === AGE_STEPS.length - 1 ? [s.label, "no date"] : [s.label])}`,
    ]),
  };
}

function ladder(): void {
  if (state.window === "24h") {
    const steps: [RGBA, string][] = [0, 1, 2, 3, 4, 5].map((k) => [
      freshColor(10 ** k, MAX_EXP_24H, 0, true),
      k === MAX_EXP_24H ? `${compact(10 ** k)}+` : compact(10 ** k),
    ]);
    renderLadder(`Unique soundings per cell · ${WINDOW_TEXT["24h"]}`, steps, fadeKey());
    return;
  }
  const e = RECENT_BINS.edges;
  renderLadder(
    `Vessel-days per cell · ${WINDOW_TEXT[state.window]}`,
    e.map((edge, i) => [binColor(edge, RECENT_BINS), i === e.length - 1 ? `${edge}+` : String(edge)]),
  );
}

function horizon(r: Recent, clock: "pub" | "coll"): void {
  const t = clock === "pub" ? r.daily_pub : r.daily_coll;
  const x = epochs(t, "day");
  const pub = column(t, "published");
  const plat = column(t, "platforms");
  renderHorizon({
    x,
    line: pub,
    faint: pub,
    smooth: 7,
    ticks: "months",
    shadeFrom: clock === "coll" ? Date.parse(r.now) / 1000 - 30 * 86400 : undefined,
    shadeLabel: "still arriving",
    hover: (i) => `${new Date(x[i] * 1000).toISOString().slice(0, 10)}  ${compact(pub[i])} soundings  ${plat[i]} platforms`,
  });
}

function logbook(r: Recent): void {
  const names: WindowName[] = ["24h", "7d", "30d", "all"];
  const s = r.strip;
  $("strip").innerHTML = table(
    ["", ...names.map((n) => n.toUpperCase())],
    [
      ["files", ...names.map((n) => s[n].files.toLocaleString("en"))],
      ["soundings", ...names.map((n) => compact(s[n].published))],
      ["unique", ...names.map((n) => compact(s[n].unique))],
      ["platforms", ...names.map((n) => s[n].platforms.toLocaleString("en"))],
      ["new", ...names.map((n) => s[n].new_platforms.toLocaleString("en"))],
      ["providers", ...names.map((n) => String(s[n].providers))],
      ["median lag", ...names.map((n) => hours(s[n].median_lag_h))],
    ],
  );
  const lag = r.lag_hist.last_30d;
  const max = Math.max(1, ...lag.map(([, n]) => n));
  $("lag").innerHTML = lag
    .map(([b, n]) => `<div class="lagrow"><span>${b}</span><span class="lagbar"><span style="width:${(100 * n) / max}%"></span></span><span class="v">${n.toLocaleString("en")}</span></div>`)
    .join("");
  const t = r.runs;
  const at = (c: string) => t.columns.indexOf(c);
  $("runs").innerHTML = t.rows.length
    ? table([], t.rows.slice(0, 10).map((row) => [String(row[at("run_at")]).replace("T", " "), String(row[at("kind")]), `${row[at("new_files")]} files`]))
    : `<p class="note">No runs recorded yet.</p>`;
}

async function main(): Promise<void> {
  ladder();
  wireLogbook();
  const [recent, manifest, meta] = await Promise.all([loadRecent(), loadManifest(), loadMeta()]);
  state.manifest = manifest;
  state.recent = recent;
  ladder();
  const batches = recent.batches;
  const last7 = column(batches, "unique").slice(-7).reverse();
  renderReflection(last7, "unique soundings, last 7 batches");
  setFavicon(last7);
  const d = recent.strip["24h"];
  const w = recent.strip["7d"];
  renderReadouts([
    [compact(d.published), "Soundings", "published in the last 24 hours"],
    [w.platforms.toLocaleString("en"), "Platforms", `active this week, <em>${w.new_platforms}</em> new`],
    [compact(w.unique), "Unique", "soundings published this week"],
    [hours(w.median_lag_h), "Median lag", "collection to publication, this week"],
  ]);
  horizon(recent, "pub");
  const pub = column(batches, "published");
  const uni = column(batches, "unique");
  renderColumns(pub.map((p, i) => [uni[i], Math.max(0, p - uni[i])]), [YELLOW, "rgba(255,255,255,0.35)"]);
  logbook(recent);
  renderUpdated(meta);

  for (const input of document.querySelectorAll<HTMLInputElement>('input[name="clock"]')) {
    input.addEventListener("change", () => horizon(recent, input.value as "pub" | "coll"));
  }
  for (const input of document.querySelectorAll<HTMLInputElement>('input[name="window"]')) {
    input.addEventListener("change", () => {
      state.window = input.value as MapWindow;
      ladder();
      void view.refresh();
    });
  }
  // The fade switch lives in the legend, which is redrawn on every change, so listen on the legend itself.
  $("ladder").addEventListener("click", (e) => {
    if (!(e.target instanceof HTMLElement) || e.target.id !== "fade-toggle") return;
    state.fade = !state.fade;
    ladder();
    void view.refresh();
  });
  whenLoaded(view.map, () => {
    frameChart(view.map);
    void view.refresh();
  });
}

main().catch((err: unknown) => {
  console.error(err);
  $("readouts").textContent = "Recent activity could not be loaded.";
});
