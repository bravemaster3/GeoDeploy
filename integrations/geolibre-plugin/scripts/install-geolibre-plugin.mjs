import { statSync } from "node:fs";
import { cp, mkdir, readFile, rm, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// Copies the built plugin bundle into GeoLibre so it loads without editing
// GeoLibre's source or its settings. Two targets:
//
//   (default) GeoLibre Desktop's auto-scan directory
//     `<app-data>/org.geolibre.desktop/plugins/<id>`, scanned on startup.
//
//   --web <geolibre-repo-root>  GeoLibre's bundled drop-in folder
//     `<root>/apps/geolibre-desktop/public/plugins/<id>`, discovered at build
//     time and baked into both the web and desktop builds.

const rootDir = dirname(dirname(fileURLToPath(import.meta.url)));
const bundleDir = join(rootDir, "geolibre-plugin");
const manifestPath = join(bundleDir, "plugin.json");

const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
const target = resolveTarget();
const targetDir = join(target.pluginsDir, manifest.id);

await assertBuilt();
await mkdir(target.pluginsDir, { recursive: true });
await rm(targetDir, { recursive: true, force: true });
await cp(bundleDir, targetDir, { recursive: true });

console.log(`Installed ${manifest.id}@${manifest.version} to ${targetDir}`);
console.log(
  target.mode === "web"
    ? "Rebuild or restart the GeoLibre dev server to load the plugin."
    : "Restart GeoLibre Desktop to load the plugin.",
);

/**
 * Resolve where to copy the bundle. `--web <geolibre-root>` targets GeoLibre's
 * public/plugins drop-in folder; otherwise the GEOLIBRE_PLUGINS_DIR override or
 * a positional path wins, falling back to the desktop app-data plugins dir.
 */
function resolveTarget() {
  const args = process.argv.slice(2);
  const webIndex = args.indexOf("--web");
  if (webIndex !== -1) {
    const geolibreRoot = args[webIndex + 1];
    if (!geolibreRoot) {
      throw new Error("--web requires a path to the GeoLibre repo root.");
    }
    // Fail fast on a mistyped root so we don't silently build a fake tree.
    const desktopAppDir = join(geolibreRoot, "apps", "geolibre-desktop");
    if (!statSync(desktopAppDir, { throwIfNoEntry: false })?.isDirectory()) {
      throw new Error(
        `--web path is not a GeoLibre repo root: ${desktopAppDir} does not exist.`,
      );
    }
    return {
      mode: "web",
      pluginsDir: join(desktopAppDir, "public", "plugins"),
    };
  }

  const override = process.env.GEOLIBRE_PLUGINS_DIR ?? args[0];
  if (override) return { mode: "desktop", pluginsDir: override };
  return { mode: "desktop", pluginsDir: defaultDesktopPluginsDir() };
}

/** Resolve GeoLibre Desktop's app-data plugins directory for this platform. */
function defaultDesktopPluginsDir() {
  const appId = "org.geolibre.desktop";
  if (process.platform === "win32") {
    const appData = process.env.APPDATA ?? join(homedir(), "AppData", "Roaming");
    return join(appData, appId, "plugins");
  }
  if (process.platform === "darwin") {
    return join(homedir(), "Library", "Application Support", appId, "plugins");
  }
  const dataHome = process.env.XDG_DATA_HOME ?? join(homedir(), ".local", "share");
  return join(dataHome, appId, "plugins");
}

/** Ensure `npm run build:geolibre` has produced the full bundle before copying. */
async function assertBuilt() {
  const required = ["plugin.json", manifest.entry];
  if (manifest.style) required.push(manifest.style);
  for (const rel of required) {
    try {
      await stat(join(bundleDir, rel));
    } catch {
      throw new Error(
        `Missing ${rel} in ${bundleDir}. Run "npm run build:geolibre" first.`,
      );
    }
  }
}
