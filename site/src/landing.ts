/** Landing page: a choice between the full archive and recent activity, each with a few live figures. */
import "./style.css";

import { compact, loadMeta } from "./metrics";
import { hours, loadRecent } from "./recentdata";

function fill(id: string, rows: [string, string][]): void {
  const el = document.getElementById(id)!;
  el.innerHTML = rows.map(([value, label]) => `<div><strong>${value}</strong><span>${label}</span></div>`).join("");
}

async function main(): Promise<void> {
  const [meta, recent] = await Promise.all([loadMeta(), loadRecent()]);
  fill("archive-figures", [
    [compact(meta.published), "soundings published"],
    [compact(meta.unique), "unique soundings"],
    [compact(meta.vessel_days ?? 0), "vessel-days"],
    [meta.platforms.toLocaleString("en"), "platform IDs"],
  ]);
  const day = recent.strip["24h"];
  const week = recent.strip["7d"];
  fill("recent-figures", [
    [compact(day.published), "soundings in the last 24 h"],
    [week.platforms.toLocaleString("en"), "platforms this week"],
    [week.new_platforms.toLocaleString("en"), "new platforms this week"],
    [hours(week.median_lag_h), "median lag this week"],
  ]);
  document.getElementById("built")!.textContent =
    `Data through ${meta.last_ingested?.slice(0, 16).replace("T", " ") ?? "?"} UTC, state generation ${meta.generation}.`;
}

main().catch((err: unknown) => {
  console.error(err);
  document.getElementById("built")!.textContent = "Census data could not be loaded.";
});
