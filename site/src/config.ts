/** Build-time switches. Provider views stay off until the DCDB/CIRES heads-up has happened. */
export const SHOW_PROVIDERS = import.meta.env.VITE_SHOW_PROVIDERS === "true";

export const DATA = "./data";

/** Long Island Sound, west to east (Throgs Neck to The Race), as [west, south, east, north]. */
export const LIS_BBOX: [number, number, number, number] = [-73.82, 40.84, -71.84, 41.42];

/** Map zoom at which each H3 resolution takes over (see layers.py LEVELS). */
export const RES_FOR_ZOOM = (zoom: number): 4 | 6 | 8 => (zoom < 5 ? 4 : zoom < 8 ? 6 : 8);

export const ESRI_ATTRIBUTION =
  "Basemap: Esri, GEBCO, NOAA, National Geographic, Garmin, HERE, Geonames.org, and other contributors";
