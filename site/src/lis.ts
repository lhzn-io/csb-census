import { DATA } from "./config";
import { compact } from "./metrics";
import { readCells, type Cell } from "./tiles";

interface Coverage {
  unique: number;
  published: number;
  providers: number;
  share_ge1: number;
  share_ge100: number;
  km2_covered: number;
}

interface Summary {
  region_km2: number;
  region_cells: number;
  all_time: Coverage;
  by_year: Record<string, Coverage>;
}

/** The fine (H3 r9) Long Island Sound layer and its summary, if this build published them. */
export async function loadLis(): Promise<{ cells: Cell[]; summary: Summary } | null> {
  // Optional: absent until the LIS polygon is published. Dev servers answer a missing file with
  // index.html and HTTP 200, so check the content type rather than trusting the status alone.
  try {
    const res = await fetch(`${DATA}/lis/summary.json`);
    if (!res.ok || !res.headers.get("content-type")?.includes("json")) return null;
    const summary = (await res.json()) as Summary;
    return { cells: await readCells("lis/r9.parquet"), summary };
  } catch (err) {
    console.warn("LIS layer unavailable", err);
    return null;
  }
}

export function renderLisSummary(el: HTMLElement, s: Summary): void {
  const recent = Object.entries(s.by_year).slice(-4);
  const pct = (x: number) => `${(100 * x).toFixed(1)}%`;
  el.innerHTML = `
    <p><strong>${compact(s.all_time.unique)}</strong> unique soundings from
       <strong>${s.all_time.providers}</strong> providers cover <strong>${pct(s.all_time.share_ge1)}</strong>
       of the Sound's ${Math.round(s.region_km2).toLocaleString("en")} km² (H3 r9 cells with at least one
       sounding); <strong>${pct(s.all_time.share_ge100)}</strong> have 100 or more.</p>
    <table><thead><tr><th>Year</th><th>Unique</th><th>Providers</th><th>Cells covered</th></tr></thead>
    <tbody>${recent
      .map(([y, c]) => `<tr><td>${y}</td><td>${compact(c.unique)}</td><td>${c.providers}</td><td>${pct(c.share_ge1)}</td></tr>`)
      .join("")}</tbody></table>`;
}
