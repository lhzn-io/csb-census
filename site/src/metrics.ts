import { DATA } from "./config";

export interface Meta {
  generation: number;
  built_at: string;
  last_ingested: string | null;
  published: number;
  unique: number;
  dup_resend: number;
  dup_cross_id: number;
  files: number;
  providers: number;
  platforms: number;
  vessel_days?: number;
  platforms_active_30d?: number;
  provider_views: boolean;
}

export async function loadMeta(): Promise<Meta> {
  const res = await fetch(`${DATA}/meta.json`);
  if (!res.ok) throw new Error(`meta: HTTP ${res.status}`);
  return (await res.json()) as Meta;
}

export function compact(n: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(n);
}

const pct = (part: number, whole: number) => `${((100 * part) / Math.max(1, whole)).toFixed(1)}%`;

export function renderMetrics(el: HTMLElement, meta: Meta): void {
  const dup = meta.published - meta.unique;
  const tiles: [string, string, string][] = [
    [compact(meta.published), "soundings published", `${meta.files.toLocaleString("en")} files`],
    [compact(meta.unique), "unique soundings", `${pct(meta.unique, meta.published)} of published`],
    [pct(dup, meta.published), "exact duplicates", `${pct(meta.dup_cross_id, meta.published)} under another platform ID`],
    [String(meta.providers), "provider labels", `${meta.platforms.toLocaleString("en")} platform IDs`],
  ];
  el.replaceChildren(
    ...tiles.map(([value, label, note]) => {
      const div = document.createElement("div");
      div.className = "metric";
      div.innerHTML = `<strong></strong><span class="label"></span><span class="note"></span>`;
      (div.children[0] as HTMLElement).textContent = value;
      (div.children[1] as HTMLElement).textContent = label;
      (div.children[2] as HTMLElement).textContent = note;
      return div;
    }),
  );
}
