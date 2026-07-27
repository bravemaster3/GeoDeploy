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

let settings: GeoDeploySettings = { baseUrl: "", token: "" };
let disposePanel: (() => void) | null = null;
let disposeMenu: (() => void) | null = null;

function isSettings(value: unknown): value is Partial<GeoDeploySettings> {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    (v.baseUrl === undefined || typeof v.baseUrl === "string") &&
    (v.token === undefined || typeof v.token === "string")
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

  // Persist the connection settings with the project.
  getProjectState() {
    return { ...settings };
  },

  applyProjectState(_app, state) {
    if (isSettings(state)) settings = { baseUrl: "", token: "", ...state };
  },
};

export default plugin;
