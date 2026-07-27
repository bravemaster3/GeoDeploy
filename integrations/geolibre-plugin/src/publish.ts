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

function endpoint(settings: GeoDeploySettings, path: string): string {
  const base = settings.baseUrl.replace(/\/+$/, "");
  return `${base}/api/interop/geolibre/${path}`;
}

async function post<T>(settings: GeoDeploySettings, path: string, project: unknown): Promise<T> {
  if (!settings.baseUrl.trim()) throw new Error("Set the GeoDeploy URL first.");
  if (!settings.token.trim()) throw new Error("Set a GeoDeploy API token first.");
  let resp: Response;
  try {
    resp = await fetch(endpoint(settings, path), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${settings.token.trim()}`,
      },
      body: JSON.stringify(project),
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
      const body = await resp.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new PublishError(detail, resp.status);
  }
  return (await resp.json()) as T;
}

export function previewProject(app: GeoLibreAppAPI, settings: GeoDeploySettings): Promise<PreviewResult> {
  return post<PreviewResult>(settings, "preview", collectProject(app));
}

export function publishProject(app: GeoLibreAppAPI, settings: GeoDeploySettings): Promise<PublishResult> {
  return post<PublishResult>(settings, "publish", collectProject(app));
}

/** The public portal URL for a published slug on the configured instance. */
export function portalUrl(settings: GeoDeploySettings, slug: string): string {
  return `${settings.baseUrl.replace(/\/+$/, "")}/portals/${slug}/`;
}
