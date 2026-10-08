import { asyncBufferFromUrl, parquetReadObjects } from "hyparquet";
import { DATA } from "./config";

/**
 * One hexagon. Archive layers carry every field; collection-window layers (recent vessel-days) carry
 * h3, vessel_days, platforms, n_unique and n_published; the 24h layer carries h3, n_unique,
 * n_published, platforms, first_day, last_day and mean_age_d.
 */
export interface Cell {
  h3: string;
  n_unique: number;
  n_published: number;
  vessel_days?: number;
  platforms: number;
  dup_share?: number;
  n_providers?: number;
  first_year?: number;
  last_year?: number;
  first_day?: string | null;
  last_day?: string | null;
  mean_age_d?: number | null;
}

export interface Tile {
  res: number;
  part: string;
  rows: number;
  bbox: [number, number, number, number];
  files: string[];
  bytes: number;
}

export interface LayerManifest {
  tiles: Tile[];
  windows?: Record<string, Tile[]>;
  providers?: Record<string, Tile[]>;
}

export async function loadManifest(): Promise<LayerManifest> {
  const res = await fetch(`${DATA}/layers/manifest.json`);
  if (!res.ok) throw new Error(`manifest: HTTP ${res.status}`);
  return (await res.json()) as LayerManifest;
}

function toNumber(v: unknown): number {
  return typeof v === "bigint" ? Number(v) : (v as number);
}

/** Small LRU cache so panning back and forth does not refetch. */
const cache = new Map<string, Promise<Cell[]>>();
const CACHE_LIMIT = 64;

export function readCells(path: string): Promise<Cell[]> {
  const url = `${DATA}/${path}`;
  const hit = cache.get(url);
  if (hit) {
    cache.delete(url);
    cache.set(url, hit);
    return hit;
  }
  const load = (async () => {
    const file = await asyncBufferFromUrl({ url });
    const rows = await parquetReadObjects({ file });
    return rows.map((r) => {
      const cell: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(r)) if (k !== "part") cell[k] = k === "h3" ? String(v) : toNumber(v);
      return cell as unknown as Cell;
    });
  })();
  cache.set(url, load);
  if (cache.size > CACHE_LIMIT) cache.delete(cache.keys().next().value as string);
  load.catch(() => cache.delete(url));
  return load;
}

type Bounds = [number, number, number, number];

function intersects(a: Bounds, b: Bounds): boolean {
  return a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];
}

/** Tiles of one resolution whose bounding boxes meet the viewport. */
export function visibleTiles(tiles: Tile[], res: number, view: Bounds): Tile[] {
  return tiles.filter((t) => t.res === res && intersects(t.bbox, view));
}

export async function cellsFor(tiles: Tile[]): Promise<Cell[]> {
  const parts = await Promise.all(tiles.flatMap((t) => t.files.map(readCells)));
  return parts.flat();
}
