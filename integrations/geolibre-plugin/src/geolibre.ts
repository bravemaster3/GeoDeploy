/**
 * GeoLibre plugin entry point: "Publish to GeoDeploy".
 *
 * Registers a right-sidebar panel (the publish form) and a toolbar menu to open
 * it. Connection settings (GeoDeploy URL + API token) persist with the project
 * via getProjectState/applyProjectState, so they survive save/reopen. The panel
 * itself lives in panel.ts; the transport in publish.ts.
 */
import "./style.css";

import type { GeoLibreAppAPI, GeoLibrePlugin } from "./host-api";
import { renderPublishPanel } from "./panel";
import type { GeoDeploySettings } from "./publish";

const PANEL_ID = "geodeploy-publish";
const MENU_ID = "geodeploy-publish-menu";

interface PluginState {
  baseUrl?: string;
  token?: string;
  /** Round-trip links: GeoLibre layer id → GeoDeploy layer id. */
  links?: Record<string, number>;
}

let settings: GeoDeploySettings = { baseUrl: "", token: "" };
let links: Record<string, number> = {};
let disposePanel: (() => void) | null = null;
let disposeMenu: (() => void) | null = null;

function isPluginState(value: unknown): value is PluginState {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    (v.baseUrl === undefined || typeof v.baseUrl === "string") &&
    (v.token === undefined || typeof v.token === "string") &&
    (v.links === undefined || (typeof v.links === "object" && v.links !== null))
  );
}

export const plugin: GeoLibrePlugin = {
  id: "geodeploy-publish",
  name: "Publish to GeoDeploy",
  version: "0.1.0",

  activate(app: GeoLibreAppAPI) {
    // Right panel: the publish form. render() runs once with an empty container.
    disposePanel =
      app.registerRightPanel?.({
        id: PANEL_ID,
        title: "Publish to GeoDeploy",
        defaultWidth: 340,
        render(container) {
          return renderPublishPanel(container, {
            app,
            getSettings: () => settings,
            setSettings: (patch) => {
              settings = { ...settings, ...patch };
            },
            getLinks: () => links,
            setLink: (geolibreLayerId, geodeployLayerId) => {
              links = { ...links, [geolibreLayerId]: geodeployLayerId };
            },
          });
        },
      }) ?? null;

    // Toolbar menu to open the panel (the panel isn't shown until opened).
    disposeMenu =
      app.registerToolbarMenu?.({
        id: MENU_ID,
        label: "GeoDeploy",
        items: [
          {
            id: "open-publish",
            label: "Publish to GeoDeploy…",
            onSelect: () => app.openRightPanel?.(PANEL_ID),
          },
        ],
      }) ?? null;
  },

  deactivate(app: GeoLibreAppAPI) {
    app.closeRightPanel?.(PANEL_ID);
    disposeMenu?.();
    disposeMenu = null;
    disposePanel?.();
    disposePanel = null;
  },

  // Persist the connection settings + round-trip links with the project.
  getProjectState() {
    return { ...settings, links };
  },

  applyProjectState(_app, state) {
    if (!isPluginState(state)) return false;
    settings = { baseUrl: state.baseUrl ?? "", token: state.token ?? "" };
    links = (state.links as Record<string, number>) ?? {};
  },
};

export default plugin;
