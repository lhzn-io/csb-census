import { asyncBufferFromUrl, parquetReadObjects } from "hyparquet";
import { DATA } from "./config";

export interface Cell {
  h3: string;
  n_unique: number;
  n_published: number;
  dup_share: number;
  n_providers: number;
  first_year: number;
  last_year: number;
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
  providers?: Record<string, Tile[]>;
}

const COLUMNS = ["h3", "n_unique", "n_published", "dup_share", "n_providers", "first_year", "last_year"];

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
    const rows = await parquetReadObjects({ file, columns: COLUMNS });
    return rows.map((r) => ({
      h3: String(r.h3),
      n_unique: toNumber(r.n_unique),
      n_published: toNumber(r.n_published),
      dup_share: toNumber(r.dup_share),
      n_providers: toNumber(r.n_providers),
      first_year: toNumber(r.first_year),
      last_year: toNumber(r.last_year),
    }));
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
