/** Recent activity: what NCEI published lately, and where boats have been. */
import { $, frameChart, renderColumns, renderHorizon, renderLadder, renderReadouts, renderReflection, table, wireLogbook, YELLOW } from "./chartroom";
import { RECENT_BINS, binColor } from "./colors";
import { createMapView, whenLoaded } from "./mapview";
import { compact } from "./metrics";
import { column, epochs, hours, loadRecent, type Recent, type WindowName } from "./recentdata";
import { loadManifest, type Cell, type LayerManifest } from "./tiles";

type MapWindow = "7d" | "30d" | "365d";
const WINDOW_TEXT: Record<MapWindow, string> = { "7d": "last 7 days", "30d": "last 30 days", "365d": "last 12 months" };

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
      `${c.vessel_days} vessel-day${c.vessel_days === 1 ? "" : "s"}, ${WINDOW_TEXT[state.window]}`,
      `${c.platforms} platform${c.platforms === 1 ? "" : "s"}`,
      `${compact(c.n_unique)} unique of ${compact(c.n_published)} soundings`,
    ].join("\n"),
});

function ladder(): void {
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
  const [recent, manifest] = await Promise.all([loadRecent(), loadManifest()]);
  state.manifest = manifest;
  const batches = recent.batches;
  renderReflection(column(batches, "unique").slice(-7).reverse(), "unique soundings, last 7 batches");
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
  // Keep the note's link; add when the windows end.
  $("mast-note").append(` Windows end ${recent.now.slice(0, 16).replace("T", " ")} UTC.`);

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
  whenLoaded(view.map, () => {
    frameChart(view.map);
    void view.refresh();
  });
}

main().catch((err: unknown) => {
  console.error(err);
  $("readouts").textContent = "Recent activity could not be loaded.";
});
