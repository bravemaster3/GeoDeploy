// What the "tile this layer" control says, and what it asks before it runs.
//
// Two surfaces offer it — the row in My Data and the layer's own page — and they were drifting:
// one said "Tile…" for a layer that was already tiled, and neither warned that pressing it throws
// away a finished archive and rebuilds it. Tiling a large layer is minutes of worker time, so that
// is a real cost to trigger by accident. One definition here, imported by both.

/** Has this layer got a finished PMTiles archive? */
export function isTiled(layer) {
  return layer?.tile_status === 'ready'
}

/** Is tiling running right now? */
export function isTiling(layer) {
  return layer?.tile_status === 'tiling'
}

/** The control's tooltip — never "Tile…" for something already tiled. */
export function tileTitle(layer) {
  if (isTiling(layer)) return 'Tiling…'
  if (isTiled(layer)) return 'Restart tiling — this layer is already tiled (rebuilds the PMTiles archive)'
  return 'Tile for fast seamless display (PMTiles)'
}

/** The control's label, where there is room for one. */
export function tileLabel(layer) {
  if (isTiling(layer)) return 'Tiling…'
  return isTiled(layer) ? 'Restart tiling' : 'Tile for fast display'
}

/**
 * True to go ahead. Silent for a first tiling — nothing is at stake there; a confirmation for a
 * restart, which discards a working archive and takes the layer back through the whole job.
 */
export function confirmTiling(layer) {
  if (!isTiled(layer)) return true
  return window.confirm(
    `Tiling is already complete for "${layer?.name || 'this layer'}".\n\n` +
    'Restarting it rebuilds the PMTiles archive from scratch. That can take several minutes on a ' +
    'large layer, and the current tiles keep serving until the new ones are ready.\n\n' +
    'Restart tiling?')
}
