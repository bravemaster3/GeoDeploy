<template>
  <div class="flex items-center gap-1.5 py-1 px-1 rounded hover:bg-muted/60">
    <span class="text-muted-foreground/40 cursor-grab flex-shrink-0 flex items-center" draggable="true"
      @dragstart="$emit('dragstart', $event)" @dragend="$emit('dragend', $event)"
      title="Drag to reorder / into a folder" v-html="dragSvg"></span>
    <button @click="toggleVisible" class="text-muted-foreground/70 hover:text-foreground flex-shrink-0 flex items-center"
      :class="{ 'opacity-50': !visible }" :title="visible ? 'Hide' : 'Show'" v-html="visible ? eyeSvg : eyeOffSvg"></button>
    <button ref="swatchBtn" @click.stop="toggleStyle"
      class="flex-shrink-0 flex items-center justify-center w-[22px] h-[22px] rounded hover:bg-muted"
      :class="config.layer_type === 'raster' ? 'text-amber-400' : ''" :title="geomLabel" v-html="geomSvg"></button>
    <span class="text-xs font-medium flex-1 truncate" :class="visible ? '' : 'text-muted-foreground/70'" :title="layerName">{{ layerName }}</span>
    <button @click="$emit('zoom')" class="text-muted-foreground/70 hover:text-primary flex-shrink-0" title="Zoom to layer">
      <LocateIcon class="w-3.5 h-3.5" />
    </button>
    <button @click="$emit('remove')" class="text-muted-foreground/70 hover:text-red-500 flex-shrink-0" title="Remove">
      <TrashIcon class="w-3.5 h-3.5" />
    </button>

    <!-- Symbology popover (opens from the swatch) -->
    <Teleport to="body">
      <div v-if="showStyle" ref="popEl" :style="popStyle"
        class="fixed z-[60] bg-card border border-border rounded-lg shadow-xl text-foreground/85">
        <div class="flex items-center justify-between gap-2 px-3 py-2 border-b border-border/60 text-xs font-semibold">
          <span class="truncate">{{ layerName }}</span>
          <button @click="showStyle = false" class="text-muted-foreground/70 hover:text-foreground text-lg leading-none flex-shrink-0">&times;</button>
        </div>
        <div class="px-3 py-2.5 space-y-3 max-h-[70vh] overflow-auto">

          <!-- Opacity (all layers) -->
          <div>
            <div class="flex items-center justify-between mb-0.5">
              <label class="text-xs text-muted-foreground">Opacity</label>
              <span class="text-xs text-muted-foreground/70">{{ Math.round(config.opacity * 100) }}%</span>
            </div>
            <input type="range" min="0" max="1" step="0.05" :value="config.opacity"
              @input="$emit('update', { opacity: parseFloat($event.target.value) })"
              class="w-full h-1 accent-primary" />
          </div>

          <!-- Vector style controls -->
          <template v-if="config.layer_type === 'vector'">
            <!-- COLOUR: one symbol, or a function of a field. The mode picker comes first because
                 it decides what the rest of this section even means. -->
            <div>
              <label class="text-xs text-muted-foreground">Color</label>
              <div v-if="styleFields.length" class="flex gap-1 mt-1 mb-1.5">
                <button v-for="m in COLOR_MODES" :key="m.value" type="button" @click="setColorMode(m.value)"
                  class="flex-1 text-[11px] py-1 rounded border transition-colors"
                  :class="colorMode === m.value
                    ? 'border-primary/60 bg-primary/15 text-foreground'
                    : 'border-border text-muted-foreground hover:text-foreground'">{{ m.label }}</button>
              </div>

              <div v-if="colorMode === 'single'" class="flex items-center gap-2 mt-0.5">
                <input type="color" :value="config.style?.color || '#3b82f6'"
                  @input="emitStyle({ color: $event.target.value })"
                  class="w-6 h-6 rounded border border-border cursor-pointer p-0" />
                <span class="text-xs text-muted-foreground/70 font-mono">{{ config.style?.color || '#3b82f6' }}</span>
              </div>

              <div v-else class="space-y-2">
                <select :value="config.style?.color_field || ''" @change="pickColorField($event.target.value)"
                  class="w-full text-xs border border-border rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-primary/60">
                  <option value="">Choose a field…</option>
                  <option v-for="f in colorFields" :key="f.name" :value="f.name">{{ f.name }}</option>
                </select>

                <div v-if="colorMode === 'graduated' && config.style?.color_field" class="flex gap-1.5">
                  <label class="flex-1">
                    <span class="text-[11px] text-muted-foreground">Classes</span>
                    <input type="number" min="2" max="12" :value="classCount" @change="setClassCount($event.target.value)"
                      class="w-full text-xs border border-border rounded px-1.5 py-1 mt-0.5" />
                  </label>
                  <label class="flex-[1.4]">
                    <span class="text-[11px] text-muted-foreground">Method</span>
                    <select :value="classMethod" @change="setMethod($event.target.value)"
                      class="w-full text-xs border border-border rounded px-1.5 py-1 mt-0.5">
                      <option value="quantile">Quantile</option>
                      <option value="equal">Equal interval</option>
                      <option value="jenks">Natural breaks</option>
                    </select>
                  </label>
                </div>

                <div v-if="colorMode === 'graduated' && config.style?.color_field" class="block">
                  <span class="text-[11px] text-muted-foreground">Colour ramp</span>
                  <div class="flex items-center gap-1 mt-0.5">
                    <select :value="ramp" @change="setRamp($event.target.value)"
                      class="flex-1 min-w-0 text-xs border border-border rounded px-1.5 py-1">
                      <optgroup label="Sequential">
                        <option v-for="r in SEQUENTIAL" :key="r" :value="r">{{ r }}</option>
                      </optgroup>
                      <optgroup label="Diverging (has a midpoint)">
                        <option v-for="r in DIVERGING" :key="r" :value="r">{{ r }}</option>
                      </optgroup>
                    </select>
                    <!-- Which end means "high" is a cartographic choice, not a property of the
                         ramp: on a dark basemap the light end often belongs to the low values. -->
                    <button type="button" @click="toggleRampReverse"
                      :aria-pressed="rampReverse"
                      :title="rampReverse ? 'Ramp reversed — click to restore' : 'Reverse the ramp'"
                      class="shrink-0 text-xs border border-border rounded px-1.5 py-1"
                      :class="rampReverse ? 'bg-primary/15 border-primary/40' : ''">⇄</button>
                  </div>
                  <div class="flex h-1.5 mt-1 rounded overflow-hidden" aria-hidden="true">
                    <span v-for="(c, i) in rampPreview" :key="i" class="flex-1"
                      :style="{ backgroundColor: c }"></span>
                  </div>
                </div>

                <p v-if="statsBusy" class="text-[11px] text-muted-foreground/70">Reading the field…</p>
                <p v-else-if="statsError" class="text-[11px] text-red-400">{{ statsError }}</p>

                <!-- The legend, editable. Each swatch is the actual colour the map will use, so this
                     doubles as the preview of the classification. -->
                <div v-if="legend.length" class="space-y-0.5 max-h-40 overflow-y-auto pr-1">
                  <div v-for="(e, i) in legend" :key="i" class="flex items-center gap-1.5">
                    <input type="color" :value="e.color" @input="setEntryColor(i, $event.target.value)"
                      :disabled="e.isOther"
                      class="w-4 h-4 rounded border border-border cursor-pointer p-0 flex-shrink-0 disabled:opacity-60" />
                    <span class="text-[11px] text-muted-foreground truncate">{{ e.label }}</span>
                  </div>
                </div>
                <p v-if="truncatedCats" class="text-[11px] text-amber-300/80">
                  Showing the {{ (config.style?.categories || []).length }} commonest values; the rest
                  draw in the “Other” colour.
                </p>
              </div>
            </div>

            <template v-if="geomType === 'polygon'">
              <div>
                <div class="flex items-center justify-between mb-0.5">
                  <label class="text-xs text-muted-foreground">Fill opacity</label>
                  <span class="text-xs text-muted-foreground/70">{{ Math.round((config.style?.fill_opacity ?? 0.45) * 100) }}%</span>
                </div>
                <input type="range" min="0" max="1" step="0.05" :value="config.style?.fill_opacity ?? 0.45"
                  @input="emitStyle({ fill_opacity: parseFloat($event.target.value) })" class="w-full h-1 accent-primary" />
              </div>
              <div>
                <label class="text-xs text-muted-foreground">Outline color</label>
                <div class="flex items-center gap-2 mt-0.5">
                  <input type="color" :value="outlineSwatch('#1d4ed8')" :disabled="noOutline"
                    @input="emitStyle({ outline_color: $event.target.value })"
                    class="w-6 h-6 rounded border border-border cursor-pointer p-0 disabled:opacity-40" />
                  <label class="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                    <input type="checkbox" :checked="noOutline" @change="setNoOutline($event.target.checked)"
                      class="accent-primary" />
                    None
                  </label>
                  <span v-if="!noOutline" class="text-xs text-muted-foreground/70 font-mono">{{ outlineSwatch('#1d4ed8') }}</span>
                </div>
              </div>

            </template>

            <div v-else-if="geomType === 'line'" class="space-y-2">
              <div>
                <label class="text-xs text-muted-foreground">Line width</label>
                <div class="flex items-center gap-2 mt-0.5">
                  <input type="number" min="0.5" max="20" step="0.5" :value="config.style?.line_width ?? 2"
                    @input="emitStyle({ line_width: parseFloat($event.target.value) })"
                    class="w-16 text-xs border border-border rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-primary/60" />
                  <span class="text-xs text-muted-foreground/70">px</span>
                </div>
              </div>
              <div>
                <label class="text-xs text-muted-foreground">Line style</label>
                <select :value="config.style?.lineType || 'solid'" @change="emitStyle({ lineType: $event.target.value })"
                  class="mt-0.5 w-full text-xs border border-border rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-primary/60">
                  <option value="solid">Solid</option>
                  <option value="dashed">Dashed</option>
                  <option value="dotted">Dotted</option>
                </select>
              </div>
            </div>

            <div v-else-if="geomType === 'point'" class="space-y-2">
              <div>
                <label class="text-xs text-muted-foreground">Marker shape</label>
                <select :value="config.style?.marker || 'circle'" @change="emitStyle({ marker: $event.target.value })"
                  class="mt-0.5 w-full text-xs border border-border rounded px-1.5 py-1 capitalize focus:outline-none focus:ring-1 focus:ring-primary/60">
                  <option v-for="s in markerShapes" :key="s" :value="s">{{ s }}</option>
                </select>
              </div>
              <div>
                <label class="text-xs text-muted-foreground">Point size</label>
                <div class="flex items-center gap-2 mt-0.5">
                  <input type="number" min="1" max="30" step="1" :value="config.style?.radius ?? 5"
                    @input="emitStyle({ radius: parseFloat($event.target.value) })"
                    class="w-16 text-xs border border-border rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-primary/60" />
                  <span class="text-xs text-muted-foreground/70">px</span>
                </div>
              </div>
              <!-- Marker outline. Points had a hard-coded white stroke and no way to change or
                   remove it. The width is a RATIO of the marker size, so it stays proportional when
                   the layer is resized — and a wide one hides the fill entirely, which is how you
                   draw a RING. -->
              <div>
                <label class="text-xs text-muted-foreground">Outline</label>
                <div class="flex items-center gap-2 mt-0.5">
                  <input type="color" :value="outlineSwatch('#ffffff')" :disabled="noOutline"
                    @input="emitStyle({ outline_color: $event.target.value })"
                    class="w-6 h-6 rounded border border-border cursor-pointer p-0 disabled:opacity-40" />
                  <label class="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                    <input type="checkbox" :checked="noOutline" @change="setNoOutline($event.target.checked)"
                      class="accent-primary" />
                    None
                  </label>
                </div>
                <div v-if="!noOutline" class="flex items-center gap-2 mt-1.5">
                  <span class="text-[11px] text-muted-foreground flex-shrink-0">Thickness</span>
                  <input type="range" min="0" max="1" step="0.04"
                    :value="config.style?.outline_width ?? 0.28"
                    @input="emitStyle({ outline_width: parseFloat($event.target.value) })"
                    class="flex-1 h-1 accent-primary" />
                  <span class="text-[11px] text-muted-foreground/70 w-8 text-right">
                    {{ Math.round((config.style?.outline_width ?? 0.28) * 100) }}%
                  </span>
                </div>
                <p v-if="!noOutline && (config.style?.outline_width ?? 0.28) > 0.6"
                   class="text-[11px] text-muted-foreground/70 mt-1">
                  At this thickness the fill is hidden — the marker reads as a ring.
                </p>
              </div>
            </div>

            <!-- 3D. Polygons extrude directly (MapLibre raises a fill); POINTS become pillars —
                 a column standing at each location, served as a buffered polygon by the shared
                 Martin function (services/pillars), so the layer keeps the renderer it already had.
                 Lines are excluded: there is no sensible column for a line, and QGIS/GeoLibre do not
                 offer one either. Hidden without a numeric field — an enabled switch that cannot do
                 anything is worse than an absent one. -->
            <div v-if="canExtrude" class="pt-1 border-t border-border/50">
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" :checked="!!config.style?.extrusion?.enabled"
                  @change="setExtrusion({ enabled: $event.target.checked })" class="accent-primary" />
                <span class="text-xs text-foreground">
                  {{ geomType === 'point' ? '3D — bars by a field' : '3D — extrude by a field' }}
                </span>
              </label>
              <div v-if="config.style?.extrusion?.enabled" class="mt-1.5 space-y-1.5 pl-5">
                <select :value="config.style?.extrusion?.field || ''"
                  @change="setExtrusion({ field: $event.target.value })"
                  class="w-full text-xs border border-border rounded px-1.5 py-1">
                  <option value="">Choose a height field…</option>
                  <option v-for="f in numericFields" :key="f.name" :value="f.name">{{ f.name }}</option>
                </select>
                <label class="flex items-center gap-2">
                  <span class="text-[11px] text-muted-foreground flex-shrink-0">Height ×</span>
                  <input type="number" min="0.01" step="0.5" :value="config.style?.extrusion?.scale ?? 1"
                    @change="setExtrusion({ scale: parseFloat($event.target.value) || 1 })"
                    class="w-20 text-xs border border-border rounded px-1.5 py-0.5" />
                </label>
                <!-- Points only: a polygon already has a footprint, a point has none — so the bar
                     needs a width before it can be drawn at all. (The code calls these "pillars"
                     — services/pillars.py, and the tile function name is baked into published
                     portals' URLs — but "bars" is the word people use, so the UI says that.) -->
                <label v-if="geomType === 'point'" class="flex items-center gap-2">
                  <span class="text-[11px] text-muted-foreground flex-shrink-0">Bar radius</span>
                  <input type="number" min="0.5" step="5" :value="config.style?.extrusion?.radius ?? defaultRadius"
                    @change="setExtrusion({ radius: parseFloat($event.target.value) || defaultRadius })"
                    class="w-20 text-xs border border-border rounded px-1.5 py-0.5" />
                  <span class="text-[11px] text-muted-foreground/70">m</span>
                </label>
                <p class="text-[11px] text-muted-foreground/70 leading-snug">
                  The field is in metres. Use the multiplier when it is not — floors × 3, say.
                  The map tilts so you can see it.
                </p>
              </div>
            </div>

            <!-- Popup fields -->
            <div v-if="layer?.columns?.length">
              <div class="flex items-center justify-between mb-1">
                <label class="text-xs text-muted-foreground">Popup fields</label>
                <button v-if="config.popup_fields?.length" @click="$emit('update', { popup_fields: [] })"
                  class="text-xs text-primary hover:text-primary/80">Reset (all)</button>
              </div>
              <div class="space-y-0.5 max-h-36 overflow-y-auto pr-1">
                <label v-for="col in layer.columns" :key="col.name" class="flex items-center gap-1.5 text-xs py-0.5 cursor-pointer group">
                  <input type="checkbox" :checked="isFieldSelected(col.name)" @change="toggleField(col.name, $event.target.checked)"
                    class="accent-primary flex-shrink-0" />
                  <span class="truncate group-hover:text-foreground transition-colors">{{ col.name }}</span>
                  <span class="text-muted-foreground/40 ml-auto flex-shrink-0 font-mono text-[10px]">{{ shortType(col.type) }}</span>
                </label>
              </div>
            </div>
          </template>

          <!-- Raster styling -->
          <template v-else-if="config.layer_type === 'raster'">
            <!-- Band selection (multiband rasters only) -->
            <template v-if="bandCount > 1">
              <div>
                <label class="text-xs text-muted-foreground">Bands</label>
                <select :value="bandMode" @change="setBandMode($event.target.value)"
                  class="mt-0.5 w-full text-xs border border-border rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-primary/60">
                  <option value="rgb">RGB composite</option>
                  <option value="single">Single band</option>
                </select>
              </div>
              <div v-if="bandMode === 'rgb'" class="flex items-center gap-2">
                <div v-for="(chan, i) in ['R', 'G', 'B']" :key="chan" class="flex items-center gap-1">
                  <label class="text-xs font-medium" :class="['text-red-500','text-green-400','text-blue-500'][i]">{{ chan }}</label>
                  <select :value="rgbBands[i]" @change="setRgbBand(i, $event.target.value)"
                    class="text-xs border border-border rounded px-1 py-0.5 focus:outline-none focus:ring-1 focus:ring-primary/60">
                    <option v-for="b in bandList" :key="b" :value="b">{{ b }}</option>
                  </select>
                </div>
              </div>
              <div v-else>
                <label class="text-xs text-muted-foreground">Band</label>
                <select :value="singleBand" @change="setSingleBand($event.target.value)"
                  class="mt-0.5 w-full text-xs border border-border rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-primary/60">
                  <option v-for="b in bandList" :key="b" :value="b">Band {{ b }}</option>
                </select>
              </div>
            </template>

            <!-- Palette + hillshade: single-band raster, or a multiband raster in single-band mode -->
            <template v-if="bandCount === 1 || bandMode === 'single'">
              <div>
                <label class="text-xs text-muted-foreground">Color palette</label>
                <select :value="config.style?.colormap || ''" :disabled="config.style?.algorithm === 'hillshade'"
                  @change="emitStyle({ colormap: $event.target.value || null })"
                  class="mt-0.5 w-full text-xs border border-border rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-primary/60 disabled:opacity-50">
                  <option value="">None (grayscale)</option>
                  <option v-for="cm in colormaps" :key="cm" :value="cm">{{ cm }}</option>
                </select>
              </div>
              <div class="flex items-center gap-3">
                <label class="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                  <input type="checkbox" :checked="config.style?.algorithm === 'hillshade'"
                    @change="emitStyle({ algorithm: $event.target.checked ? 'hillshade' : null })" class="accent-primary flex-shrink-0" />
                  Hillshade
                </label>
                <div v-if="config.style?.algorithm === 'hillshade'" class="flex items-center gap-1.5" title="Vertical exaggeration (Z factor)">
                  <label class="text-xs text-muted-foreground">Z</label>
                  <input type="number" min="0.1" max="10" step="0.1" :value="config.style?.zfactor ?? 1"
                    @input="emitStyle({ zfactor: parseFloat($event.target.value) || 1 })"
                    class="w-14 text-xs border border-border rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-primary/60" />
                </div>
              </div>
            </template>

            <!-- Stretch is disabled under hillshade: the algorithm returns a finished 0–255 relief
                 image and TiTiler applies rescale AFTER it, so a data-range stretch would flatten
                 the shading to one colour. Saying so beats letting the control look available. -->
            <div :class="isHillshade ? 'opacity-50' : ''">
              <div class="flex items-center justify-between mb-0.5">
                <label class="text-xs text-muted-foreground">Stretch (min / max)</label>
                <button @click="autoStretch" :disabled="autoStretching || isHillshade"
                  class="text-xs text-primary hover:text-primary/80 font-medium disabled:opacity-50"
                  title="Compute min/max from the raster (2–98th percentile)">
                  {{ autoStretching ? 'Computing…' : '⚡ Auto' }}
                </button>
              </div>
              <div class="flex items-center gap-2">
                <input type="number" :value="rescaleMin" :disabled="isHillshade" @input="setRescale('min', $event.target.value)" placeholder="min"
                  class="w-16 text-xs border border-border rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-primary/60 disabled:opacity-50" />
                <span class="text-muted-foreground/40">–</span>
                <input type="number" :value="rescaleMax" :disabled="isHillshade" @input="setRescale('max', $event.target.value)" placeholder="max"
                  class="w-16 text-xs border border-border rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-primary/60 disabled:opacity-50" />
              </div>
              <p class="text-[10px] text-muted-foreground/70 mt-0.5">
                {{ isHillshade ? 'Not used while Hillshade is on — the shading is already 0–255.'
                               : 'For non-8-bit imagery (e.g. 0–4095). Blank = default.' }}
              </p>
            </div>
          </template>

          <!-- External source (WMS / XYZ / WFS) -->
          <template v-else-if="config.layer_type === 'external'">
            <div v-if="layer?.kind === 'vector'">
              <label class="text-xs text-muted-foreground">Color</label>
              <div class="flex items-center gap-2 mt-0.5">
                <input type="color" :value="config.style?.color || '#3b82f6'"
                  @input="emitStyle({ color: $event.target.value })"
                  class="w-6 h-6 rounded border border-border cursor-pointer p-0" />
                <span class="text-xs text-muted-foreground/70 font-mono">{{ config.style?.color || '#3b82f6' }}</span>
              </div>
            </div>
            <p class="text-[10px] text-muted-foreground/70">
              External {{ layer?.source_type?.toUpperCase() }} source — served by the provider.
              <span v-if="layer?.attribution">© {{ layer.attribution }}</span>
            </p>
          </template>

          <!-- Default style actions (not applicable to external sources) -->
          <div v-if="config.layer_type !== 'external'" class="flex items-center gap-2 pt-1 border-t border-border/60">
            <button v-if="layer?.default_style" @click="useDefault" class="text-xs text-primary hover:text-primary/80 font-medium"
              title="Apply saved default style to this portal">↩ Use default</button>
            <button @click="saveDefault" :disabled="savingDefault" class="text-xs text-muted-foreground hover:text-foreground ml-auto"
              title="Save current style as the default for this layer">{{ savingDefault ? 'Saving…' : '⭐ Save as default' }}</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useDataStore } from '@/stores/data'
import { saveVectorDefaultStyle, saveRasterDefaultStyle, listColormaps, getRasterStats,
         getFieldStats } from '@/api'
// The shared symbology vocabulary — twin of api/geodeploy/services/symbology.py. The swatch and
// the legend here must describe exactly what the published portal will draw.
import { RAMPS, DIVERGING, NO_OUTLINE, markerOutline, legendEntries, rampColors,
         representativeColor, pillarRadius } from '@/lib/symbology'
import { TrashIcon, LocateIcon } from '@/views/icons'

const props = defineProps({ config: Object })
const emit = defineEmits(['remove', 'update', 'zoom', 'dragstart', 'dragend'])

const dataStore = useDataStore()
const savingDefault = ref(false)
const colormaps = ref([])

const showStyle = ref(false)
const swatchBtn = ref(null)
const popEl = ref(null)
const popStyle = ref({})

const dragSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/><circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/></svg>'
const eyeSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/></svg>'
const eyeOffSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.94 10.94 0 0 1 12 19c-7 0-11-7-11-7a18.5 18.5 0 0 1 5.06-5.94M9.9 4.24A11 11 0 0 1 12 4c7 0 11 7 11 7a18.5 18.5 0 0 1-2.16 3.19M1 1l22 22"/></svg>'

const visible = computed(() => props.config.visible !== false)
function toggleVisible() { emit('update', { visible: !visible.value }) }

function toggleStyle() {
  showStyle.value = !showStyle.value
  if (showStyle.value) nextTick(positionPop)
}
function positionPop() {
  const el = swatchBtn.value
  if (!el) return
  const r = el.getBoundingClientRect()
  // Widened from 230: the panel now carries a colour-mode picker, a field, class count/method/ramp,
  // an editable legend, marker + outline controls and a 3D block. At 230 the labelled rows wrapped.
  const w = 288
  let left = r.right + 8
  if (left + w > window.innerWidth) left = Math.max(8, r.left - w - 8)
  popStyle.value = { left: left + 'px', top: Math.min(r.top, window.innerHeight - 380) + 'px', width: w + 'px' }
}
function onDocClick(e) {
  if (!showStyle.value) return
  if (popEl.value && !popEl.value.contains(e.target) && swatchBtn.value && !swatchBtn.value.contains(e.target)) {
    showStyle.value = false
  }
}
onMounted(async () => {
  document.addEventListener('mousedown', onDocClick)
  if (props.config.layer_type === 'raster') {
    try { const { data } = await listColormaps(); colormaps.value = data } catch {}
  }
})
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocClick))

const layer = computed(() => {
  if (props.config.layer_type === 'external') return dataStore.externalSources.find(s => s.id === props.config.layer_id) || null
  const list = props.config.layer_type === 'vector' ? dataStore.vectorLayers : dataStore.rasterLayers
  return list.find(l => l.id === props.config.layer_id) || null
})

const layerName = computed(() => layer.value?.name || `Layer ${props.config.layer_id}`)

const geomType = computed(() => {
  const g = (layer.value?.geometry_type || '').toLowerCase()
  if (g.includes('polygon')) return 'polygon'
  if (g.includes('line')) return 'line'
  if (g.includes('point')) return 'point'
  return 'unknown'
})

const geomKind = computed(() => {
  if (props.config.layer_type === 'raster') return 'raster'
  if (props.config.layer_type === 'external') return layer.value?.kind === 'raster' ? 'raster' : geomType.value
  return geomType.value
})
const geomLabel = computed(() => ({
  polygon: 'Polygons', line: 'Lines', point: 'Points', raster: 'Raster',
}[geomKind.value] || 'Vector'))

// Legend swatch mirroring the layer's actual symbol — colour + line dash for vectors.
const geomSvg = computed(() => {
  const k = geomKind.value
  // The swatch has to stand for the WHOLE layer, so under a classification it shows the middle
  // class rather than the flat `color` (which a data-driven layer no longer uses anywhere). A
  // swatch showing a colour that appears nowhere on the map is a small lie told constantly.
  const col = representativeColor(props.config.style || {})
  if (k === 'polygon')
    return `<svg width="18" height="18" viewBox="0 0 18 18"><rect x="2.5" y="4" width="13" height="10" fill="${col}" fill-opacity="0.45" stroke="${col}" stroke-width="1.5"/></svg>`
  if (k === 'line') {
    const lt = props.config.style?.lineType
    const da = lt === 'dashed' ? ' stroke-dasharray="3 2"' : lt === 'dotted' ? ' stroke-dasharray="0.6 3"' : ''
    return `<svg width="18" height="18" viewBox="0 0 18 18"><line x1="2" y1="9" x2="16" y2="9" stroke="${col}" stroke-width="3" stroke-linecap="round"${da}/></svg>`
  }
  if (k === 'raster')
    return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="1"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/></svg>'
  return `<svg width="18" height="18" viewBox="0 0 18 18">${markerSvg(props.config.style?.marker || 'circle', col)}</svg>`
})

const markerShapes = ['circle', 'square', 'triangle', 'diamond', 'star', 'cross']
function starPts(cx, cy, r) {
  const p = []
  for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + i * Math.PI / 5, rr = (i % 2) ? r * 0.45 : r; p.push((cx + Math.cos(a) * rr).toFixed(1) + ',' + (cy + Math.sin(a) * rr).toFixed(1)) }
  return p.join(' ')
}
function crossPts(cx, cy, r) {
  const t = r * 0.38
  return [[-t, -r], [t, -r], [t, -t], [r, -t], [r, t], [t, t], [t, r], [-t, r], [-t, t], [-r, t], [-r, -t], [-t, -t]]
    .map(d => (cx + d[0]).toFixed(1) + ',' + (cy + d[1]).toFixed(1)).join(' ')
}
function markerSvg(shape, c) {
  // Mirrors the canvas marker: no outline means none here either, or the list swatch would keep a
  // white ring the map does not draw.
  const [oc] = markerOutline(props.config.style || {})
  const s = oc ? ` stroke="${oc}" stroke-width="1.5" stroke-linejoin="round"` : ''
  if (shape === 'square') return `<rect x="3" y="3" width="12" height="12" fill="${c}"${s}/>`
  if (shape === 'triangle') return `<polygon points="9,2.5 15.5,15 2.5,15" fill="${c}"${s}/>`
  if (shape === 'diamond') return `<polygon points="9,2 16,9 9,16 2,9" fill="${c}"${s}/>`
  if (shape === 'star') return `<polygon points="${starPts(9, 9, 6.5)}" fill="${c}"${s}/>`
  if (shape === 'cross') return `<polygon points="${crossPts(9, 9, 6.5)}" fill="${c}"${s}/>`
  return `<circle cx="9" cy="9" r="5.5" fill="${c}"${s}/>`
}

function emitStyle(patch) {
  emit('update', { style: { ...props.config.style, ...patch } })
}

// ── Data-driven symbology ────────────────────────────────────────────────────
// The class BREAKS are computed on the server (`GET /data/vector/{ref}/field-stats`) and never
// here: the classifier reads the whole column, and a second implementation in the browser would be
// two versions of one decision — exactly the divergence `lib/symbology.js` exists to avoid. This
// component chooses, requests and edits; it does not classify.
const COLOR_MODES = [
  { value: 'single', label: 'Single' },
  { value: 'graduated', label: 'Graduated' },
  { value: 'categorized', label: 'Categories' },
]
const SEQUENTIAL = Object.keys(RAMPS).filter(r => !DIVERGING.includes(r))

const statsBusy = ref(false)
const statsError = ref('')
const truncatedCats = ref(false)

const colorMode = computed(() => props.config.style?.color_mode || 'single')
const classCount = computed(() => (props.config.style?.classes || []).length || 5)
const classMethod = computed(() => props.config.style?.class_method || 'quantile')
const ramp = computed(() => props.config.style?.color_ramp || 'viridis')
const rampReverse = computed(() => !!props.config.style?.color_ramp_reverse)
// The swatch strip under the picker: what the classes WILL look like, in the current direction.
const rampPreview = computed(() => rampColors(ramp.value, 7, rampReverse.value))

// Fields worth offering. The geometry column is never a symbology field, and a column of unique
// ids classifies into as many classes as there are rows — offering them invites a useless map.
const styleFields = computed(() => (layer.value?.columns || []).filter(
  c => c.name && !/^(geom|geometry|wkb_geometry|the_geom|bbox)$/i.test(c.name)))
const numericFields = computed(() => styleFields.value.filter(
  c => /int|numeric|decimal|double|real|float|serial/i.test(c.type || '')))
// Graduated needs a quantity; categories need something with repeated values. Text columns are
// offered for both because a numeric-looking code (a zone, a year) is legitimately categorical.
const colorFields = computed(() =>
  colorMode.value === 'graduated' ? numericFields.value : styleFields.value)

const legend = computed(() => {
  const entries = legendEntries(props.config.style || {})
  const cats = props.config.style?.categories || []
  return entries.map((e, i) => ({
    ...e,
    // The last entry of a categorized legend is the `match` fallback, which has no value to edit.
    isOther: colorMode.value === 'categorized' && i >= cats.length,
  }))
})

function setColorMode(mode) {
  if (mode === colorMode.value) return
  if (mode === 'single') {
    // Keep the field: switching back and forth while comparing is normal, and re-picking it every
    // time would be its own small punishment.
    emitStyle({ color_mode: 'single' })
    return
  }
  emitStyle({ color_mode: mode, classes: [], categories: [] })
  if (props.config.style?.color_field) refreshClasses({ color_mode: mode })
}

function pickColorField(field) {
  emitStyle({ color_field: field, classes: [], categories: [] })
  if (field) refreshClasses({ color_field: field })
}
// 12, not 9: the server clamps `classes` to 12 in routers/data/vector.py, and a control that stops
// at 9 silently refuses counts the classifier would have produced (issue #10).
function setClassCount(n) { refreshClasses({ classes_n: Math.max(2, Math.min(12, parseInt(n) || 5)) }) }
function setMethod(m) { refreshClasses({ class_method: m }) }
function setRamp(r) { refreshClasses({ color_ramp: r }) }
// Reversing has to REGENERATE the class colours, not just flip the legend swatches: the colours are
// stored per class and are individually editable, so the stored list is the truth (issue #11).
function toggleRampReverse() { refreshClasses({ color_ramp_reverse: !rampReverse.value }) }

/**
 * Ask the server to classify the chosen field and apply the result.
 *
 * `over` carries the control the user JUST changed, because the style prop has not been updated yet
 * when this runs — reading it back would classify with the previous value and look like a one-step
 * lag, which is the classic version of this bug.
 */
async function refreshClasses(over = {}) {
  const style = { ...props.config.style, ...over }
  const mode = over.color_mode || colorMode.value
  const field = over.color_field ?? style.color_field
  if (!field || mode === 'single') return
  statsBusy.value = true
  statsError.value = ''
  try {
    // `??`, not `||`: turning the reverse OFF passes false, which `||` would discard and the
    // toggle would only ever work in one direction.
    const reverse = over.color_ramp_reverse ?? rampReverse.value
    const { data } = await getFieldStats(props.config.layer_id, {
      field,
      classes: over.classes_n || classCount.value,
      method: over.class_method || classMethod.value,
      ramp: over.color_ramp || ramp.value,
      reverse,
    })
    truncatedCats.value = !!data.truncated
    const patch = {
      color_mode: mode,
      color_field: field,
      color_ramp: over.color_ramp || ramp.value,
      color_ramp_reverse: reverse,
      class_method: over.class_method || classMethod.value,
    }
    // A text column cannot be graduated and a numeric one is usually not meant to be categorical.
    // Follow the DATA rather than refusing: the mode switches, and the legend shows what happened.
    if (data.kind === 'numeric' && mode === 'graduated') {
      patch.classes = data.suggestion?.classes || []
      patch.categories = []
      if (!patch.classes.length) statsError.value = 'That field has no usable range to classify.'
    } else if (data.kind === 'categorical' || mode === 'categorized') {
      patch.color_mode = 'categorized'
      patch.categories = data.suggestion?.categories || []
      patch.classes = []
      if (!patch.categories.length) statsError.value = 'That field has no values to group by.'
    }
    emitStyle(patch)
  } catch (e) {
    statsError.value = e?.response?.data?.detail || 'Could not read that field.'
  } finally {
    statsBusy.value = false
  }
}

function setEntryColor(i, color) {
  if (colorMode.value === 'graduated') {
    const classes = (props.config.style?.classes || []).map((c, j) => j === i ? { ...c, color } : c)
    emitStyle({ classes })
  } else {
    const cats = (props.config.style?.categories || []).map((c, j) => j === i ? { ...c, color } : c)
    emitStyle({ categories: cats })
  }
}

// Which layers can be given 3D, and why the others cannot:
//   * LINES — there is no sensible column for a line; QGIS and GeoLibre do not offer one either.
//   * GEOPARQUET POINTS — they render through deck.gl, not Martin, so the buffered-polygon tile
//     source that gives PostGIS points their bars does not apply. deck extrudes POLYGONS, not
//     points, and the vendored bundle has no ColumnLayer — so a pillar there needs the geometry
//     buffered client-side first, which is not built. Hidden rather than shown doing nothing, the
//     same rule as hiding it when a layer has no numeric field.
// GeoParquet POLYGONS are fine and offered: deck's GeoJsonLayer extrudes them directly
// (`extruded` + `getElevation`), and a PMTiles-tiled one takes the normal MapLibre path.
const canExtrude = computed(() => {
  if (!numericFields.value.length) return false
  if (geomType.value === 'line') return false
  // An UNKNOWN geometry gets no 3D. "Unknown" is a real stored value — Fiona reports it for any
  // shapefile with a generic or mixed header — and offering "extrude by a field" for it produced a
  // control whose behaviour nobody could predict: the server's fallback treated the layer as points
  // and buffered polygons into a mess. Ingest now resolves the type from the data, so this is the
  // backstop for layers imported before that, not the normal path.
  if (geomType.value === 'unknown') return false
  if (geomType.value === 'point' && layer.value?.storage_backend === 'geoparquet') return false
  return true
})

// Outline, shared by polygons and points. NO_OUTLINE is a sentinel string rather than '' or null,
// because this dict is JSON that round-trips through a saved portal and three renderers — and '' is
// what an uninitialised colour input yields, which would silently remove outlines from layers whose
// author never touched the control. Absent still means "the default", so old portals are unchanged.
const noOutline = computed(() => props.config.style?.outline_color === NO_OUTLINE)
const outlineSwatch = (fallback) => {
  const c = props.config.style?.outline_color
  return (!c || c === NO_OUTLINE) ? fallback : c
}
function setNoOutline(on) {
  // Turning it back ON restores the geometry's own default rather than whatever was last picked:
  // the previous colour is gone from the style, and guessing one would be worse than a known start.
  emitStyle({ outline_color: on ? NO_OUTLINE : (geomType.value === 'point' ? '#ffffff' : '#1d4ed8') })
}

function setExtrusion(patch) {
  emitStyle({ extrusion: { ...(props.config.style?.extrusion || {}), ...patch } })
}

// The bar footprint the SERVER will use when the author has not chosen one — derived from the
// layer's own extent (parity: `symbology.pillar_radius`). Shown in the input so the number on
// screen is the number being rendered; a hard-coded 30 there was a lie for any layer wider than a
// town, and 30 m on a world map is about three thousandths of a pixel.
const defaultRadius = computed(() =>
  Math.round(pillarRadius(props.config.style || {}, layer.value?.bbox)))


// ── Multiband band selection (bidx) ──────────────────────────────────────────
// bidx in the style: [n] = single band, [r,g,b] = RGB composite, absent = TiTiler default.
const bandCount = computed(() => layer.value?.band_count || 1)
const bandList = computed(() => Array.from({ length: bandCount.value }, (_, i) => i + 1))
const bidx = computed(() => props.config.style?.bidx || null)
// Default a multiband raster to RGB; one selected band means single-band mode.
const bandMode = computed(() => (bidx.value && bidx.value.length === 1) ? 'single' : 'rgb')
const rgbBands = computed(() =>
  (bidx.value && bidx.value.length === 3)
    ? bidx.value
    : [1, Math.min(2, bandCount.value), Math.min(3, bandCount.value)])
const singleBand = computed(() => (bidx.value && bidx.value.length === 1) ? bidx.value[0] : 1)

function setBandMode(mode) {
  if (mode === 'rgb') emitStyle({ bidx: rgbBands.value.slice(), colormap: null, algorithm: null })
  else emitStyle({ bidx: [singleBand.value] })
}
function setRgbBand(i, val) {
  const b = rgbBands.value.slice()
  b[i] = parseInt(val)
  emitStyle({ bidx: b, colormap: null, algorithm: null })
}
function setSingleBand(val) {
  emitStyle({ bidx: [parseInt(val)] })
}

// Hillshade returns its own 0–255 image, so the stretch controls below do nothing while it is on.
const isHillshade = computed(() => props.config.style?.algorithm === 'hillshade')
const rescaleMin = computed(() => (props.config.style?.rescale || '').split(',')[0] || '')
const rescaleMax = computed(() => (props.config.style?.rescale || '').split(',')[1] || '')
const autoStretching = ref(false)
async function autoStretch() {
  if (!layer.value) return
  autoStretching.value = true
  try {
    const { data } = await getRasterStats(layer.value.id)
    if (data?.rescale) emitStyle({ rescale: data.rescale })
  } catch { /* leave manual values */ } finally {
    autoStretching.value = false
  }
}
function setRescale(which, val) {
  const parts = (props.config.style?.rescale || ',').split(',')
  let mn = which === 'min' ? val : parts[0]
  let mx = which === 'max' ? val : parts[1]
  const rescale = (mn !== '' && mn != null && mx !== '' && mx != null) ? `${mn},${mx}` : null
  emitStyle({ rescale })
}

function isFieldSelected(name) {
  const fields = props.config.popup_fields
  return !fields?.length || fields.includes(name)
}
function toggleField(name, checked) {
  const cols = layer.value?.columns || []
  let current = props.config.popup_fields?.length ? [...props.config.popup_fields] : cols.map(c => c.name)
  if (checked) { if (!current.includes(name)) current.push(name) }
  else { current = current.filter(n => n !== name) }
  const allSelected = cols.every(c => current.includes(c.name))
  emit('update', { popup_fields: allSelected ? [] : current })
}
function shortType(type) {
  const t = (type || '').toLowerCase()
  if (t.includes('int') || t.includes('num')) return 'num'
  if (t.includes('float') || t.includes('real') || t.includes('double')) return 'dec'
  if (t.includes('bool')) return 'bool'
  if (t.includes('date') || t.includes('time')) return 'date'
  return 'str'
}

async function saveDefault() {
  if (!layer.value) return
  savingDefault.value = true
  try {
    const body = props.config.layer_type === 'vector'
      ? { opacity: props.config.opacity, style: props.config.style, popup_fields: props.config.popup_fields }
      : {
          opacity: props.config.opacity,
          colormap: props.config.style?.colormap || null,
          rescale: props.config.style?.rescale || null,
          algorithm: props.config.style?.algorithm || null,
          zfactor: props.config.style?.zfactor ?? null,
          bidx: props.config.style?.bidx || null,
        }
    const fn = props.config.layer_type === 'vector' ? saveVectorDefaultStyle : saveRasterDefaultStyle
    const { data: updated } = await fn(layer.value.id, body)
    const list = props.config.layer_type === 'vector' ? dataStore.vectorLayers : dataStore.rasterLayers
    const idx = list.findIndex(l => l.id === layer.value.id)
    if (idx !== -1) list[idx] = updated
  } finally {
    savingDefault.value = false
  }
}

function useDefault() {
  if (!layer.value?.default_style) return
  const ds = layer.value.default_style
  emit('update', {
    opacity: ds.opacity ?? 1.0,
    style: props.config.layer_type === 'vector'
      ? (ds.style ?? {})
      : { colormap: ds.colormap || null, rescale: ds.rescale || null, algorithm: ds.algorithm || null, zfactor: ds.zfactor ?? null, bidx: ds.bidx || null },
    ...(props.config.layer_type === 'vector' ? { popup_fields: ds.popup_fields ?? [] } : {}),
  })
}
</script>
