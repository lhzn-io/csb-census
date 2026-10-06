export type RGBA = [number, number, number, number];
type Stop = [number, [number, number, number]];

// Cividis (colour-vision friendly) for counts; a warm ramp for duplicate share.
const CIVIDIS: Stop[] = [
  [0, [0, 34, 78]],
  [0.25, [65, 77, 107]],
  [0.5, [124, 123, 120]],
  [0.75, [188, 175, 111]],
  [1, [254, 232, 56]],
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

/** Log-scaled unique soundings: 1 to 10^maxExp maps across the ramp. */
export function uniqueColor(n: number, maxExp: number): RGBA {
  return ramp(CIVIDIS, Math.log10(Math.max(1, n)) / maxExp, 210);
}

export function dupColor(share: number): RGBA {
  return ramp(DUP, share, 210);
}

export function legendGradient(kind: "unique" | "dup"): string {
  const stops = kind === "unique" ? CIVIDIS : DUP;
  return `linear-gradient(to right, ${stops.map(([t, c]) => `rgb(${c.join(",")}) ${t * 100}%`).join(", ")})`;
}
