import { resolve } from "node:path";
import { defineConfig } from "vite";

// Relative base: the site is served from https://lhzn-io.github.io/csb-census/ (or a custom domain).
// Data lives in public/data, written by `csb-census layers --out site/public/data`.
// Three pages: the landing page, the full archive and recent activity.
export default defineConfig({
  base: "./",
  build: {
    target: "es2022",
    chunkSizeWarningLimit: 2400, // the map bundle (MapLibre + deck.gl), loaded only by the two map pages
    rollupOptions: {
      input: {
        index: resolve(import.meta.dirname, "index.html"),
        archive: resolve(import.meta.dirname, "archive.html"),
        recent: resolve(import.meta.dirname, "recent.html"),
      },
    },
  },
});
