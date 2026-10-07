/** Landing page: the whole archive on the world-ocean square, with the ways in. */
import { $, renderLadder, renderReflection, renderUpdated, setFavicon } from "./chartroom";
import { ARCHIVE_BINS, binColor, uniqueColor, type RGBA } from "./colors";
import { DATA } from "./config";
import { compact, loadMeta } from "./metrics";
import { hours, loadRecent } from "./recentdata";

interface Land {
  size: number;
  runs: number[][];
}
interface Cells {
  u: number[];
  v: number[];
  lat: number[];
  lon: number[];
  unique: number[];
  published: number[];
  vessel_days: number[];
  platforms: number[];
  first_year: number[];
  last_year: number[];
}
type Metric = "unique" | "vessels";

const state = {
  metric: "unique" as Metric,
  center: "spilhaus",
  land: null as HTMLCanvasElement | null,
  cells: null as Cells | null,
  nearest: null as ((u: number, v: number, maxDist: number) => number) | null,
  hover: -1,
};
/** Where each view is centered; only the classic one keeps every edge of the square on land. */
const CENTERS: Record<string, string> = {
  spilhaus: "",
  americas: "centered 15°N 75°W",
  atlantic: "centered 40°N 45°W",
  pacific: "centered 15°N 165°W",
};
const CENTER_KEY = "csb-census:center";
const SPILHAUS = "https://en.wikipedia.org/wiki/Athelstan_Spilhaus";
const SPILHAUS_MAP = "https://storymaps.arcgis.com/stories/756bcae18d304a1eac140f19f4d5cb3d";

/**
 * A first-visit default near the visitor, read from the browser's own time zone (no IP lookup, no
 * request). The classic view is centered on the southern Indian Ocean, so it goes to the Indian Ocean
 * rim and Antarctica; the Americas, the Atlantic side (Europe, Africa) and the Pacific side get theirs.
 */
function nearestCenter(): string {
  let zone = "";
  try {
    zone = Intl.DateTimeFormat().resolvedOptions().timeZone ?? "";
  } catch {
    return "spilhaus";
  }
  const [region] = zone.split("/");
  const offsetHours = -new Date().getTimezoneOffset() / 60;
  if (region === "America") return "americas";
  if (region === "Europe" || region === "Africa" || region === "Atlantic" || region === "Arctic") return "atlantic";
  if (region === "Pacific") return "pacific";
  if (region === "Indian" || region === "Antarctica" || zone === "Australia/Perth") return "spilhaus";
  if (region === "Asia") return offsetHours <= 6.5 ? "spilhaus" : "pacific"; // west Asia and India face the Indian Ocean
  if (region === "Australia") return "pacific";
  return "spilhaus";
}
/** Recentered views run open ocean into the square's edges; fade the last few percent into the page. */
const FADE = 0.07;
const MAX_EXP = 8; // H3 r4: the densest cells hold around 10^8 soundings
const OCEAN_IN = "#2b4457";
const OCEAN_OUT = "#16232d";
const LAND = "#111111"; // the page background: land disappears, leaving only the ocean and the data

/** Census data lives under DATA; the land masks are static assets beside the page. */
async function json<T>(path: string, base: string = DATA): Promise<T> {
  const res = await fetch(`${base}/${path}`);
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

/** The land mask as an offscreen canvas, scaled smoothly when drawn so coasts are soft. */
function landCanvas(land: Land): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = c.height = land.size;
  const ctx = c.getContext("2d")!;
  ctx.fillStyle = LAND;
  land.runs.forEach((row, y) => {
    for (let i = 0; i < row.length; i += 2) ctx.fillRect(row[i], y, row[i + 1], 1);
  });
  return c;
}

function color(i: number): RGBA {
  const c = state.cells!;
  return state.metric === "unique" ? uniqueColor(c.unique[i], MAX_EXP) : binColor(c.vessel_days[i], ARCHIVE_BINS);
}

/** Fade all four edges into the page color, so the square's straight cuts dissolve. */
function fadeEdges(ctx: CanvasRenderingContext2D, side: number): void {
  const band = side * FADE;
  const sides: [number, number, number, number, number, number, number, number][] = [
    [0, 0, 0, band, 0, 0, side, band], // top
    [0, side, 0, side - band, 0, side - band, side, band], // bottom
    [0, 0, band, 0, 0, 0, band, side], // left
    [side, 0, side - band, 0, side - band, 0, band, side], // right
  ];
  for (const [x0, y0, x1, y1, rx, ry, rw, rh] of sides) {
    const g = ctx.createLinearGradient(x0, y0, x1, y1);
    g.addColorStop(0, LAND);
    g.addColorStop(1, "rgba(17,17,17,0)");
    ctx.fillStyle = g;
    ctx.fillRect(rx, ry, rw, rh);
  }
}

function draw(): void {
  const canvas = $("sp-canvas") as HTMLCanvasElement;
  const box = $("sp-chart").getBoundingClientRect();
  const side = Math.floor(Math.min(box.width, box.height - 26));
  const dpr = window.devicePixelRatio || 1;
  canvas.style.width = canvas.style.height = `${side}px`;
  canvas.width = canvas.height = Math.round(side * dpr);
  const ctx = canvas.getContext("2d")!;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  // The ocean: a slate glow from the center of the square, where Spilhaus puts the open sea.
  const glow = ctx.createRadialGradient(side / 2, side / 2, side * 0.05, side / 2, side / 2, side * 0.72);
  glow.addColorStop(0, OCEAN_IN);
  glow.addColorStop(1, OCEAN_OUT);
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, side, side);
  if (state.land) {
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(state.land, 0, 0, side, side);
  }
  const c = state.cells;
  if (!c) return;
  const r = Math.max(0.9, side / 760);
  const order = c.u.map((_, i) => i);
  if (state.metric === "vessels") order.sort((a, b) => c.vessel_days[a] - c.vessel_days[b]);
  for (const i of order) {
    const [cr, cg, cb, ca] = color(i);
    ctx.fillStyle = `rgba(${cr},${cg},${cb},${Math.min(1, (ca / 255) * 1.15)})`;
    ctx.beginPath();
    ctx.arc(c.u[i] * side, c.v[i] * side, r, 0, Math.PI * 2);
    ctx.fill();
  }
  if (state.center !== "spilhaus") fadeEdges(ctx, side);
  if (state.hover >= 0) {
    ctx.strokeStyle = "#decf03";
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(c.u[state.hover] * side, c.v[state.hover] * side, r + 4, 0, Math.PI * 2);
    ctx.stroke();
  }
}

/** Nearest cell to the pointer, through a coarse grid index. */
function indexCells(c: Cells): (u: number, v: number, maxDist: number) => number {
  const G = 160;
  const grid = new Map<number, number[]>();
  c.u.forEach((u, i) => {
    const k = Math.floor(u * G) * G + Math.floor(c.v[i] * G);
    (grid.get(k) ?? grid.set(k, []).get(k)!).push(i);
  });
  return (u, v, maxDist) => {
    let best = -1;
    let bestD = maxDist * maxDist;
    const [gu, gv] = [Math.floor(u * G), Math.floor(v * G)];
    for (let a = gu - 1; a <= gu + 1; a++)
      for (let b = gv - 1; b <= gv + 1; b++)
        for (const i of grid.get(a * G + b) ?? []) {
          const d = (c.u[i] - u) ** 2 + (c.v[i] - v) ** 2;
          if (d < bestD) [best, bestD] = [i, d];
        }
    return best;
  };
}

function latlon(lat: number, lon: number): string {
  const f = (v: number, p: string, n: string) => `${Math.abs(v).toFixed(1)}°${v >= 0 ? p : n}`;
  return `${f(lat, "N", "S")} ${f(lon, "E", "W")}`;
}

function wirePointer(): void {
  const canvas = $("sp-canvas") as HTMLCanvasElement;
  const tip = $("sp-tip");
  const at = (e: PointerEvent | MouseEvent, radiusPx: number) => {
    const b = canvas.getBoundingClientRect();
    const near = state.nearest;
    return near ? near((e.clientX - b.left) / b.width, (e.clientY - b.top) / b.height, radiusPx / b.width) : -1;
  };
  const open = (i: number) => {
    const c = state.cells!;
    location.href = `archive.html#6/${c.lat[i].toFixed(3)}/${c.lon[i].toFixed(3)}`;
  };
  const show = (i: number, x: number, y: number, touch: boolean) => {
    if (i !== state.hover) {
      state.hover = i;
      draw();
    }
    if (i < 0) {
      tip.hidden = true;
      canvas.style.cursor = "default";
      return;
    }
    const c = state.cells!;
    canvas.style.cursor = "pointer";
    const years = c.first_year[i] === c.last_year[i] ? `${c.first_year[i]}` : `${c.first_year[i]}-${c.last_year[i]}`;
    tip.innerHTML = `<b>${latlon(c.lat[i], c.lon[i])}</b><br>${compact(c.unique[i])} unique of ${compact(c.published[i])}
      <br>${compact(c.vessel_days[i])} vessel-days, ${c.platforms[i]} platforms<br>collected ${years}
      <br><i>${touch ? "tap again" : "click"} to open the chart</i>`;
    const b = $("sp-chart").getBoundingClientRect();
    tip.style.left = `${Math.max(0, Math.min(x - b.left + 14, b.width - 220))}px`;
    tip.style.top = `${y - b.top + 14}px`;
    tip.hidden = false;
  };
  // Mouse: hover shows, click opens. Touch has no hover: the first tap shows, a second tap on the same dot opens.
  canvas.addEventListener("pointermove", (e) => {
    if (e.pointerType === "mouse") show(at(e, 8), e.clientX, e.clientY, false);
  });
  canvas.addEventListener("pointerleave", (e) => {
    if (e.pointerType === "mouse") show(-1, 0, 0, false);
  });
  canvas.addEventListener("pointerup", (e) => {
    const touch = e.pointerType !== "mouse";
    const i = at(e, touch ? 18 : 8);
    if (i < 0) return show(-1, 0, 0, touch);
    if (!touch || i === state.hover) return open(i);
    show(i, e.clientX, e.clientY, true);
  });
}
function ladder(): void {
  if (state.metric === "vessels") {
    const e = ARCHIVE_BINS.edges;
    renderLadder("Vessel-days per cell", e.map((edge, i) => [binColor(edge, ARCHIVE_BINS), i === e.length - 1 ? `${compact(edge)}+` : compact(edge)]));
  } else {
    renderLadder("Unique soundings per cell", [0, 2, 4, 6, 8].map((k) => [uniqueColor(10 ** k, MAX_EXP), k === 8 ? "100M+" : compact(10 ** k)]));
  }
}

/** Load (once) and show the land mask and projected cells for one center of the projection. */
const loaded = new Map<string, Promise<[HTMLCanvasElement, Cells]>>();
async function useCenter(name: string): Promise<void> {
  if (!loaded.has(name)) {
    loaded.set(
      name,
      Promise.all([json<Land>(`spilhaus/${name}/land.json`, "."), json<Cells>(`spilhaus/${name}/cells.json`)]).then(
        ([land, cells]) => [landCanvas(land), cells],
      ),
    );
  }
  const [land, cells] = await loaded.get(name)!;
  state.center = name;
  state.land = land;
  state.cells = cells;
  state.nearest = indexCells(cells);
  state.hover = -1;
  $("sp-caption-text").innerHTML = [
    `After <a href="${SPILHAUS}">Athelstan Spilhaus</a>'s <a href="${SPILHAUS_MAP}">world ocean map</a>`,
    CENTERS[name],
    `<a href="https://h3geo.org/">H3</a> resolution 4`,
  ]
    .filter(Boolean)
    .join(" · ");
  try {
    localStorage.setItem(CENTER_KEY, name);
  } catch {
    // storage unavailable: the choice lasts for this visit
  }
  draw();
}

function wireDrawer(): void {
  const tab = $("center-tab");
  const drawer = $("center-drawer");
  tab.addEventListener("click", () => {
    const open = drawer.hidden;
    drawer.hidden = !open;
    tab.setAttribute("aria-expanded", String(open));
  });
}

async function main(): Promise<void> {
  wireDrawer();
  ladder();
  // A visitor's own earlier choice wins; otherwise start near them.
  let initial = nearestCenter();
  try {
    const saved = localStorage.getItem(CENTER_KEY);
    if (saved && saved in CENTERS) initial = saved;
  } catch {
    // storage unavailable: the time-zone default stands
  }
  const [meta, recent] = await Promise.all([loadMeta(), loadRecent(), useCenter(initial)]);
  wirePointer();
  for (const input of document.querySelectorAll<HTMLInputElement>('input[name="center"]')) {
    input.checked = input.value === initial;
    input.addEventListener("change", () => void useCenter(input.value));
  }
  renderUpdated(meta);
  new ResizeObserver(() => draw()).observe($("sp-chart"));

  $("sp-figures").innerHTML = [
    [compact(meta.published), "soundings published"],
    [compact(meta.unique), "unique"],
    [compact(meta.vessel_days ?? 0), "vessel-days"],
    [meta.platforms.toLocaleString("en"), "platforms"],
  ]
    .map(([n, l]) => `<div><span class="n">${n}</span><span class="l">${l}</span></div>`)
    .join("");
  const w = recent.strip["7d"];
  $("recent-text").textContent =
    `This week: ${compact(w.published)} soundings from ${w.platforms} platforms, ${w.new_platforms} of them new; median lag ${hours(w.median_lag_h)}.`;
  // Reflection lines: unique soundings in the last seven publication batches, as on the recent page.
  const uni = recent.batches.rows.map((r) => Number(r[recent.batches.columns.indexOf("unique")]));
  renderReflection(uni.slice(-7).reverse(), "unique soundings, last 7 batches");
  setFavicon(uni.slice(-7).reverse());

  for (const input of document.querySelectorAll<HTMLInputElement>('input[name="metric"]')) {
    input.addEventListener("change", () => {
      state.metric = input.value as Metric;
      ladder();
      draw();
    });
  }
}

main().catch((err: unknown) => {
  console.error(err);
  $("sp-figures").textContent = "Census data could not be loaded.";
});
