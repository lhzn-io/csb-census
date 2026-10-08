export type RGBA = [number, number, number, number];
type Stop = [number, [number, number, number]];

// Counts: muted dark purple through blues into green (monotonic in lightness, so it reads in
// grayscale and for most color-vision types); a warm ramp for duplicate share.
const COUNTS: Stop[] = [
  [0, [84, 62, 122]],
  [0.25, [72, 88, 168]],
  [0.5, [46, 132, 200]],
  [0.75, [36, 170, 160]],
  [1, [124, 212, 112]],
];
const DUP: Stop[] = [
  [0, [255, 247, 236]],
  [0.25, [253, 212, 158]],
  [0.5, [252, 141, 89]],
  [0.75, [215, 48, 31]],
  [1, [127, 0, 0]],
];

function ramp(stops: Stop[], t: number, alpha: number): RGBA {
  const x = Math.min(1, Math.max(0, t));
  for (let i = 1; i < stops.length; i++) {
    const [t1, c1] = stops[i];
    const [t0, c0] = stops[i - 1];
    if (x <= t1) {
      const f = (x - t0) / (t1 - t0);
      return [
        Math.round(c0[0] + f * (c1[0] - c0[0])),
        Math.round(c0[1] + f * (c1[1] - c0[1])),
        Math.round(c0[2] + f * (c1[2] - c0[2])),
        alpha,
      ];
    }
  }
  const last = stops[stops.length - 1][1];
  return [last[0], last[1], last[2], alpha];
}

/** Log-scaled unique soundings: 1 to 10^maxExp maps across the ramp; sparse cells fade into the basemap. */
export function uniqueColor(n: number, maxExp: number): RGBA {
  const t = Math.min(1, Math.log10(Math.max(1, n)) / maxExp);
  return ramp(COUNTS, t, Math.round(45 + 190 * t));
}

/**
 * Freshness, for the last 24 hours of publication: how long before publication a cell's soundings
 * were collected sets its opacity (the color stays the soundings ramp). Steps match the census's
 * AGE_BUCKETS; a cell with no valid collection date fades like the oldest.
 */
export const AGE_STEPS: { label: string; maxDays: number; alpha: number }[] = [
  { label: "this week", maxDays: 7, alpha: 245 },
  { label: "this month", maxDays: 30, alpha: 165 },
  { label: "this year", maxDays: 365, alpha: 100 },
  { label: "older", maxDays: Infinity, alpha: 55 },
];

export function ageAlpha(ageDays: number | null | undefined): number {
  if (ageDays == null) return AGE_STEPS[AGE_STEPS.length - 1].alpha;
  return (AGE_STEPS.find((s) => ageDays < s.maxDays) ?? AGE_STEPS[AGE_STEPS.length - 1]).alpha;
}

/** Soundings color with the count ramp's own low-count fade replaced by freshness (or solid, unfaded). */
export function freshColor(n: number, maxExp: number, ageDays: number | null | undefined, fade: boolean): RGBA {
  const [r, g, b] = uniqueColor(n, maxExp);
  return [r, g, b, fade ? ageAlpha(ageDays) : 235];
}

export function dupColor(share: number): RGBA {
  return ramp(DUP, share, 210);
}

export function legendGradient(kind: "unique" | "dup"): string {
  const stops = kind === "unique" ? COUNTS : DUP;
  return `linear-gradient(to right, ${stops.map(([t, c]) => `rgb(${c.join(",")}) ${t * 100}%`).join(", ")})`;
}

/**
 * Discrete bins for vessel-days, read like a legend people can quote ("100+ vessel-days").
 * Lower bins are more transparent, so sparse cells recede into the basemap.
 */
export interface Bins {
  edges: number[]; // lower bound of each bin; the last one is open-ended
  label: string;
}

export const ARCHIVE_BINS: Bins = { edges: [1, 10, 100, 1000], label: "vessel-days per cell" };
export const RECENT_BINS: Bins = { edges: [1, 3, 10, 30], label: "vessel-days per cell in the window" };

const BIN_ALPHA = [80, 140, 200, 240];

function binIndex(n: number, edges: number[]): number {
  let i = 0;
  while (i + 1 < edges.length && n >= edges[i + 1]) i++;
  return i;
}

export function binColor(n: number, bins: Bins): RGBA {
  const i = binIndex(n, bins.edges);
  return ramp(COUNTS, i / (bins.edges.length - 1), BIN_ALPHA[i]);
}

/** Legend chips: one swatch per bin, labelled with its lower bound ("1000+" for the last). */
export function binChips(bins: Bins): string {
  return bins.edges
    .map((edge, i) => {
      const [r, g, b, a] = ramp(COUNTS, i / (bins.edges.length - 1), BIN_ALPHA[i]);
      const text = i === bins.edges.length - 1 ? `${edge.toLocaleString("en")}+` : edge.toLocaleString("en");
      return `<span class="chip" style="background:rgba(${r},${g},${b},${a / 255})">${text}</span>`;
    })
    .join("");
}
