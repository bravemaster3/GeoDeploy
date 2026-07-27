# Upstream patch: expose the project snapshot to plugins (`app.getProjectSnapshot()`)

The "Publish to GeoDeploy" plugin (and any plugin that wants to read the whole project) needs the host
to hand it the current project. GeoLibre already computes exactly this internally — it just isn't on
the plugin API yet. This is a **small, general, additive** change (one optional method); it benefits
every plugin, not only ours.

Target repo: **`opengeos/GeoLibre`**. Verified against the clone at `GeoLibre/` on `main` (2026-07-27).

## The change

### 1. Type — `packages/plugins/src/types.ts` (`GeoLibreAppAPI`)

Add one optional member (near `getProjectState`/`applyProjectState`):

```ts
export interface GeoLibreAppAPI {
  // …existing members…

  /**
   * The current project serialized as a `.geolibre.json` string (the same bytes
   * "Save project" writes). Lets a plugin read the whole project — layers,
   * per-layer LayerStyle, view, basemap, story map — e.g. to publish or export
   * it. Optional so a host build without it degrades gracefully.
   */
  getProjectSnapshot?: () => string;
}
```

### 2. Wiring — `apps/geolibre-desktop/src/hooks/usePlugins.ts`

The app already has both helpers; wire them into the `api` object (the one that starts at
`const api = {` ~line 625, where `mapControllerRef` is already in scope — it's used by `getMap` a few
lines down). Add the import and the method:

```ts
// with the other imports:
import { serializeProject } from "@geolibre/core";
import { buildProjectSnapshot } from "../lib/build-project-snapshot";

// inside `const api = { … }` (next to getMap):
getProjectSnapshot: () => serializeProject(buildProjectSnapshot(mapControllerRef)),
```

`buildProjectSnapshot(mapControllerRef)` → `GeoLibreProject` (`apps/.../lib/build-project-snapshot.ts`)
and `serializeProject` → string (`@geolibre/core`) already exist and are exactly what "Save project"
and live collaboration use (`hooks/useCollaboration.ts`), so this exposes the **same** snapshot with no
new serialization logic.

### 3. Docs — `docs/plugin-api.md`

Add `getProjectSnapshot` to the `GeoLibreAppAPI` interface block and a line under the members list:

> `getProjectSnapshot()` — the current project serialized as a `.geolibre.json` string. Optional;
> call it with optional chaining and handle absence.

### 4. (Optional) Template — `opengeos/geolibre-plugin-template`

Mirror the optional member into that repo's `src/lib/geolibre/host-api.ts` so template-based plugins
get the type without redeclaring it. Not required for functionality — a plugin can declare the member
itself (this one does, in `src/host-api.ts`).

## Why it's safe / uncontroversial
- Purely **additive** and **optional** — no existing behavior changes; no new capability a plugin
  didn't already have transitively (plugins are trusted local code).
- **Read-only** — it serializes current state; it doesn't mutate the project.
- Reuses the **existing** snapshot path (save/collab), so there's nothing new to maintain.

## Test to include
A unit/integration test asserting `app.getProjectSnapshot()` returns a string that `parseProject()`
round-trips (project name + layer count match the store).
