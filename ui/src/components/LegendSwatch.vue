<!--
  A legend swatch that looks like what it stands for.

  A square for everything says "this colour appears somewhere". A circle, a line and a filled
  rectangle say WHICH KIND OF THING is that colour — and for a point layer the marker shape is part
  of the symbology, so a square swatch actively misreports a layer drawn with stars.

  Deliberately the same geometry as `templates/shared/portal.js::legendSwatch` / `markerSvg`: the
  published portal draws its legend with those, and a reader who sees the layer in both places must
  not see two different keys. That runtime is a standalone bundle the app cannot import, so the
  shapes are matched by hand — change one, change the other (the parity note in CLAUDE.md).
-->
<template>
  <svg :width="size" :height="size" viewBox="0 0 18 18" class="flex-shrink-0" aria-hidden="true">
    <!-- Line: a stroke, dashed the way the layer is dashed. -->
    <line v-if="geom === 'line'" x1="2" y1="9" x2="16" y2="9"
      :stroke="color" stroke-width="3" stroke-linecap="round"
      :stroke-dasharray="dash === 'dashed' ? '3 2' : (dash === 'dotted' ? '0.6 3' : undefined)" />

    <!-- Polygon: a fill with its outline, at the same 45% the map uses. -->
    <rect v-else-if="geom === 'polygon'" x="2.5" y="4" width="13" height="10"
      :fill="color" fill-opacity="0.45" :stroke="color" stroke-width="1.5" />

    <!-- Raster: a small chequer, standing for a grid of cells. -->
    <g v-else-if="geom === 'raster'">
      <rect x="2" y="2" width="14" height="14" :fill="color" fill-opacity="0.35"
        :stroke="color" stroke-width="1.2" />
      <line x1="9" y1="2" x2="9" y2="16" :stroke="color" stroke-width="1" stroke-opacity="0.6" />
      <line x1="2" y1="9" x2="16" y2="9" :stroke="color" stroke-width="1" stroke-opacity="0.6" />
    </g>

    <!-- Point: the actual marker shape. -->
    <rect v-else-if="marker === 'square'" x="3" y="3" width="12" height="12"
      :fill="color" v-bind="outline" />
    <polygon v-else-if="marker === 'triangle'" points="9,2.5 15.5,15 2.5,15"
      :fill="color" v-bind="outline" />
    <polygon v-else-if="marker === 'diamond'" points="9,2 16,9 9,16 2,9"
      :fill="color" v-bind="outline" />
    <polygon v-else-if="marker === 'star'" :points="starPoints" :fill="color" v-bind="outline" />
    <polygon v-else-if="marker === 'cross'" :points="crossPoints" :fill="color" v-bind="outline" />
    <circle v-else cx="9" cy="9" r="5.5" :fill="color" v-bind="outline" />
  </svg>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  geom: { type: String, default: 'point' },     // point | line | polygon | raster
  color: { type: String, default: '#3b82f6' },
  marker: { type: String, default: 'circle' },
  dash: { type: String, default: 'solid' },     // solid | dashed | dotted
  size: { type: Number, default: 16 },
})

const outline = { stroke: '#fff', 'stroke-width': 1.5, 'stroke-linejoin': 'round' }

// Same construction as portal.js::starPoints / crossPoints, at the swatch's radius.
const starPoints = computed(() => {
  const cx = 9, cy = 9, r = 6.5
  const pts = []
  for (let i = 0; i < 10; i++) {
    const rad = i % 2 ? r * 0.5 : r
    const a = (Math.PI / 5) * i - Math.PI / 2
    pts.push(`${(cx + rad * Math.cos(a)).toFixed(2)},${(cy + rad * Math.sin(a)).toFixed(2)}`)
  }
  return pts.join(' ')
})

const crossPoints = computed(() => {
  const cx = 9, cy = 9, r = 6.5, t = r * 0.38
  return [[-t, -r], [t, -r], [t, -t], [r, -t], [r, t], [t, t], [t, r], [-t, r],
          [-t, t], [-r, t], [-r, -t], [-t, -t]]
    .map(([dx, dy]) => `${cx + dx},${cy + dy}`).join(' ')
})
</script>
