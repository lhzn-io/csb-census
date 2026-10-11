/**
 * NCEI batch poller: starts the census update within minutes of each NOAA NCEI publication batch.
 *
 * Every 5 minutes (cron) it lists the files stamped in the last LOOKBACK_H hours in the public archive and
 * finds the newest S3 publication time. When that is SETTLE_MIN to SETTLE_MIN + 5 minutes old, the batch
 * has stopped growing, and it starts the census workflow. Each batch falls in exactly one such window, so
 * no state is kept between runs. The census itself skips files it has already counted, so a stray extra
 * start costs a short run and nothing else.
 */

interface Env {
  REPO: string; // owner/name
  WORKFLOW: string; // workflow file name
  GH_DISPATCH_TOKEN: string; // fine-grained token: this repo only, Actions read and write
}

const BUCKET = "https://noaa-dcdb-bathymetry-pds.s3.amazonaws.com/";
const LOOKBACK_H = 7; // a batch holds files stamped since the previous one, six hours earlier
const SETTLE_MIN = 5;
const MAX_PAGES = 4;

const pad = (n: number) => String(n).padStart(2, "0");
const dayPrefix = (d: Date) => `csb/csv/${d.getUTCFullYear()}/${pad(d.getUTCMonth() + 1)}/${pad(d.getUTCDate())}/`;
const stamp = (d: Date) =>
  `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}`;

/** The newest LastModified among keys under `prefix` that sort after `after`, or 0 when there are none. */
async function newestPublished(prefix: string, after: string): Promise<number> {
  let newest = 0;
  let token = "";
  for (let page = 0; page < MAX_PAGES; page++) {
    const q = new URLSearchParams({ "list-type": "2", prefix, "start-after": after });
    if (token) q.set("continuation-token", token);
    const res = await fetch(`${BUCKET}?${q}`);
    if (!res.ok) throw new Error(`list ${prefix}: HTTP ${res.status}`);
    const xml = await res.text();
    for (const m of xml.matchAll(/<LastModified>([^<]+)<\/LastModified>/g)) newest = Math.max(newest, Date.parse(m[1]));
    token = /<IsTruncated>true<\/IsTruncated>/.test(xml) ? (xml.match(/<NextContinuationToken>([^<]+)</)?.[1] ?? "") : "";
    if (!token) break;
  }
  return newest;
}

async function poll(env: Env, now: Date): Promise<string> {
  const from = new Date(now.getTime() - LOOKBACK_H * 3600_000);
  // Files are filed under their stamp's day, so look in yesterday's folder too just after midnight.
  const days = dayPrefix(from) === dayPrefix(now) ? [now] : [from, now];
  let newest = 0;
  for (const d of days) {
    const prefix = dayPrefix(d);
    newest = Math.max(newest, await newestPublished(prefix, d === from ? `${prefix}${stamp(from)}` : prefix));
  }
  const ageMin = (now.getTime() - newest) / 60_000;
  if (!newest || ageMin < SETTLE_MIN || ageMin >= SETTLE_MIN + 5) return `idle (newest ${newest ? ageMin.toFixed(1) + " min" : "none"})`;
  const res = await fetch(`https://api.github.com/repos/${env.REPO}/actions/workflows/${env.WORKFLOW}/dispatches`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
      accept: "application/vnd.github+json",
      "user-agent": "csb-census-poller",
      "x-github-api-version": "2022-11-28",
    },
    body: JSON.stringify({ ref: "main", inputs: { mode: "incremental" } }),
  });
  if (!res.ok) throw new Error(`dispatch: HTTP ${res.status} ${await res.text()}`);
  return `dispatched (batch settled ${ageMin.toFixed(1)} min ago)`;
}

export default {
  async scheduled(_event: unknown, env: Env): Promise<void> {
    console.log(await poll(env, new Date()));
  },
};
