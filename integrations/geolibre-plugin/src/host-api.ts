/**
 * The slice of the GeoLibre host API this plugin uses.
 *
 * This mirrors the canonical contract from `opengeos/geolibre-plugin-template`
 * (`src/lib/geolibre/host-api.ts`), narrowed to what the GeoDeploy publisher
 * needs, plus two members the template's type omits but the real runtime app
 * provides / will provide:
 *
 *  - `getMap()` — present in the live GeoLibre app API (see the app's
 *    `usePlugins.ts`), used only as a last-resort probe.
 *  - `getProjectSnapshot()` — the **project-snapshot host API** this plugin
 *    depends on for full fidelity: it returns the current project serialized as
 *    a `.geolibre.json` string (the app already computes this internally via
 *    `serializeProject(buildProjectSnapshot(...))`; exposing it to plugins is a
 *    small upstream addition — GeoDeploy interop Front 4). It is typed optional
 *    so the plugin degrades to a clear "update GeoLibre" message when a host
 *    build predates it, rather than silently shipping a styleless 2D export.
 */

export type GeoLibreMapControlPosition =
  | "top-left"
  | "top-right"
  | "bottom-left"
  | "bottom-right";

export interface GeoLibreRightPanelRegistration {
  id: string;
  title: string;
  icon?: string;
  defaultWidth?: number;
  render: (container: HTMLElement) => void | (() => void);
  onOpen?: () => void;
  onClose?: () => void;
}

export interface GeoLibreToolbarMenuAction {
  type?: "action";
  id: string;
  label: string;
  icon?: string;
  disabled?: boolean;
  onSelect: () => void;
}

export interface GeoLibreToolbarMenu {
  id: string;
  label: string;
  icon?: string;
  items: GeoLibreToolbarMenuAction[];
}

/** Minimal MapLibre map handle — we only ever read the style as a fallback probe. */
export interface GeoLibreMapLike {
  getStyle: () => unknown;
}

export interface GeoLibreAppAPI {
  registerRightPanel?: (panel: GeoLibreRightPanelRegistration) => () => void;
  unregisterRightPanel?: (id: string) => void;
  openRightPanel?: (id: string) => boolean;
  closeRightPanel?: (id: string) => void;
  registerToolbarMenu?: (menu: GeoLibreToolbarMenu) => () => void;
  unregisterToolbarMenu?: (id: string) => void;
  /** Live app API (not in the template's typed subset); a last-resort probe only. */
  getMap?: () => GeoLibreMapLike | null;
  /** Upstream project-snapshot API — the full-fidelity source of the `.geolibre.json`. */
  getProjectSnapshot?: () => string;
}

export interface GeoLibrePlugin {
  id: string;
  name: string;
  version: string;
  activate: (app: GeoLibreAppAPI) => boolean | void;
  deactivate: (app: GeoLibreAppAPI) => void;
  getProjectState?: () => unknown;
  applyProjectState?: (app: GeoLibreAppAPI, state: unknown) => boolean | void;
}
