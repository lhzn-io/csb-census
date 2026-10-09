/** Bookmarks: places worth a closer look, in a drawer under the Logbook tab on both map pages. */
import type { Map } from "maplibre-gl";
import { $, closeDrawer, wireDrawer } from "./chartroom";
import { LIS_BBOX } from "./config";

export interface Bookmark {
  id: string;
  name: string;
  note: string;
  bbox: [number, number, number, number]; // west, south, east, north
}

export const BOOKMARKS: Bookmark[] = [
  { id: "lis", name: "Long Island Sound", note: "Throgs Neck to The Race", bbox: LIS_BBOX },
  {
    id: "stellwagen",
    name: "Stellwagen Bank",
    note: "National Marine Sanctuary, Massachusetts Bay",
    bbox: [-70.62, 42.07, -70.0, 42.78],
  },
  {
    id: "block-island",
    name: "Block Island Sound",
    note: "Block Island and its wind farm, Rhode Island",
    bbox: [-71.95, 40.98, -71.3, 41.38],
  },
  {
    id: "hudson-canyon",
    name: "Hudson Canyon",
    note: "Submarine canyon at the shelf edge, New York Bight (try the Relief chart)",
    bbox: [-73.0, 39.2, -71.6, 40.3],
  },
];

export function wireBookmarks(map: Map): void {
  $("bookmark-list").innerHTML = BOOKMARKS.map(
    (b) => `<button type="button" class="bookmark" data-id="${b.id}"><span class="bookmark-name">${b.name}</span><span class="bookmark-note">${b.note}</span></button>`,
  ).join("");
  wireDrawer("bookmarks-tab", "bookmarks");
  $("bookmark-list").addEventListener("click", (e) => {
    const id = (e.target as HTMLElement).closest<HTMLElement>(".bookmark")?.dataset.id;
    const place = BOOKMARKS.find((b) => b.id === id);
    if (!place) return;
    closeDrawer("bookmarks");
    map.fitBounds(place.bbox, { padding: 24, duration: 1200 });
  });
}
