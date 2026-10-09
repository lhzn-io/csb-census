import { DATA } from "./config";

/** Counts per collection month (timeseries_month.json): community, and per provider when published. */
export interface Series {
  columns: string[];
  community: [string, number, number, number, number][];
  providers?: Record<string, [string, number, number, number, number][]>;
  /** Per collection month: underway hours and r8 cells first covered that month. */
  reach?: { columns: string[]; rows: [string, number, number][] };
}

export async function loadSeries(): Promise<Series> {
  const res = await fetch(`${DATA}/timeseries_month.json`);
  if (!res.ok) throw new Error(`series: HTTP ${res.status}`);
  return (await res.json()) as Series;
}
