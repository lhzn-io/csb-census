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
