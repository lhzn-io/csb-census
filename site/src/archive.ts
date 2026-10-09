/** Full archive: every sounding DCDB has published, by place. */
import { $, frameChart, measureSwitch, renderUpdated, renderColumns, renderHorizon, renderLadder, renderReadouts, renderReflection, setFavicon, table, wireAbout, wireLogbook, wireMeasureSwitch, YELLOW, type Measure } from "./chartroom";
import { loadSeries, type Series } from "./charts";
import { ARCHIVE_BINS, binColor, dupColor, duration, reachBins, reachColor, underwayColor, uniqueColor, type RGBA } from "./colors";
import { loadLis, renderLisSummary } from "./lis";
import { wireBookmarks } from "./bookmarks";
import { createMapView, whenLoaded } from "./mapview";
import { compact, loadMeta, type Meta } from "./metrics";
import { loadFineTiles, loadManifest, type Cell, type LayerManifest, type Tile } from "./tiles";
import { column, loadRecent } from "./recentdata";

type Metric = "unique" | "traffic" | "reach" | "dup";

const state = {
  metric: "unique" as Metric,
  measure: "underway" as Measure,
  manifest: null as LayerManifest | null,
  lis: null as Cell[] | null,
  fine: null as Promise<Tile[]> | null,
  fineTiles: [] as Tile[],
};

/** Fetch the finest level's index the first time the map is zoomed in far enough to use it. */
function wantFine(): void {
  const fine = state.manifest?.fine;
  if (!fine || state.fine || view.map.getZoom() < 10) return;
  state.fine = loadFineTiles(fine.index);
  state.fine
    .then((tiles) => {
      state.fineTiles = tiles;
      void view.refresh();
    })
    .catch((err: unknown) => console.error(err));
}

/** Color scales stretch to the densest cell typical of each resolution (soundings; underway minutes). */
const MAX_EXP: Record<number, number> = { 4: 8, 6: 7, 8: 6, 9: 5 };
const UW_EXP: Record<number, number> = { 4: 5, 6: 4, 8: 3, 9: 2.5 };
const FIRST_MONTH = "2017-01"; // the census measures the community from 2017; earlier months are sparse
const REACH = reachBins();

function color(d: Cell, res: number): RGBA {
  switch (state.metric) {
    case "unique":
      return uniqueColor(d.n_unique, MAX_EXP[res]);
    case "traffic":
      return state.measure === "underway"
        ? underwayColor(d.underway_h ?? 0, UW_EXP[res] ?? 3)
        : binColor(d.vessel_days ?? 0, ARCHIVE_BINS);
    case "reach":
      return reachColor(d.first_month, REACH);
    default:
      return dupColor(d.dup_share ?? 0);
  }
}

const view = createMapView({
  container: "map",
  basemap: "dark",
  storageKey: "csb-census:basemap:archive",
  // Traffic stops at r8, where vessel-days and underway time are measured; the other maps reach r9.
  pool: () => {
    wantFine();
    return [...(state.manifest?.tiles ?? []), ...state.fineTiles].filter((t) => state.metric !== "traffic" || t.res <= 8);
  },
  color,
  colorKey: () => `${state.metric}:${state.measure}`,
  tooltip: (c: Cell) => {
    const years = c.first_year === c.last_year ? `${c.first_year}` : `${c.first_year}-${c.last_year}`;
    // r9 cells carry soundings only; the traffic lines show where traffic was measured (r8 and coarser).
    const traffic =
      c.vessel_days === undefined
        ? []
        : [
            `${duration((c.underway_h ?? 0) * 60)} underway, ${duration((c.stationary_h ?? 0) * 60)} stationary`,
            `${compact(c.vessel_days)} vessel-days, ${compact(c.platforms ?? 0)} platforms`,
          ];
    return [
      `${compact(c.n_unique)} unique of ${compact(c.n_published)} published`,
      ...traffic,
      `first covered ${c.first_month ?? "?"}, collected ${years}`,
      `duplicate share ${(100 * (c.dup_share ?? 0)).toFixed(1)}%`,
    ].join("\n");
  },
  extra: (zoom, hex) => (state.lis && zoom >= 9 ? [hex("lis-r9", state.lis, 9)] : []),
});

function ladder(): void {
  let steps: [RGBA, string][];
  let title: string;
  let extra: ReturnType<typeof measureSwitch> | undefined;
  if (state.metric === "traffic" && state.measure === "vessels") {
    const e = ARCHIVE_BINS.edges;
    steps = e.map((edge, i) => [binColor(edge, ARCHIVE_BINS), i === e.length - 1 ? `${compact(edge)}+` : compact(edge)]);
    title = "Vessel-days per cell · all time";
    extra = measureSwitch(state.measure);
  } else if (state.metric === "traffic") {
    steps = [0, 1, 2, 3, 4].map((k) => [underwayColor(10 ** k / 60, UW_EXP[6]), k === 4 ? `${duration(10 ** k)}+` : duration(10 ** k)]);
    title = "Underway time per cell · all time";
    extra = measureSwitch(state.measure);
  } else if (state.metric === "reach") {
    steps = REACH.map((b) => [reachColor(Number.isFinite(b.from) ? `${b.from}-01` : null, REACH), b.label]);
    title = "First covered · new ground by year";
  } else if (state.metric === "unique") {
    steps = [0, 2, 4, 6, 7].map((k) => [uniqueColor(10 ** k, MAX_EXP[6]), k === 7 ? "10M+" : compact(10 ** k)]);
    title = "Unique soundings per cell · all time";
  } else {
    steps = [0, 0.25, 0.5, 0.75, 1].map((s) => [dupColor(s), `${s * 100}%`]);
    title = "Share published more than once";
  }
  renderLadder(title, steps, extra);
}

function byYear(rows: Series["community"]): [string, number, number, number][] {
  const years = new Map<string, [number, number, number]>();
  for (const [month, , unique, resend, cross] of rows) {
    if (month < FIRST_MONTH) continue;
    const y = month.slice(0, 4);
    const acc = years.get(y) ?? [0, 0, 0];
    years.set(y, [acc[0] + unique, acc[1] + resend, acc[2] + cross]);
  }
  return [...years.entries()].map(([y, [u, r, c]]) => [y, u, r, c]);
}

function band(meta: Meta, series: Series): void {
  renderReadouts([
    [compact(meta.published), "Soundings", `published in ${meta.files.toLocaleString("en")} files`],
    [compact(meta.unique), "Unique", `<em>${((100 * meta.unique) / meta.published).toFixed(0)}%</em> of published`],
    [compact(meta.vessel_days ?? 0), "Vessel-days", underwayShare(meta)],
    [meta.platforms.toLocaleString("en"), "Platforms", `IDs under ${meta.providers} provider labels`],
  ]);
  const rows = series.community.filter((r) => r[0] >= FIRST_MONTH);
  const x = rows.map((r) => Date.UTC(Number(r[0].slice(0, 4)), Number(r[0].slice(5, 7)) - 1, 1) / 1000);
  const published = rows.map((r) => r[1]);
  const unique = rows.map((r) => r[2]);
  renderHorizon({
    x,
    line: unique,
    faint: published,
    ticks: "years",
    hover: (i) =>
      `${rows[i][0]}  ${compact(published[i])} published  ${compact(unique[i])} unique (${((100 * unique[i]) / Math.max(1, published[i])).toFixed(0)}%)`,
  });
  const years = byYear(series.community);
  renderColumns(
    years.map(([, u, r, c]) => [u, r, c]),
    [YELLOW, "rgba(255,255,255,0.35)", "rgba(255,255,255,0.14)"],
    { normalize: true, labels: years.map(([y]) => `’${y.slice(2)}`) },
  );
  renderReflection(years.map(([, u]) => u).reverse().slice(0, 7), "unique soundings, last 7 years");
}

/** The vessel-days readout's sub-line: how many of the logged platform-days got underway. */
function underwayShare(meta: Meta): string {
  if (meta.share_never_underway == null) return "one platform, one place, one day";
  return `<em>${Math.round(100 * (1 - meta.share_never_underway))}%</em> of them got underway`;
}

/** Reach by collection year: underway hours, r8 cells first covered, and new cells per underway hour. */
function reachTable(series: Series): string {
  const years = new Map<string, [number, number]>();
  for (const [month, hours, cells] of series.reach?.rows ?? []) {
    if (month < FIRST_MONTH) continue;
    const acc = years.get(month.slice(0, 4)) ?? [0, 0];
    years.set(month.slice(0, 4), [acc[0] + hours, acc[1] + cells]);
  }
  const rows = [...years.entries()].map(([y, [h, c]]) => [y, compact(h), compact(c), h ? (c / h).toFixed(1) : "-"]);
  return rows.length ? table(["year", "underway h", "new cells", "per hour"], rows) : `<p class="note">Not measured yet.</p>`;
}

function logbook(meta: Meta): void {
  const n = (v: number) => v.toLocaleString("en");
  $("totals").innerHTML = table(
    [],
    [
      ["soundings published", n(meta.published)],
      ["unique", n(meta.unique)],
      ["resent, same platform ID", n(meta.dup_resend)],
      ["repeated, other platform ID", n(meta.dup_cross_id)],
      ["files", n(meta.files)],
      ["platform IDs", n(meta.platforms)],
      ["provider labels", String(meta.providers)],
      ["vessel-days", n(meta.vessel_days ?? 0)],
      ["hours underway", n(meta.underway_hours ?? 0)],
      ["hours stationary", n(meta.stationary_hours ?? 0)],
      ["platform-days never underway", n(meta.platform_days_never_underway ?? 0)],
      ["platforms active, 30 days", n(meta.platforms_active_30d ?? 0)],
      ["data through", `${meta.last_ingested?.slice(0, 16).replace("T", " ") ?? "?"} UTC`],
    ],
  );
}

async function main(): Promise<void> {
  ladder();
  wireLogbook();
  wireBookmarks(view.map);
  wireAbout();
  const [meta, manifest, series] = await Promise.all([loadMeta(), loadManifest(), loadSeries()]);
  state.manifest = manifest;
  band(meta, series);
  logbook(meta);
  $("reach").innerHTML = reachTable(series);
  renderUpdated(meta);
  loadRecent()
    .then((r) => setFavicon(column(r.batches, "unique").slice(-7).reverse()))
    .catch(() => undefined); // the static favicon stands

  wireMeasureSwitch((m) => {
    state.measure = m;
    ladder();
    void view.refresh();
  });
  for (const input of document.querySelectorAll<HTMLInputElement>('input[name="metric"]')) {
    input.addEventListener("change", () => {
      state.metric = input.value as Metric;
      ladder();
      void view.refresh();
    });
  }
  whenLoaded(view.map, () => {
    frameChart(view.map);
    void view.refresh();
  });

  const lis = await loadLis();
  if (lis) {
    state.lis = lis.cells;
    renderLisSummary($("lis-summary"), lis.summary);
    $("lis-section").hidden = false;
  }
}

main().catch((err: unknown) => {
  console.error(err);
  $("readouts").textContent = "Census data could not be loaded.";
});
