import { defineConfig } from "vite";

// Relative base: the site is served from https://lhzn-io.github.io/csb-census/ (or a custom domain).
// Data lives in public/data, written by `csb-census layers --out site/public/data`.
export default defineConfig({
  base: "./",
  build: { target: "es2022", chunkSizeWarningLimit: 2000 },
});
