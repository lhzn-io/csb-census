import { DATA } from "./config";

/** One publication window of the strip (see layers.py `_recent`). */
export interface StripWindow {
  files: number;
  published: number;
  unique: number;
  platforms: number;
  new_platforms: number;
  providers: number;
  median_lag_h: number | null;
}

export type WindowName = "24h" | "7d" | "30d" | "all";

export interface Table {
  columns: string[];
  rows: (string | number | boolean | null)[][];
}

export interface Recent {
  now: string;
  strip: Record<WindowName, StripWindow>;
  batches: Table;
  daily_pub: Table;
  daily_coll: Table;
  lag_hist: { buckets: string[]; last_30d: [string, number][]; all: [string, number][] };
  runs: Table;
  providers?: Record<string, Record<WindowName, StripWindow>>;
}

export async function loadRecent(): Promise<Recent> {
  const res = await fetch(`${DATA}/recent.json`);
  if (!res.ok) throw new Error(`recent: HTTP ${res.status}`);
  return (await res.json()) as Recent;
}

/** Column of a table by name, as numbers (missing values become 0). */
export function column(t: Table, name: string): number[] {
  const i = t.columns.indexOf(name);
  return t.rows.map((r) => Number(r[i] ?? 0));
}

/** Epoch seconds for "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM" values, read as UTC. */
export function epochs(t: Table, name: string): number[] {
  const i = t.columns.indexOf(name);
  return t.rows.map((r) => {
    const v = String(r[i]);
    return Date.parse(v.length === 10 ? `${v}T00:00:00Z` : `${v}:00Z`) / 1000;
  });
}

export function hours(h: number | null): string {
  if (h == null) return "-";
  if (h < 48) return `${h.toFixed(h < 10 ? 1 : 0)} h`;
  return `${(h / 24).toFixed(h < 240 ? 1 : 0)} d`;
}
