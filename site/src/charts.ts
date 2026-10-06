import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { DATA } from "./config";
import { compact } from "./metrics";

export interface Series {
  columns: string[];
  community: [string, number, number, number, number][];
  providers?: Record<string, [string, number, number, number, number][]>;
}

export async function loadSeries(): Promise<Series> {
  const res = await fetch(`${DATA}/timeseries_month.json`);
  if (!res.ok) throw new Error(`series: HTTP ${res.status}`);
  return (await res.json()) as Series;
}

/** Collection months from 2016 on; the archive's older logs are sparse and would flatten the plot. */
const FIRST_MONTH = "2016-01";

/** Colours shared by every chart: published (muted), unique (blue), platforms (green). */
export const CHART = { published: "#8a93a6", unique: "#2e84c8", platforms: "#48b38a" };

function cssVar(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function axis(extra: uPlot.Axis = {}): uPlot.Axis {
  const muted = cssVar("--muted", "#5b6b78");
  return { stroke: muted, grid: { stroke: cssVar("--rule", "#d8e1e8"), width: 1 }, ticks: { show: false }, ...extra };
}

const fmt = (_u: uPlot, v: number | null) => (v == null ? "" : compact(v));

/** Keep a chart's width in step with its container. */
function fit(plot: uPlot, el: HTMLElement, height: number): void {
  new ResizeObserver(() => plot.setSize({ width: Math.max(240, el.clientWidth), height })).observe(el);
}

function toData(rows: Series["community"]): uPlot.AlignedData {
  const kept = rows.filter((r) => r[0] >= FIRST_MONTH);
  const x = kept.map((r) => Date.UTC(Number(r[0].slice(0, 4)), Number(r[0].slice(5, 7)) - 1, 1) / 1000);
  return [x, kept.map((r) => r[1]), kept.map((r) => r[2])];
}

let monthly: uPlot | null = null;

/** Published and unique soundings by collection month (archive page). Drag to zoom, double-click to reset. */
export function renderChart(el: HTMLElement, rows: Series["community"]): void {
  const data = toData(rows);
  if (monthly) {
    monthly.setData(data);
    return;
  }
  monthly = new uPlot(
    {
      width: Math.max(240, el.clientWidth),
      height: 200,
      cursor: { drag: { x: true, y: false } },
      scales: { y: { distr: 3 } },
      axes: [axis(), axis({ values: (_u, ticks) => ticks.map((t) => compact(t)), size: 56 })],
      series: [
        {},
        { label: "Published", stroke: CHART.published, width: 1.5, value: fmt },
        { label: "Unique", stroke: CHART.unique, width: 2, value: fmt },
      ],
    },
    data,
    el,
  );
  fit(monthly, el, 200);
}

/**
 * Bars per publication batch: published behind, unique in front, so the visible gap is the
 * duplicates. Linear scale, so a single large batch reads as large.
 */
export function batchChart(el: HTMLElement, x: number[], published: number[], unique: number[]): uPlot {
  const bars = uPlot.paths.bars!({ size: [0.85, 14], align: 1 });
  const plot = new uPlot(
    {
      width: Math.max(240, el.clientWidth),
      height: 180,
      cursor: { drag: { x: true, y: false } },
      axes: [axis(), axis({ values: (_u, ticks) => ticks.map((t) => compact(t)), size: 56 })],
      series: [
        { value: (_u, v) => (v == null ? "" : new Date(v * 1000).toISOString().slice(0, 16).replace("T", " ")) },
        { label: "Published", fill: CHART.published, stroke: CHART.published, paths: bars, points: { show: false }, value: fmt },
        { label: "Unique", fill: CHART.unique, stroke: CHART.unique, paths: bars, points: { show: false }, value: fmt },
      ],
    },
    [x, published, unique],
    el,
  );
  fit(plot, el, 180);
  return plot;
}

/**
 * Daily soundings (log scale, left) and active platforms (right). Drag across a range to zoom in,
 * double-click to zoom back out.
 */
export function dailyChart(el: HTMLElement): (x: number[], published: number[], unique: number[], platforms: number[]) => void {
  let plot: uPlot | null = null;
  return (x, published, unique, platforms) => {
    const data: uPlot.AlignedData = [x, published, unique, platforms];
    if (plot) {
      plot.setData(data);
      return;
    }
    plot = new uPlot(
      {
        width: Math.max(240, el.clientWidth),
        height: 220,
        cursor: { drag: { x: true, y: false } },
        scales: { y: { distr: 3 }, p: { range: (_u, _min, max) => [0, Math.max(1, max * 1.1)] } },
        axes: [
          axis(),
          axis({ values: (_u, ticks) => ticks.map((t) => compact(t)), size: 56 }),
          axis({ scale: "p", side: 1, grid: { show: false }, size: 40 }),
        ],
        series: [
          {},
          { label: "Published", stroke: CHART.published, width: 1.2, value: fmt },
          { label: "Unique", stroke: CHART.unique, width: 1.8, value: fmt },
          { label: "Platforms", stroke: CHART.platforms, width: 1.5, scale: "p", value: fmt },
        ],
      },
      data,
      el,
    );
    fit(plot, el, 220);
  };
}
