/** "Chart room" design: the pieces shared by the archive and recent pages (frame, legend, band, logbook). */
import "./chartroom.css";

import type { Map as MapLibreMap } from "maplibre-gl";
import type { RGBA } from "./colors";
import { compact } from "./metrics";

export const $ = (id: string) => document.getElementById(id)!;
const SVG = "http://www.w3.org/2000/svg";
const MONTHS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];
export const YELLOW = "#decf03";

export function el<K extends keyof SVGElementTagNameMap>(
  name: K,
  attrs: Record<string, string | number>,
): SVGElementTagNameMap[K] {
  const node = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

/* ---------- Chart frame: graticule ticks and a cursor position in degrees and minutes ---------- */

const STEPS = [30, 20, 10, 5, 2, 1, 0.5, 0.25, 10 / 60, 5 / 60, 2 / 60, 1 / 60];

function dm(v: number, pos: string, neg: string, degWidth: number): string {
  const a = Math.abs(v);
  const d = Math.floor(a + 1e-9);
  const m = (a - d) * 60;
  const mm = m < 0.05 || m > 59.95 ? "" : `${m.toFixed(Math.abs(m - Math.round(m)) < 0.05 ? 0 : 1).padStart(2, "0")}′`;
  return `${String(d).padStart(degWidth, "0")}°${mm}${v === 0 ? "" : v > 0 ? pos : neg}`;
}

function drawTicks(map: MapLibreMap): void {
  const step = (span: number) => STEPS.find((s) => span / s >= 4) ?? STEPS[STEPS.length - 1];
  const b = map.getBounds();
  const c = map.getCenter();
  const box = map.getContainer();
  const [w, h] = [box.clientWidth, box.clientHeight];
  const lonStep = step(b.getEast() - b.getWest());
  const latStep = step(b.getNorth() - b.getSouth());
  const lons: string[] = [];
  for (let lon = Math.ceil(b.getWest() / lonStep) * lonStep; lon <= b.getEast(); lon += lonStep) {
    const x = map.project([lon, c.lat]).x;
    if (x > 24 && x < w - 24) {
      const wrapped = ((((lon + 180) % 360) + 360) % 360) - 180;
      lons.push(`<span class="tick" style="left:${x}px">${dm(wrapped, "E", "W", 3)}</span>`);
    }
  }
  const lats: string[] = [];
  for (let lat = Math.ceil(b.getSouth() / latStep) * latStep; lat <= b.getNorth(); lat += latStep) {
    const y = map.project([c.lng, lat]).y;
    if (y > 24 && y < h - 24) lats.push(`<span class="tick" style="top:${y}px">${dm(lat, "N", "S", 2)}</span>`);
  }
  $("ticks-top").innerHTML = $("ticks-bottom").innerHTML = lons.join("");
  $("ticks-left").innerHTML = $("ticks-right").innerHTML = lats.join("");
}

/** Graticule ticks on the neatline and a cursor readout, kept in step with the map. */
export function frameChart(map: MapLibreMap): void {
  const out = $("cursor");
  const f = (v: number, p: string, n: string, w: number) => {
    const a = Math.abs(v);
    return `${String(Math.floor(a)).padStart(w, "0")}°${((a % 1) * 60).toFixed(2).padStart(5, "0")}′${v >= 0 ? p : n}`;
  };
  map.on("mousemove", (e) => {
    const { lat, lng } = e.lngLat.wrap();
    out.textContent = `${f(lat, "N", "S", 2)}  ${f(lng, "E", "W", 3)}`;
  });
  map.on("mouseout", () => (out.textContent = "--"));
  map.on("move", () => drawTicks(map));
  map.on("resize", () => drawTicks(map));
  drawTicks(map);
}

/* ---------- Legend ladder: bins read bottom (sparse) to top (busy) ---------- */

export function renderLadder(title: string, steps: [RGBA, string][]): void {
  const rows = steps
    .map(([[r, g, b, a], text]) => `<div class="ladder-step"><i style="background:rgba(${r},${g},${b},${a / 255})"></i>${text}</div>`)
    .join("");
  $("ladder").innerHTML = `<span class="ladder-title">${title}</span><div class="ladder-steps">${rows}</div>`;
}

/* ---------- Masthead reflection: the wordmark's water lines, drawn from data (newest first) ---------- */

export function renderReflection(values: number[], label: string): void {
  const max = Math.max(1, ...values);
  const svg = $("reflection");
  svg.setAttribute("viewBox", "0 0 120 46");
  svg.setAttribute("aria-label", label);
  svg.innerHTML = values
    .slice(0, 7)
    .map((v, i) => {
      const w = 18 + 102 * Math.sqrt(v / max);
      return `<line x1="0" x2="${w.toFixed(1)}" y1="${4 + i * 6.4}" y2="${4 + i * 6.4}" stroke="${YELLOW}" stroke-width="${(1.6 - i * 0.12).toFixed(2)}" stroke-opacity="${(1 - i * 0.1).toFixed(2)}"/>`;
    })
    .join("");
  const title = svg.closest(".mast")?.querySelector<HTMLElement>(".reflection-title");
  if (title) title.textContent = label;
}

/* ---------- Horizon band ---------- */

export function renderReadouts(items: [string, string, string][]): void {
  $("readouts").innerHTML = items
    .map(
      ([num, lab, sub]) =>
        `<div class="readout"><div class="num">${num}</div><span class="lab">${lab}</span><span class="sub">${sub}</span></div>`,
    )
    .join("");
}

export interface HorizonOptions {
  x: number[]; // epoch seconds
  line: number[]; // the horizon itself (yellow)
  faint?: number[]; // context drawn faintly behind it
  smooth?: number; // rolling mean window for `line`, in points
  ticks: "months" | "years";
  shadeFrom?: number; // epoch seconds: shade from here to the end
  shadeLabel?: string;
  hover: (i: number) => string;
}

/** A log-scaled series drawn as the horizon: yellow line over a soft fill, graduations and a hover rule. */
export function renderHorizon(o: HorizonOptions): void {
  const svg = $("horizon") as unknown as SVGSVGElement;
  const W = 1000;
  const H = 100;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.replaceChildren();
  const { x } = o;
  if (x.length < 2) return;
  const [x0, x1] = [x[0], x[x.length - 1]];
  const lg = (v: number) => Math.log10(Math.max(1, v));
  const k = o.smooth ?? 1;
  const line = o.line.map((_, i) => {
    const win = o.line.slice(Math.max(0, i - k + 1), i + 1);
    return lg(win.reduce((a, b) => a + b, 0) / win.length);
  });
  const faint = o.faint?.map(lg);
  const all = [...line, ...(faint ?? [])].filter((v) => v > 0);
  const lo = Math.min(...all) - 0.2;
  const hi = Math.max(...all) + 0.1;
  const px = (v: number) => ((v - x0) / (x1 - x0)) * W;
  const py = (l: number) => H - ((Math.max(lo, l) - lo) / (hi - lo)) * H;
  const pts = (ys: number[]) => x.map((v, i) => `${px(v).toFixed(1)},${py(ys[i]).toFixed(1)}`);

  const defs = el("defs", {});
  const grad = el("linearGradient", { id: "hz-fill", x1: 0, y1: 0, x2: 0, y2: 1 });
  grad.append(el("stop", { offset: "0%", "stop-color": YELLOW, "stop-opacity": 0.28 }));
  grad.append(el("stop", { offset: "100%", "stop-color": YELLOW, "stop-opacity": 0 }));
  defs.append(grad);
  svg.append(defs);

  const labels: string[] = [];
  const rule = (xx: number) =>
    svg.append(el("line", { x1: xx, x2: xx, y1: 0, y2: H, stroke: "rgba(255,255,255,0.12)", "vector-effect": "non-scaling-stroke" }));
  const start = new Date(x0 * 1000);
  if (o.ticks === "months") {
    for (let m = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 1)); m.getTime() / 1000 < x1; m.setUTCMonth(m.getUTCMonth() + 1)) {
      const xx = px(m.getTime() / 1000);
      rule(xx);
      const jan = m.getUTCMonth() === 0;
      labels.push(`<span class="month${jan ? " jan" : ""}" style="left:${(100 * xx) / W}%">${jan ? m.getUTCFullYear() : MONTHS[m.getUTCMonth()]}</span>`);
    }
  } else {
    for (let y = start.getUTCFullYear() + 1; Date.UTC(y, 0, 1) / 1000 < x1; y++) {
      const xx = px(Date.UTC(y, 0, 1) / 1000);
      rule(xx);
      labels.push(`<span class="month jan" style="left:${(100 * xx) / W}%">${y}</span>`);
    }
  }
  labels.push(`<span class="axis"></span>`);
  if (o.shadeFrom !== undefined) {
    const xs = px(Math.max(x0, o.shadeFrom));
    svg.append(el("rect", { x: xs, y: 0, width: Math.max(0, W - xs), height: H, fill: "rgba(17,17,17,0.35)" }));
    if (o.shadeLabel) labels.push(`<span class="arriving" style="left:${(100 * xs) / W}%">${o.shadeLabel}</span>`);
  }
  const every = hi - lo > 4.5 ? 2 : 1; // at most a handful of scale lines
  for (let d = Math.floor(hi); d >= Math.ceil(lo); d -= every) {
    const yy = py(d);
    svg.append(el("line", { x1: 0, x2: W, y1: yy, y2: yy, stroke: "rgba(255,255,255,0.16)", "stroke-dasharray": "2 4", "vector-effect": "non-scaling-stroke" }));
    labels.push(`<span class="decade" style="top:${(100 * yy) / H}%">${compact(10 ** d)}</span>`);
  }
  const main = pts(line);
  svg.append(el("path", { d: `M0,${H} L${main.join(" L")} L${W},${H} Z`, fill: "url(#hz-fill)" }));
  if (faint) {
    svg.append(el("polyline", { points: pts(faint).join(" "), fill: "none", stroke: "rgba(255,255,255,0.4)", "stroke-width": 0.9, "vector-effect": "non-scaling-stroke" }));
  }
  svg.append(el("polyline", { points: main.join(" "), fill: "none", stroke: YELLOW, "stroke-width": 1.8, "vector-effect": "non-scaling-stroke" }));
  $("horizon-labels").innerHTML = labels.join("");

  const cursor = el("line", { x1: 0, x2: 0, y1: 0, y2: H, stroke: "#fff", "stroke-width": 1, "vector-effect": "non-scaling-stroke", visibility: "hidden" });
  svg.append(cursor);
  const hover = $("horizon-hover");
  svg.onmousemove = (e) => {
    const box = svg.getBoundingClientRect();
    const tx = x0 + ((e.clientX - box.left) / box.width) * (x1 - x0);
    let i = 0;
    while (i + 1 < x.length && Math.abs(x[i + 1] - tx) < Math.abs(x[i] - tx)) i++;
    const xx = String(px(x[i]));
    cursor.setAttribute("x1", xx);
    cursor.setAttribute("x2", xx);
    cursor.setAttribute("visibility", "visible");
    hover.textContent = o.hover(i);
  };
  svg.onmouseleave = () => {
    cursor.setAttribute("visibility", "hidden");
    hover.textContent = "";
  };
}

/** Columns in the band's right panel: each column stacks its parts (bottom first). */
export function renderColumns(
  columns: number[][],
  fills: string[],
  opts: { normalize?: boolean; labels?: string[] } = {},
): void {
  const svg = $("batches") as unknown as SVGSVGElement;
  const W = 300;
  const H = 100;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.replaceChildren();
  const totals = columns.map((c) => c.reduce((a, b) => a + b, 0));
  const max = Math.max(1, ...totals);
  const bw = W / Math.max(1, columns.length);
  const gap = columns.length > 40 ? 0.6 : bw * 0.25;
  columns.forEach((parts, i) => {
    let y = H;
    const scale = opts.normalize ? H / Math.max(1, totals[i]) : H / max;
    parts.forEach((v, j) => {
      const h = v * scale;
      y -= h;
      svg.append(el("rect", { x: i * bw + gap / 2, y, width: Math.max(0.6, bw - gap), height: h, fill: fills[j] }));
    });
  });
  const lab = document.getElementById("batches-labels");
  if (lab) {
    lab.innerHTML = (opts.labels ?? [])
      .map((t, i) => `<span class="month" style="left:${((i + 0.5) * 100) / columns.length}%">${t}</span>`)
      .join("");
  }
}

/* ---------- Logbook drawer ---------- */

export function wireLogbook(): void {
  const tab = $("logbook-tab");
  const book = $("logbook");
  tab.addEventListener("click", () => {
    const open = book.hidden;
    book.hidden = !open;
    tab.setAttribute("aria-expanded", String(open));
  });
}

export function table(head: string[], rows: string[][]): string {
  return `<table>${head.length ? `<thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead>` : ""}
    <tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
