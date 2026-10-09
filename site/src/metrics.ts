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
  /** Underway time (layers.underway_meta): hours by speed class, and platform-days that never got underway. */
  underway_hours?: number;
  stationary_hours?: number;
  platform_days_logged?: number;
  platform_days_never_underway?: number;
  share_never_underway?: number | null;
  reach_cells_12m?: number;
  provider_views: boolean;
  /** Latency behind NCEI (layers.latency): S3 publication of the last batch taken in, and minutes to the census. */
  last_batch_published?: string | null;
  last_latency_min?: number | null;
  median_latency_min_30d?: number | null;
}

export async function loadMeta(): Promise<Meta> {
  const res = await fetch(`${DATA}/meta.json`);
  if (!res.ok) throw new Error(`meta: HTTP ${res.status}`);
  return (await res.json()) as Meta;
}

export function compact(n: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(n);
}
