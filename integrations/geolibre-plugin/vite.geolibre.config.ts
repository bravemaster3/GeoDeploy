import { defineConfig } from "vite";

/**
 * Builds the plugin as a single self-contained ESM bundle GeoLibre can load:
 *   geolibre-plugin/dist/index.js   (the plugin entry, referenced by plugin.json)
 *   geolibre-plugin/dist/style.css  (injected globally by the host; scoped .gdp-*)
 *
 * `package:geolibre` then zips the `geolibre-plugin/` folder (plugin.json + dist).
 */
export default defineConfig({
  build: {
    outDir: "geolibre-plugin/dist",
    emptyOutDir: true,
    cssCodeSplit: false,
    lib: {
      entry: "src/index.ts",
      formats: ["es"],
      fileName: () => "index.js",
    },
    rollupOptions: {
      output: {
        // Emit the CSS as a stable name plugin.json can reference.
        assetFileNames: (asset) => (asset.name?.endsWith(".css") ? "style.css" : "[name][extname]"),
      },
    },
  },
});
