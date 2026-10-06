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

function toData(rows: Series["community"]): uPlot.AlignedData {
  const kept = rows.filter((r) => r[0] >= FIRST_MONTH);
  const x = kept.map((r) => Date.UTC(Number(r[0].slice(0, 4)), Number(r[0].slice(5, 7)) - 1, 1) / 1000);
  return [x, kept.map((r) => r[1]), kept.map((r) => r[2])];
}

let plot: uPlot | null = null;

export function renderChart(el: HTMLElement, rows: Series["community"]): void {
  const data = toData(rows);
  const width = Math.max(240, el.clientWidth);
  if (plot) {
    plot.setData(data);
    return;
  }
  plot = new uPlot(
    {
      width,
      height: 200,
      cursor: { drag: { x: true, y: false } },
      scales: { y: { distr: 3 } },
      axes: [{}, { values: (_u, ticks) => ticks.map((t) => compact(t)), size: 56 }],
      series: [
        {},
        { label: "Published", stroke: "#7c7b78", width: 1.5, value: (_u, v) => (v == null ? "" : compact(v)) },
        { label: "Unique", stroke: "#00224e", width: 2, value: (_u, v) => (v == null ? "" : compact(v)) },
      ],
    },
    data,
    el,
  );
  new ResizeObserver(() => plot?.setSize({ width: Math.max(240, el.clientWidth), height: 200 })).observe(el);
}
