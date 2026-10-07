/** Full archive: every sounding DCDB has published, by place. */
import { $, frameChart, renderUpdated, renderColumns, renderHorizon, renderLadder, renderReadouts, renderReflection, setFavicon, table, wireLogbook, YELLOW } from "./chartroom";
import { loadSeries, type Series } from "./charts";
import { ARCHIVE_BINS, binColor, dupColor, uniqueColor, type RGBA } from "./colors";
import { LIS_BBOX } from "./config";
import { loadLis, renderLisSummary } from "./lis";
import { createMapView, whenLoaded } from "./mapview";
import { compact, loadMeta, type Meta } from "./metrics";
import { loadManifest, type Cell, type LayerManifest } from "./tiles";
import { column, loadRecent } from "./recentdata";

type Metric = "unique" | "vessels" | "dup";

const state = { metric: "unique" as Metric, manifest: null as LayerManifest | null, lis: null as Cell[] | null };

/** Color scale stretches to the densest cell typical of each resolution. */
const MAX_EXP: Record<number, number> = { 4: 8, 6: 7, 8: 6, 9: 5 };
const FIRST_MONTH = "2017-01"; // the census measures the community from 2017; earlier months are sparse

const view = createMapView({
  container: "map",
  basemap: "dark",
  storageKey: "csb-census:basemap:archive",
  pool: () => state.manifest?.tiles ?? [],
  color: (d, res) =>
    state.metric === "unique"
      ? uniqueColor(d.n_unique, MAX_EXP[res])
      : state.metric === "vessels"
        ? binColor(d.vessel_days ?? 0, ARCHIVE_BINS)
        : dupColor(d.dup_share ?? 0),
  colorKey: () => state.metric,
  tooltip: (c: Cell) => {
    const years = c.first_year === c.last_year ? `${c.first_year}` : `${c.first_year}-${c.last_year}`;
    return [
      `${compact(c.n_unique)} unique of ${compact(c.n_published)} published`,
      `${compact(c.vessel_days ?? 0)} vessel-days, ${compact(c.platforms ?? 0)} platforms`,
      `duplicate share ${(100 * (c.dup_share ?? 0)).toFixed(1)}%, collected ${years}`,
    ].join("\n");
  },
  extra: (zoom, hex) => (state.lis && zoom >= 9 ? [hex("lis-r9", state.lis, 9)] : []),
});

function ladder(): void {
  let steps: [RGBA, string][];
  let title: string;
  if (state.metric === "vessels") {
    const e = ARCHIVE_BINS.edges;
    steps = e.map((edge, i) => [binColor(edge, ARCHIVE_BINS), i === e.length - 1 ? `${compact(edge)}+` : compact(edge)]);
    title = "Vessel-days per cell · all time";
  } else if (state.metric === "unique") {
    steps = [0, 2, 4, 6, 7].map((k) => [uniqueColor(10 ** k, MAX_EXP[6]), k === 7 ? "10M+" : compact(10 ** k)]);
    title = "Unique soundings per cell · all time";
  } else {
    steps = [0, 0.25, 0.5, 0.75, 1].map((s) => [dupColor(s), `${s * 100}%`]);
    title = "Share published more than once";
  }
  renderLadder(title, steps);
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
    [compact(meta.vessel_days ?? 0), "Vessel-days", "one platform, one place, one day"],
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
      ["platforms active, 30 days", n(meta.platforms_active_30d ?? 0)],
      ["data through", `${meta.last_ingested?.slice(0, 16).replace("T", " ") ?? "?"} UTC`],
    ],
  );
}

async function main(): Promise<void> {
  ladder();
  wireLogbook();
  const [meta, manifest, series] = await Promise.all([loadMeta(), loadManifest(), loadSeries()]);
  state.manifest = manifest;
  band(meta, series);
  logbook(meta);
  renderUpdated(meta);
  loadRecent()
    .then((r) => setFavicon(column(r.batches, "unique").slice(-7).reverse()))
    .catch(() => undefined); // the static favicon stands

  for (const input of document.querySelectorAll<HTMLInputElement>('input[name="metric"]')) {
    input.addEventListener("change", () => {
      state.metric = input.value as Metric;
      ladder();
      void view.refresh();
    });
  }
  $("lis").addEventListener("click", () => view.map.fitBounds(LIS_BBOX, { padding: 24, duration: 1200 }));
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
