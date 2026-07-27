/**
 * The GeoDeploy publish/preview transport.
 *
 * Reads the current project as a `.geolibre.json` (via the host's
 * `getProjectSnapshot()` — see host-api.ts) and POSTs it to a GeoDeploy
 * instance's interop endpoints:
 *
 *   - `POST /api/interop/geolibre/preview` — dry run, returns what WOULD import.
 *   - `POST /api/interop/geolibre/publish` — creates + publishes the portal.
 *
 * Auth is a GeoDeploy API token (Settings → API tokens) sent as a Bearer header;
 * the endpoints require the `portal:write` scope.
 */
import type { GeoLibreAppAPI } from "./host-api";

export interface GeoDeploySettings {
  /** Base URL of the GeoDeploy instance, e.g. https://geo.example.org (no trailing /api). */
  baseUrl: string;
  /** A GeoDeploy API token with the portal:write scope. */
  token: string;
}

export interface PreviewLayer {
  name: string;
  geolibre_type: string;
  target: string;
  render_mode: string;
  has_z: boolean;
  feature_count: number | null;
  maplibre_layer_count: number;
  warnings: string[];
}

export interface PreviewResult {
  portal: { title: string; view: unknown; basemap: unknown; story: unknown };
  layers: PreviewLayer[];
  warnings: string[];
}

export interface PublishResult {
  portal_id: number;
  slug: string;
  layer_count: number;
  ingesting: number;
  warnings: string[];
}

/** Error carrying an HTTP status so the UI can distinguish auth (401/403) from bad input (400). */
export class PublishError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "PublishError";
    this.status = status;
  }
}

/**
 * The current project as a parsed `.geolibre.json` object. Requires the host's
 * project-snapshot API; throws a clear, actionable error when the host build
 * doesn't expose it (rather than shipping a styleless partial export).
 */
export function collectProject(app: GeoLibreAppAPI): unknown {
  if (typeof app.getProjectSnapshot === "function") {
    const snapshot = app.getProjectSnapshot();
    try {
      return JSON.parse(snapshot);
    } catch {
      throw new Error("GeoLibre returned an unreadable project snapshot.");
    }
  }
  throw new Error(
    "This GeoLibre build can't hand the whole project (layers + styles + 3D) to a plugin. " +
      "Update GeoLibre to a version with the project-snapshot plugin API, then try again.",
  );
}

export function canPublish(app: GeoLibreAppAPI): boolean {
  return typeof app.getProjectSnapshot === "function";
}

/** Full URL for an interop sub-path, e.g. "geolibre/publish" or "geodeploy/layers". */
function endpoint(settings: GeoDeploySettings, path: string): string {
  return `${settings.baseUrl.replace(/\/+$/, "")}/api/interop/${path}`;
}

async function request<T>(
  settings: GeoDeploySettings,
  method: "GET" | "POST" | "PUT",
  path: string,
  body?: unknown,
): Promise<T> {
  if (!settings.baseUrl.trim()) throw new Error("Set the GeoDeploy URL first.");
  if (!settings.token.trim()) throw new Error("Set a GeoDeploy API token first.");
  let resp: Response;
  try {
    resp = await fetch(endpoint(settings, path), {
      method,
      headers: {
        Authorization: `Bearer ${settings.token.trim()}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    // A network/CORS failure (common when the GeoDeploy instance doesn't allow this origin).
    throw new PublishError(
      `Could not reach GeoDeploy at ${settings.baseUrl}. Check the URL and that the instance allows ` +
        `requests from GeoLibre (CORS). (${(err as Error).message})`,
      0,
    );
  }
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const eb = await resp.json();
      if (eb && typeof eb.detail === "string") detail = eb.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new PublishError(detail, resp.status);
  }
  return (await resp.json()) as T;
}

export function previewProject(app: GeoLibreAppAPI, settings: GeoDeploySettings): Promise<PreviewResult> {
  return request<PreviewResult>(settings, "POST", "geolibre/preview", collectProject(app));
}

export function publishProject(app: GeoLibreAppAPI, settings: GeoDeploySettings): Promise<PublishResult> {
  return request<PublishResult>(settings, "POST", "geolibre/publish", collectProject(app));
}

// ── Write-back round-trip (F5): load a GeoDeploy layer, edit it, save it back ──

export interface EditableLayer {
  id: number;
  name: string;
  geometry_type: string | null;
  feature_count: number | null;
  crs: string | null;
}

export interface WriteBackResult {
  job_id: string;
  layer_id: number;
  status: string;
  features: number;
}

/** PostGIS vector layers on the instance that can be loaded, edited, and written back. */
export function listGeoDeployLayers(settings: GeoDeploySettings): Promise<EditableLayer[]> {
  return request<EditableLayer[]>(settings, "GET", "geodeploy/layers");
}

/** A GeoDeploy layer as an editable GeoJSON FeatureCollection (EPSG:4326). */
export function loadGeoDeployLayer(settings: GeoDeploySettings, layerId: number): Promise<unknown> {
  return request<unknown>(settings, "GET", `geodeploy/layers/${layerId}/features.geojson`);
}

/** Replace a GeoDeploy layer's features with edited GeoJSON (a full re-ingest server-side). */
export function writeBackLayer(
  settings: GeoDeploySettings,
  layerId: number,
  geojson: unknown,
): Promise<WriteBackResult> {
  return request<WriteBackResult>(settings, "PUT", `geodeploy/layers/${layerId}/features`, geojson);
}

/** The current (edited) GeoJSON of a project layer by its GeoLibre id, from a project snapshot. */
export function findLayerGeojson(project: unknown, geolibreLayerId: string): unknown | null {
  const layers = (project as { layers?: Array<{ id?: string; geojson?: unknown }> })?.layers || [];
  const layer = layers.find((l) => l.id === geolibreLayerId);
  return layer?.geojson ?? null;
}

/** The public portal URL for a published slug on the configured instance. */
export function portalUrl(settings: GeoDeploySettings, slug: string): string {
  return `${settings.baseUrl.replace(/\/+$/, "")}/portals/${slug}/`;
}
