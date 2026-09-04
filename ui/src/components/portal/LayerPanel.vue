<template>
  <!-- Two hosts, one control (issue #23). In a PORTAL this is a row in the layer list whose swatch
       opens a teleported popover. In MY DATA there is no list and no map — the same symbology body
       is rendered in place inside a modal, editing the layer's DEFAULT style. `standalone` is the
       only difference, because a second styling UI is a second place for the vocabulary to drift. -->
  <div :class="standalone ? '' : 'flex items-center gap-1.5 py-1 px-1 rounded hover:bg-muted/60'">
    <template v-if="!standalone">
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
    </template>

    <!-- Symbology popover (opens from the swatch) — rendered IN PLACE when standalone. -->
    <Teleport to="body" :disabled="standalone">
      <div v-if="showStyle || standalone" ref="popEl" :style="standalone ? null : popStyle"
        :class="standalone
          ? 'text-foreground/85'
          : 'fixed z-[60] flex flex-col bg-card border border-border rounded-lg shadow-xl text-foreground/85'">
        <div v-if="!standalone" class="flex items-center justify-between gap-2 px-3 py-2 border-b border-border/60 text-xs font-semibold flex-shrink-0">
          <span class="truncate">{{ layerName }}</span>
          <button @click="showStyle = false" class="text-muted-foreground/70 hover:text-foreground text-lg leading-none flex-shrink-0">&times;</button>
        </div>
        <!-- `min-h-0` is what makes the flex child actually scroll: without it a flex item refuses
             to shrink below its content and the popover grows past the height positionPop gave it,
             which is how the bottom of this panel ended up off-screen. The max height now lives on
             the OUTER box (set from the space actually available), not on this one as a fixed 70vh. -->
        <div class="space-y-3" :class="standalone ? '' : 'px-3 py-2.5 overflow-auto flex-1 min-h-0'">

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
                    <input type="number" min="2" max="100" :value="classCount" @change="setClassCount($event.target.value)"
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
                  </div>
                  <div class="flex items-center justify-between gap-2 mt-1">
                    <div class="flex h-2 flex-1 rounded overflow-hidden" aria-hidden="true">
                      <span v-for="(c, i) in rampPreview" :key="i" class="flex-1"
                        :style="{ backgroundColor: c }"></span>
                    </div>
                    <!-- Which end means "high" is a cartographic choice, not a property of the
                         ramp: on a dark basemap the light end often belongs to the low values. -->
                    <label class="flex items-center gap-1 text-[11px] text-muted-foreground
                                  cursor-pointer shrink-0">
                      <input type="checkbox" :checked="rampReverse"
                        @change="toggleRampReverse" class="cursor-pointer" />
                      Reverse
                    </label>
                  </div>
                </div>

                <p v-if="statsBusy" class="text-[11px] text-muted-foreground/70">Reading the field…</p>
                <p v-else-if="statsError" class="text-[11px] text-red-400">{{ statsError }}</p>

                <!-- The legend, editable. Each swatch is the actual colour the map will use, so this
                     doubles as the preview of the classification. -->
                <!-- Name the COLUMN. Swatches alone show that something varies without saying
                     what, and the published legend now says it — these two must agree. -->
                <p v-if="legend.length && colorField" class="text-[11px] text-muted-foreground/70">
                  Colour by <span class="font-medium text-muted-foreground">{{ colorField }}</span>
                </p>
                <div v-if="legend.length" class="space-y-0.5 max-h-40 overflow-y-auto pr-1">
                  <div v-for="(e, i) in legend" :key="i" class="flex items-center gap-1.5">
                    <!-- A RULE or a HEATMAP entry has no colour this panel can set: a rule's colour
                         lives in the rule, and a heatmap's is a ramp. Both are read-only here (the
                         "Styled in QGIS" block and the ramp control own them), so they show the
                         symbol rather than a picker that would write nowhere. -->
                    <LegendSwatch v-if="e.rule || e.heatmap" :geom="geomType" :color="e.color"
                      :marker="e.shape" :dash="e.dash" :size="18"
                      :image="e.marker_image" :pattern="e.fill_pattern" :ramp="e.ramp" />
                    <!-- "Other" IS EDITABLE. It was disabled — while the hint below it said the
                         remaining values draw in the Other colour, which left no way to choose
                         that colour. It has no `value` to edit, but it does have a colour: it is
                         the `match` fallback, `style.other_color`, which both renderers and the
                         QGIS plugin have always read. -->
                    <input v-else type="color" :value="e.color"
                      @input="setEntryColor(i, $event.target.value)"
                      :title="e.isOther ? 'Everything not listed above' : e.label"
                      class="w-5 h-5 rounded border border-border/50 cursor-pointer p-0 flex-shrink-0" />
                    <span class="text-[11px] text-muted-foreground truncate">{{ e.label }}</span>
                  </div>
                </div>
                <p v-if="truncatedCats" class="text-[11px] text-amber-300/80">
                  Showing the {{ (config.style?.categories || []).length }} commonest values; the rest
                  draw in the “Other” colour.
                </p>
                <!-- Says what happened instead of silently rewriting the box. Repeated values
                     collapse a break, so a column can legitimately yield fewer classes than asked
                     — one on a live instance yields exactly one, whatever you request. -->
                <p v-if="fewerThanAsked" class="text-[11px] text-amber-300/80">
                  {{ producedClasses }} of {{ classCount }} classes — the rest would be empty,
                  because this column's values repeat.
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
                <!-- A polygon's outline WIDTH, in pixels — the same unit a line uses, because it is
                     one. Anything above the default 1 is drawn as its own line layer beside the
                     fill: a MapLibre fill strokes its own edge at a fixed hairline, so a width
                     could not be honoured by the fill at all. -->
                <div v-if="!noOutline" class="flex items-center gap-2 mt-1.5">
                  <span class="text-[11px] text-muted-foreground flex-shrink-0">Width</span>
                  <input type="number" min="0" max="20" step="0.5"
                    :value="config.style?.outline_width ?? 1"
                    @input="emitStyle({ outline_width: parseFloat($event.target.value) })"
                    class="w-16 text-xs border border-border rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-primary/60" />
                  <span class="text-xs text-muted-foreground/70">px</span>
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

            <!-- Size from a field (issue #21). The instance has drawn this since v1.1 — an
                 `interpolate` over `size_field` for circle-radius and line-width, and `icon-size`
                 for marker shapes — but nothing in the dashboard could set it, so it was reachable
                 only from the CLI. Points and lines only: a polygon has no width to vary. -->
            <div v-if="canSizeByField" class="pt-1 border-t border-border/50 space-y-1.5">
              <label class="text-xs text-muted-foreground">
                {{ geomType === 'point' ? 'Size by a field' : 'Width by a field' }}
              </label>
              <select :value="sizeField || ''" @change="pickSizeField($event.target.value)"
                class="w-full text-xs border border-border rounded px-1.5 py-1">
                <option value="">Fixed {{ geomType === 'point' ? 'size' : 'width' }}</option>
                <option v-for="f in numericFields" :key="f.name" :value="f.name">{{ f.name }}</option>
              </select>
              <div v-if="sizeField" class="flex items-center gap-1.5">
                <label class="flex-1">
                  <span class="text-[11px] text-muted-foreground">Smallest</span>
                  <input type="number" min="0.5" max="60" step="0.5" :value="sizePx[0]"
                    @change="setSizePx(0, $event.target.value)"
                    class="w-full text-xs border border-border rounded px-1.5 py-1 mt-0.5" />
                </label>
                <label class="flex-1">
                  <span class="text-[11px] text-muted-foreground">Largest</span>
                  <input type="number" min="0.5" max="60" step="0.5" :value="sizePx[1]"
                    @change="setSizePx(1, $event.target.value)"
                    class="w-full text-xs border border-border rounded px-1.5 py-1 mt-0.5" />
                </label>
              </div>
              <p v-if="sizeField && sizeRange" class="text-[11px] text-muted-foreground/70">
                {{ sizeRange[0] }} → {{ sizeRange[1] }} maps to {{ sizePx[0] }} → {{ sizePx[1] }} px.
              </p>
              <p v-else-if="sizeField && sizeBusy" class="text-[11px] text-muted-foreground/70">
                Reading the field…
              </p>
              <!-- The same two-ended scale the published legend draws, so what is configured here
                   and what a reader sees there are visibly the same thing. Two ends only: the size
                   expression interpolates linearly, and drawing intermediate steps would imply
                   classes the map does not have. -->
              <div v-if="sizeField && sizeRange" class="flex items-end gap-4 pt-1">
                <div v-for="(end, i) in [0, 1]" :key="i"
                  class="flex flex-col items-center gap-1 min-w-[34px]">
                  <span class="flex items-end justify-center" style="min-height:30px">
                    <span v-if="geomType === 'line'" :style="{
                      display: 'block', width: '26px',
                      height: Math.max(2, Math.min(28, sizePx[end])) + 'px',
                      borderRadius: (Math.max(2, Math.min(28, sizePx[end])) / 2) + 'px',
                      background: baseColor }" />
                    <span v-else :style="{
                      display: 'block',
                      width: (Math.max(2, Math.min(28, sizePx[end])) * 2) + 'px',
                      height: (Math.max(2, Math.min(28, sizePx[end])) * 2) + 'px',
                      borderRadius: '50%', background: baseColor }" />
                  </span>
                  <span class="text-[10.5px] text-muted-foreground tabular-nums">
                    {{ sizeRange[end] }}
                  </span>
                </div>
              </div>
            </div>

            <!-- 3D / 2.5D. Polygons rise directly (MapLibre raises a fill, deck extrudes the mesh);
                 POINTS become bars — a column standing at each location, served as a buffered
                 polygon by the shared Martin function (services/pillars), so the layer keeps the
                 renderer it already had. Lines are excluded: there is no sensible column for a line,
                 and QGIS/GeoLibre do not offer one either.

                 TWO height sources, because QGIS has two renderers here and they are not variations
                 of one control: a 2.5D renderer gives every feature the SAME height (its height is a
                 project variable, not a field), while attribute-driven 3D reads a column. Offering
                 only the second is what made a 2.5D style from QGIS uneditable — and hid this whole
                 section for any layer with no numeric column, which is most building footprints. -->
            <div v-if="canExtrude" class="pt-1 border-t border-border/50">
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" :checked="!!config.style?.extrusion?.enabled"
                  @change="setExtrusionOn($event.target.checked)" class="accent-primary" />
                <span class="text-xs text-foreground">
                  {{ geomType === 'point' ? '3D — bars at each point' : '3D — raise these polygons' }}
                </span>
              </label>
              <div v-if="config.style?.extrusion?.enabled" class="mt-1.5 space-y-1.5 pl-5">
                <div class="flex items-center gap-2">
                  <span class="text-[11px] text-muted-foreground flex-shrink-0 w-12">Height</span>
                  <select :value="extrusionMode" @change="setExtrusionMode($event.target.value)"
                    class="flex-1 text-xs border border-border rounded px-1.5 py-1">
                    <option value="fixed">The same for all</option>
                    <option value="field" :disabled="!numericFields.length">From a field</option>
                  </select>
                </div>
                <!-- One flat height: the 2.5D case. In metres, like every other height here, so a
                     number that came from QGIS's map units may need adjusting once. -->
                <label v-if="extrusionMode === 'fixed'" class="flex items-center gap-2">
                  <span class="text-[11px] text-muted-foreground flex-shrink-0 w-12"></span>
                  <input type="number" min="0" step="1"
                    :value="config.style?.extrusion?.height ?? FLAT_HEIGHT_M"
                    @change="setExtrusion({ height: Math.max(0, parseFloat($event.target.value) || 0) })"
                    class="w-20 text-xs border border-border rounded px-1.5 py-0.5" />
                  <span class="text-[11px] text-muted-foreground/70">m tall</span>
                </label>
                <template v-else>
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
                </template>
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
                <button type="button" @click="toggle('more3d')"
                  class="text-[11px] text-primary hover:text-primary/80">
                  {{ open.more3d ? '−' : '+' }} More 3D options
                </button>
                <div v-if="open.more3d" class="space-y-1.5">
                  <!-- The ROOF colour in QGIS's vocabulary: MapLibre paints the whole volume one
                       colour with a vertical gradient down the walls, so this is the only colour a
                       3D layer has. Blank means "whatever the flat symbology uses", which keeps a
                       layer's 2D and 3D forms in agreement unless somebody asks otherwise. -->
                  <div class="flex items-center gap-2">
                    <span class="text-[11px] text-muted-foreground flex-shrink-0 w-12">Colour</span>
                    <input type="color" :value="config.style?.extrusion?.color || config.style?.color || '#3b82f6'"
                      @input="setExtrusion({ color: $event.target.value })"
                      class="h-6 w-10 border border-border rounded cursor-pointer bg-transparent" />
                    <button v-if="config.style?.extrusion?.color" type="button"
                      @click="setExtrusion({ color: undefined })"
                      class="text-[11px] text-primary hover:text-primary/80">match the fill</button>
                  </div>
                  <!-- Where the volume STARTS. A floor number times the multiplier gives a storey
                       that floats above the ground; a plain number lifts everything equally. -->
                  <div class="flex items-center gap-2">
                    <span class="text-[11px] text-muted-foreground flex-shrink-0 w-12">Base</span>
                    <select :value="baseMode" @change="setBaseMode($event.target.value)"
                      class="flex-1 text-xs border border-border rounded px-1.5 py-1">
                      <option value="ground">On the ground</option>
                      <option value="fixed">A fixed height up</option>
                      <option value="field" :disabled="!numericFields.length">From a field</option>
                    </select>
                  </div>
                  <label v-if="baseMode === 'fixed'" class="flex items-center gap-2">
                    <span class="text-[11px] text-muted-foreground flex-shrink-0 w-12"></span>
                    <input type="number" min="0" step="1" :value="config.style?.extrusion?.base || 0"
                      @change="setExtrusion({ base: Math.max(0, parseFloat($event.target.value) || 0) })"
                      class="w-20 text-xs border border-border rounded px-1.5 py-0.5" />
                    <span class="text-[11px] text-muted-foreground/70">m up</span>
                  </label>
                  <select v-if="baseMode === 'field'" :value="config.style?.extrusion?.base || ''"
                    @change="setExtrusion({ base: $event.target.value })"
                    class="w-full text-xs border border-border rounded px-1.5 py-1">
                    <option value="">Choose a base field…</option>
                    <option v-for="f in numericFields" :key="f.name" :value="f.name">{{ f.name }}</option>
                  </select>
                  <label class="flex items-center gap-2">
                    <span class="text-[11px] text-muted-foreground flex-shrink-0 w-12">Solid</span>
                    <input type="range" min="0.1" max="1" step="0.05"
                      :value="config.style?.extrusion?.opacity ?? 1"
                      @input="setExtrusion({ opacity: parseFloat($event.target.value) })"
                      class="flex-1 accent-primary" />
                    <span class="text-[11px] text-muted-foreground/70 w-8 text-right tabular-nums">
                      {{ Math.round((config.style?.extrusion?.opacity ?? 1) * 100) }}%
                    </span>
                  </label>
                </div>
                <p class="text-[11px] text-muted-foreground/70 leading-snug">
                  Heights are in metres. Use the multiplier when the field is not — floors × 3, say.
                  The map tilts so you can see it.
                </p>
                <!-- What a 2.5D style loses on the way here, said once, where the height is. QGIS
                     rakes its walls off at an angle and drops a shadow; MapLibre raises a true
                     volume and has neither. The angle and shadow are still CARRIED (in
                     `extrusion.qgis25d`) so they survive the trip back — this says so rather than
                     leaving the difference to be discovered on screen. -->
                <p v-if="from25D" class="text-[11px] text-muted-foreground/70 leading-snug">
                  From a QGIS 2.5D layer. Its viewing angle and shadow are kept for the trip back to
                  QGIS, but the web map raises a real volume and draws neither.
                </p>
              </div>
            </div>

            <!-- HEATMAP. A renderer, not a paint option: it REPLACES the points with a density
                 surface, so it sits with the other "what is this layer" choices rather than among
                 the colours it makes irrelevant. Points only — a density map of polygons is not a
                 thing QGIS offers either. -->
            <div v-if="geomType === 'point' && config.layer_type === 'vector'"
                 class="pt-1 border-t border-border/50">
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" :checked="heatmapOn"
                  @change="setHeatmapOn($event.target.checked)" class="accent-primary" />
                <span class="text-xs text-foreground">Draw as a heatmap</span>
              </label>
              <div v-if="heatmapOn" class="mt-1.5 space-y-1.5 pl-5">
                <div class="flex items-center gap-2">
                  <span class="text-[11px] text-muted-foreground flex-shrink-0 w-12">Radius</span>
                  <input type="range" min="4" max="80" step="1"
                    :value="config.style?.heatmap?.radius ?? 20"
                    @input="setHeatmap({ radius: parseFloat($event.target.value) })"
                    class="flex-1 h-1 accent-primary" />
                  <span class="text-[11px] text-muted-foreground/70 w-8 text-right">
                    {{ config.style?.heatmap?.radius ?? 20 }}px
                  </span>
                </div>
                <!-- THE SAME CONTROL AS THE GRADUATED RAMP ABOVE: a name, and one flat bar under
                     it. A previous attempt drew each ramp as its own CSS gradient over a
                     chequerboard and the `background-size` list applied to the gradient layer as
                     well, repeating it every 8px — four boxes of vertical stripes. Spans with a
                     flat `backgroundColor`, exactly as `rampPreview` does it, have no such trap.
                     The first stop is transparent, which is what stops the whole viewport being
                     painted at density zero; it gets a chequer so it reads as a fade to nothing
                     rather than as a ramp that just starts pale. -->
                <div class="block">
                  <span class="text-[11px] text-muted-foreground">Colour ramp</span>
                  <select :value="heatmapRampName" @change="setHeatmapRamp($event.target.value)"
                    class="mt-0.5 w-full text-xs border border-border rounded px-1.5 py-1">
                    <option v-for="r in heatmapRamps" :key="r.name" :value="r.name">{{ r.label }}</option>
                  </select>
                  <div class="flex items-center justify-between gap-2 mt-1">
                    <div class="flex h-2 flex-1 rounded overflow-hidden" aria-hidden="true"
                         :style="CHECKER">
                      <span v-for="(c, i) in heatmapPreview" :key="i" class="flex-1"
                        :style="{ backgroundColor: c }"></span>
                    </div>
                    <!-- Reversing flips the COLOURS and rebuilds the transparent stop at whichever
                         is now lowest — see `heatmapColors`. Density always fades out at the bottom
                         end; what changes is which hue it fades from. -->
                    <label class="flex items-center gap-1 text-[11px] text-muted-foreground
                                  cursor-pointer shrink-0">
                      <input type="checkbox" :checked="heatmapReverse"
                        @change="toggleHeatmapReverse" class="cursor-pointer" />
                      Reverse
                    </label>
                  </div>
                </div>
                <select :value="config.style?.heatmap?.weight_field || ''"
                  @change="setHeatmapWeight($event.target.value)"
                  class="w-full text-xs border border-border rounded px-1.5 py-1">
                  <option value="">Every point counts the same</option>
                  <option v-for="f in numericFields" :key="f.name" :value="f.name">
                    Weight by {{ f.name }}
                  </option>
                </select>
                <p class="text-[11px] text-muted-foreground/70 leading-snug">
                  The points themselves stop drawing — a heatmap answers "where are these
                  concentrated", not "where is each one".
                </p>
              </div>
            </div>

            <!-- HATCHES. The tile is generated here, in the browser, exactly as the QGIS plugin
                 generates one from a QGIS symbol — same `fill_pattern` key, same data URI, so a
                 hatch made here and one pushed from QGIS are the same thing to every renderer.
                 Presets rather than angle-and-spacing boxes: those are what people pick, and the
                 four offered are the four angles at which a square tile actually closes. -->
            <div v-if="geomType === 'polygon' && config.layer_type === 'vector'"
                 class="pt-1 border-t border-border/50">
              <label class="text-xs text-muted-foreground">Fill pattern</label>
              <div class="flex flex-wrap items-center gap-1 mt-1">
                <button v-for="h in hatchPresets" :key="h.name" type="button" :title="h.title"
                  @click="setHatch(h.name)"
                  class="w-7 h-7 rounded border overflow-hidden flex items-center justify-center"
                  :class="activeHatch === h.name
                    ? 'border-primary ring-1 ring-primary/40' : 'border-border hover:border-primary/50'">
                  <span v-if="h.name === 'none'" class="text-[9px] text-muted-foreground">none</span>
                  <img v-else :src="hatchPreview(h.name)" alt="" class="w-full h-full" />
                </button>
              </div>
              <p v-if="activeHatch !== 'none'" class="text-[11px] text-muted-foreground/70 mt-1">
                The pattern replaces the fill colour; opacity still applies.
              </p>
            </div>

            <!-- DIRECTION ARROWS. The same trick as the hatches above, for lines: the head is drawn
                 here, in the browser, into the same `line_marker` key and the same PNG data URI the
                 QGIS plugin's `arrows.py` produces — so an arrow drawn here and a QGIS arrow line
                 are the same thing to every renderer. Direction is the whole point of a flow, a
                 one-way street or a river, and there was no way to show it without QGIS. -->
            <div v-if="geomType === 'line' && config.layer_type === 'vector'"
                 class="pt-1 border-t border-border/50">
              <label class="text-xs text-muted-foreground">Direction arrows</label>
              <div class="flex flex-wrap items-center gap-1 mt-1">
                <button v-for="a in arrowPresets" :key="a.name" type="button" :title="a.title"
                  @click="setArrow(a.name)"
                  class="h-7 min-w-[28px] px-1 rounded border overflow-hidden flex items-center justify-center"
                  :class="activeArrow === a.name
                    ? 'border-primary ring-1 ring-primary/40' : 'border-border hover:border-primary/50'">
                  <span v-if="a.name === 'none'" class="text-[9px] text-muted-foreground">none</span>
                  <img v-else :src="arrowPreview(a.name)" alt="" class="h-2.5" />
                </button>
              </div>
              <label v-if="activeArrow !== 'none'" class="flex items-center gap-2 mt-1.5">
                <span class="text-[11px] text-muted-foreground flex-shrink-0">Every</span>
                <input type="number" min="10" step="10"
                  :value="config.style?.line_marker?.spacing ?? 90"
                  @change="setArrowSpacing($event.target.value)"
                  class="w-20 text-xs border border-border rounded px-1.5 py-0.5" />
                <span class="text-[11px] text-muted-foreground/70">px</span>
              </label>
            </div>

            <!-- LABELS. New to the platform in 2026-09; there was no way to label a layer at all
                 before, here or anywhere. Collapsed behind its own switch, following the 3D block
                 above: an off switch and one line of text is the whole cost when you do not want
                 labels, and QGIS's own labelling tab is a panel you open rather than a wall you
                 scroll past. -->
            <div v-if="canLabel" class="pt-1 border-t border-border/50">
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" :checked="labelsOn"
                  @change="setLabelsOn($event.target.checked)" class="accent-primary" />
                <span class="text-xs text-foreground">Label features</span>
              </label>
              <div v-if="labelsOn" class="mt-1.5 space-y-1.5 pl-5">
                <select :value="config.style?.labels?.field || ''"
                  @change="setLabels({ field: $event.target.value })"
                  class="w-full text-xs border border-border rounded px-1.5 py-1">
                  <option value="">Choose a field…</option>
                  <option v-for="f in styleFields" :key="f.name" :value="f.name">{{ f.name }}</option>
                </select>
                <div class="flex items-center gap-2">
                  <input type="color" :value="config.style?.labels?.color || '#333333'"
                    @input="setLabels({ color: $event.target.value })"
                    class="w-6 h-6 rounded border border-border cursor-pointer p-0" />
                  <span class="text-[11px] text-muted-foreground">Size</span>
                  <input type="number" min="6" max="48" step="1" :value="config.style?.labels?.size ?? 12"
                    @change="setLabels({ size: parseFloat($event.target.value) || 12 })"
                    class="w-14 text-xs border border-border rounded px-1.5 py-0.5" />
                  <span class="text-[11px] text-muted-foreground/70">px</span>
                </div>
                <!-- The halo is what makes a label readable over a busy basemap, so it is on by
                     default and offered here rather than hidden deeper. QGIS calls it a buffer. -->
                <div class="flex items-center gap-2">
                  <input type="color" :value="config.style?.labels?.halo_color || '#ffffff'"
                    @input="setLabels({ halo_color: $event.target.value })"
                    class="w-6 h-6 rounded border border-border cursor-pointer p-0" />
                  <span class="text-[11px] text-muted-foreground">Halo</span>
                  <input type="number" min="0" max="6" step="0.5" :value="config.style?.labels?.halo_width ?? 1"
                    @change="setLabels({ halo_width: parseFloat($event.target.value) || 0 })"
                    class="w-14 text-xs border border-border rounded px-1.5 py-0.5" />
                  <span class="text-[11px] text-muted-foreground/70">px</span>
                </div>
                <label v-if="geomType === 'line'" class="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" :checked="config.style?.labels?.placement === 'line'"
                    @change="setLabels({ placement: $event.target.checked ? 'line' : 'point' })"
                    class="accent-primary" />
                  <span class="text-[11px] text-muted-foreground">Bend the text along the line</span>
                </label>

                <!-- WHERE THE TEXT SITS. A nine-way anchor rather than two number boxes, because
                     "above the point" is what somebody means and "y offset -12" is how a renderer
                     spells it. The offset boxes are still there underneath for the cases the grid
                     cannot say. QGIS gives the same choice the same way. -->
                <div v-if="config.style?.labels?.placement !== 'line'">
                  <label class="text-[11px] text-muted-foreground">Position</label>
                  <div class="grid grid-cols-3 gap-0.5 mt-0.5 w-[4.5rem]">
                    <button v-for="a in labelAnchors" :key="a.value" type="button"
                      :title="a.title" @click="setLabels({ anchor: a.value })"
                      class="h-5 rounded border text-[9px] leading-none"
                      :class="(config.style?.labels?.anchor || 'center') === a.value
                        ? 'border-primary bg-primary/20 text-foreground'
                        : 'border-border text-muted-foreground/60 hover:border-primary/50'">
                      {{ a.mark }}
                    </button>
                  </div>
                </div>

                <button type="button" @click="toggle('labelMore')"
                  class="text-[11px] text-primary hover:text-primary/80">
                  {{ open.labelMore ? '− Fewer' : '+ More' }} label options
                </button>
                <div v-if="open.labelMore" class="space-y-1.5 pt-0.5">
                  <div class="flex items-center gap-2">
                    <span class="text-[11px] text-muted-foreground flex-shrink-0 w-14">Nudge</span>
                    <input type="number" step="1" :value="labelOffset[0]" title="Left / right, px"
                      @change="setLabelOffset(0, $event.target.value)"
                      class="w-14 text-xs border border-border rounded px-1.5 py-0.5" />
                    <input type="number" step="1" :value="labelOffset[1]" title="Up / down, px"
                      @change="setLabelOffset(1, $event.target.value)"
                      class="w-14 text-xs border border-border rounded px-1.5 py-0.5" />
                    <span class="text-[11px] text-muted-foreground/70">px</span>
                  </div>
                  <div class="flex items-center gap-2">
                    <span class="text-[11px] text-muted-foreground flex-shrink-0 w-14">Rotate</span>
                    <input type="number" step="5" min="0" max="359"
                      :value="config.style?.labels?.rotation ?? 0"
                      @change="setLabels({ rotation: parseFloat($event.target.value) || 0 })"
                      class="w-14 text-xs border border-border rounded px-1.5 py-0.5" />
                    <span class="text-[11px] text-muted-foreground/70">°</span>
                  </div>
                  <div class="flex items-center gap-2">
                    <span class="text-[11px] text-muted-foreground flex-shrink-0 w-14">Wrap at</span>
                    <input type="number" step="1" min="0" max="60"
                      :value="config.style?.labels?.max_width ?? ''" placeholder="no wrap"
                      @change="setLabels({ max_width: parseFloat($event.target.value) || undefined })"
                      class="w-16 text-xs border border-border rounded px-1.5 py-0.5" />
                    <span class="text-[11px] text-muted-foreground/70">characters</span>
                  </div>
                  <select :value="config.style?.labels?.transform || 'none'"
                    @change="setLabels({ transform: $event.target.value })"
                    class="w-full text-xs border border-border rounded px-1.5 py-1">
                    <option value="none">As written</option>
                    <option value="uppercase">UPPERCASE</option>
                    <option value="lowercase">lowercase</option>
                  </select>
                  <label class="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox" :checked="!!config.style?.labels?.allow_overlap"
                      @change="setLabels({ allow_overlap: $event.target.checked })"
                      class="accent-primary" />
                    <span class="text-[11px] text-muted-foreground">
                      Draw every label, even where they collide
                    </span>
                  </label>
                  <!-- Priority decides which label wins the space when two want it. QGIS runs it
                       0-10 with higher meaning more important, and so does this; the map inverts
                       it, because MapLibre places the LOWEST sort key first. -->
                  <div class="flex items-center gap-2">
                    <span class="text-[11px] text-muted-foreground flex-shrink-0 w-14">Priority</span>
                    <input type="range" min="0" max="10" step="1"
                      :value="config.style?.labels?.priority ?? 5"
                      @input="setLabels({ priority: parseFloat($event.target.value) })"
                      class="flex-1 h-1 accent-primary" />
                    <span class="text-[11px] text-muted-foreground/70 w-4 text-right">
                      {{ config.style?.labels?.priority ?? 5 }}
                    </span>
                  </div>
                </div>

                <p class="text-[11px] text-muted-foreground/70 leading-snug">
                  Drawn above every layer on the map. A portal draws the fonts its glyph set
                  contains; anything else is matched to the nearest it has.
                </p>
              </div>
            </div>

            <!-- THE REST OF THE VOCABULARY, behind one disclosure. Every one of these round-trips
                 from QGIS already; putting them all on screen would cost every user the clarity of
                 this panel to serve the few who want a mitred join. Open it and they are there. -->
            <div v-if="config.layer_type === 'vector' && geomType !== 'unknown'"
                 class="pt-1 border-t border-border/50">
              <button type="button" @click="toggle('more')"
                class="text-[11px] text-primary hover:text-primary/80">
                {{ open.more ? '−' : '+' }} More {{ geomType }} options
              </button>
              <div v-if="open.more" class="mt-1.5 space-y-1.5">
                <template v-if="geomType === 'line' || geomType === 'polygon'">
                  <div class="flex items-center gap-2">
                    <span class="text-[11px] text-muted-foreground flex-shrink-0 w-12">Ends</span>
                    <select :value="config.style?.line_cap || 'butt'"
                      @change="emitStyle({ line_cap: $event.target.value })"
                      class="flex-1 text-xs border border-border rounded px-1.5 py-1">
                      <option value="butt">Flat</option>
                      <option value="round">Round</option>
                      <option value="square">Square</option>
                    </select>
                  </div>
                  <div class="flex items-center gap-2">
                    <span class="text-[11px] text-muted-foreground flex-shrink-0 w-12">Corners</span>
                    <select :value="config.style?.line_join || 'miter'"
                      @change="emitStyle({ line_join: $event.target.value })"
                      class="flex-1 text-xs border border-border rounded px-1.5 py-1">
                      <option value="miter">Sharp</option>
                      <option value="round">Round</option>
                      <option value="bevel">Cut off</option>
                    </select>
                  </div>
                </template>
                <div v-if="geomType === 'line'" class="flex items-center gap-2">
                  <span class="text-[11px] text-muted-foreground flex-shrink-0 w-12">Offset</span>
                  <input type="number" step="0.5" :value="config.style?.line_offset ?? 0"
                    @change="emitStyle({ line_offset: parseFloat($event.target.value) || undefined })"
                    class="w-16 text-xs border border-border rounded px-1.5 py-0.5" />
                  <span class="text-[11px] text-muted-foreground/70">px to one side</span>
                </div>
                <template v-if="geomType === 'point'">
                  <div class="flex items-center gap-2">
                    <span class="text-[11px] text-muted-foreground flex-shrink-0 w-12">Rotate</span>
                    <input type="number" step="5" min="0" max="359"
                      :value="config.style?.marker_rotation ?? 0"
                      @change="emitStyle({ marker_rotation: parseFloat($event.target.value) || undefined })"
                      class="w-16 text-xs border border-border rounded px-1.5 py-0.5" />
                    <span class="text-[11px] text-muted-foreground/70">°</span>
                  </div>
                  <div class="flex items-center gap-2">
                    <span class="text-[11px] text-muted-foreground flex-shrink-0 w-12">Nudge</span>
                    <input type="number" step="1" :value="markerOffset[0]" title="Left / right, px"
                      @change="setMarkerOffset(0, $event.target.value)"
                      class="w-14 text-xs border border-border rounded px-1.5 py-0.5" />
                    <input type="number" step="1" :value="markerOffset[1]" title="Up / down, px"
                      @change="setMarkerOffset(1, $event.target.value)"
                      class="w-14 text-xs border border-border rounded px-1.5 py-0.5" />
                    <span class="text-[11px] text-muted-foreground/70">px</span>
                  </div>
                  <div class="flex items-center gap-2">
                    <span class="text-[11px] text-muted-foreground flex-shrink-0 w-12">Fade</span>
                    <input type="range" min="0" max="1" step="0.05"
                      :value="config.style?.marker_opacity ?? 1"
                      @input="emitStyle({ marker_opacity: parseFloat($event.target.value) })"
                      class="flex-1 h-1 accent-primary" />
                    <span class="text-[11px] text-muted-foreground/70 w-8 text-right">
                      {{ Math.round((config.style?.marker_opacity ?? 1) * 100) }}%
                    </span>
                  </div>
                </template>
                <!-- A layer that draws nothing is not a broken one: it is how you keep a layer for
                     its labels, or its popups, without its geometry cluttering the map. QGIS calls
                     the renderer "No symbols". -->
                <label class="flex items-center gap-2 cursor-pointer pt-0.5">
                  <input type="checkbox" :checked="!!config.style?.no_symbol"
                    @change="emitStyle({ no_symbol: $event.target.checked || undefined })"
                    class="accent-primary" />
                  <span class="text-[11px] text-muted-foreground">
                    Draw no shapes — keep the labels and popups only
                  </span>
                </label>
              </div>
            </div>

            <!-- WHERE IT DRAWS. QGIS keeps this on the layer rather than in its symbology, and so
                 does GeoDeploy — it applies to everything the layer draws, its labels included. Two
                 numbers, so it stays one line rather than a section. -->
            <div v-if="config.layer_type === 'vector'" class="pt-1 border-t border-border/50">
              <label class="text-xs text-muted-foreground">Visible zoom range</label>
              <div class="flex items-center gap-2 mt-0.5">
                <input type="number" min="0" max="24" step="1" placeholder="0"
                  :value="config.style?.minzoom ?? ''"
                  @change="setZoom('minzoom', $event.target.value)"
                  class="w-16 text-xs border border-border rounded px-1.5 py-0.5" />
                <span class="text-[11px] text-muted-foreground/70">to</span>
                <input type="number" min="0" max="24" step="1" placeholder="24"
                  :value="config.style?.maxzoom ?? ''"
                  @change="setZoom('maxzoom', $event.target.value)"
                  class="w-16 text-xs border border-border rounded px-1.5 py-0.5" />
                <span class="text-[11px] text-muted-foreground/70">leave blank for no limit</span>
              </div>
            </div>

            <!-- SYMBOLOGY THAT CAME FROM QGIS. Rules, a pattern fill, a rendered marker and markers
                 along a line are all drawn here but not editable here — and each of them OUTRANKS
                 the controls above, so without this the colour picker would appear to do nothing
                 and there would be no way to find out why. Naming what is in charge, and offering
                 one button to hand control back, is the whole point of the block. -->
            <div v-if="qgisStyling.length" class="pt-1 border-t border-border/50">
              <p class="text-xs text-foreground">Styled in QGIS</p>
              <ul class="mt-1 space-y-0.5">
                <li v-for="item in qgisStyling" :key="item" class="text-[11px] text-muted-foreground">
                  • {{ item }}
                </li>
              </ul>
              <p class="text-[11px] text-muted-foreground/70 mt-1 leading-snug">
                This is drawn on the map but edited in QGIS. It takes precedence over the controls
                above.
              </p>
              <button @click="clearQgisStyling"
                class="mt-1.5 text-[11px] text-primary hover:text-primary/80 font-medium">
                Use simple styling instead
              </button>
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
              <!-- CONTINUOUS or CLASSIFIED — the same first question the vector side asks, and for
                   the same reason: a ramp claims that the distance between two values is meaningful,
                   which for land cover or soil codes it is not. -->
              <div v-if="!isHillshade && !isContours">
                <label class="text-xs text-muted-foreground">Values</label>
                <div class="flex gap-1 mt-1">
                  <button v-for="m in [{ value: 'ramp', label: 'Continuous' }, { value: 'classes', label: 'Unique values' }]"
                    :key="m.value" type="button" @click="setRasterColorMode(m.value)"
                    class="flex-1 text-[11px] py-1 rounded border transition-colors"
                    :class="rasterColorMode === m.value
                      ? 'border-primary/60 bg-primary/15 text-foreground'
                      : 'border-border text-muted-foreground hover:text-foreground'">{{ m.label }}</button>
                </div>
              </div>

              <div v-if="rasterColorMode === 'classes' && !isHillshade && !isContours" class="space-y-1.5">
                <div class="flex items-center justify-between">
                  <label class="text-xs text-muted-foreground">Classes</label>
                  <button @click="loadUniqueValues" :disabled="loadingValues"
                    class="text-xs text-primary hover:text-primary/80 font-medium disabled:opacity-50"
                    title="Read the distinct pixel values from the raster">
                    {{ loadingValues ? 'Reading…' : (rasterClasses.length ? '↻ Re-read' : '⚡ Read values') }}
                  </button>
                </div>
                <p v-if="valuesNote" class="text-[10px] text-muted-foreground/80">{{ valuesNote }}</p>
                <div v-if="rasterClasses.length" class="space-y-1 max-h-44 overflow-auto pr-0.5">
                  <div v-for="(c, i) in rasterClasses" :key="c.value" class="flex items-center gap-1.5">
                    <input type="color" :value="classHex(c.color)"
                      @input="setClassColor(i, $event.target.value)"
                      class="w-6 h-6 rounded border border-border cursor-pointer p-0 flex-shrink-0" />
                    <span class="text-xs text-muted-foreground/80 tabular-nums w-10 flex-shrink-0">{{ c.value }}</span>
                    <input type="text" :value="c.label ?? ''" placeholder="label"
                      @input="setClassLabel(i, $event.target.value)"
                      class="flex-1 min-w-0 text-xs border border-border rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-primary/60" />
                  </div>
                </div>
                <!-- Transparency is how a "no data" class is expressed, and it has to be reachable:
                     dropping it would paint that class over everything beneath the layer. -->
                <p v-if="rasterClasses.length" class="text-[10px] text-muted-foreground/70">
                  A colour per pixel value. Re-reading keeps the colours you have already chosen.
                </p>
              </div>

              <div v-show="rasterColorMode !== 'classes' || isHillshade || isContours">
                <label class="text-xs text-muted-foreground">Color palette</label>
                <select :value="config.style?.colormap || ''" :disabled="config.style?.algorithm === 'hillshade'"
                  @change="emitStyle({ colormap: $event.target.value || null })"
                  class="mt-0.5 w-full text-xs border border-border rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-primary/60 disabled:opacity-50">
                  <option value="">None (grayscale)</option>
                  <option v-for="cm in colormaps" :key="cm" :value="cm">{{ cm }}</option>
                </select>
                <!-- Reversing is not a preference: depth, deprivation and error all read
                     dark-for-high, which is the opposite of most sequential ramps. -->
                <label v-if="config.style?.colormap"
                  class="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer mt-1.5">
                  <input type="checkbox" :checked="!!config.style?.colormap_reverse"
                    @change="emitStyle({ colormap_reverse: $event.target.checked })"
                    class="accent-primary" />
                  Reverse the palette
                </label>
              </div>
              <!-- ONE CHOICE, not two checkboxes: TiTiler takes a single `algorithm`, so hillshade
                   and contours are mutually exclusive and a pair of ticks would let the user ask
                   for something that cannot be rendered. -->
              <div>
                <label class="text-xs text-muted-foreground">Terrain rendering</label>
                <select :value="config.style?.algorithm || ''"
                  @change="setAlgorithm($event.target.value)"
                  class="mt-0.5 w-full text-xs border border-border rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-primary/60">
                  <option value="">None</option>
                  <option value="hillshade">Hillshade</option>
                  <option value="contours">Contour lines</option>
                </select>
              </div>
              <div v-if="config.style?.algorithm === 'hillshade'" class="flex items-center gap-1.5"
                title="Vertical exaggeration (Z factor)">
                <label class="text-xs text-muted-foreground">Z factor</label>
                <input type="number" min="0.1" max="10" step="0.1" :value="config.style?.zfactor ?? 1"
                  @input="emitStyle({ zfactor: parseFloat($event.target.value) || 1 })"
                  class="w-14 text-xs border border-border rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-primary/60" />
              </div>
              <div v-if="config.style?.algorithm === 'contours'" class="flex items-end gap-2">
                <div class="flex-1">
                  <label class="text-xs text-muted-foreground" title="Spacing between contour lines, in the raster's own units">
                    Interval
                  </label>
                  <!-- DECIMALS ARE ALLOWED AGAIN, and they now work. TiTiler types the interval as
                       an integer with a minimum of 0, so 1 is the finest it can express — and a
                       vegetation index running 0.556-0.947 is narrower than that end to end, which
                       drew the whole raster as one flat dark band with no interval able to fix it.
                       `mapStyle.js`/`titiler.py` scale the DATA instead (`expression=b1*1000`), so
                       an interval of 0.05 becomes an ordinary 50 and the number typed here is the
                       one in the raster's own units. -->
                  <input type="number" min="0" step="any" :value="contourInterval"
                    @input="emitStyle({ increment: parseFloat($event.target.value) || null })"
                    class="mt-0.5 w-full text-xs border border-border rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-primary/60" />
                </div>
                <div class="w-20">
                  <label class="text-xs text-muted-foreground">Line width</label>
                  <input type="number" min="1" max="10" step="1" :value="config.style?.thickness ?? 1"
                    @input="emitStyle({ thickness: parseInt($event.target.value, 10) || null })"
                    class="mt-0.5 w-full text-xs border border-border rounded px-1.5 py-1 focus:outline-none focus:ring-1 focus:ring-primary/60" />
                </div>
              </div>
              <!-- THE CONTOUR COLOURS. TiTiler's algorithm hard-codes both — `cmap.get("terrain")`
                   for the background and black for the lines — so neither could be chosen. The
                   server reproduces the same picture as band maths plus an explicit colormap when
                   either differs from those defaults, and uses the algorithm untouched when they
                   do not, so an existing layer renders byte-identically. -->
              <div v-if="config.style?.algorithm === 'contours'" class="flex items-end gap-2">
                <div class="flex-1 min-w-0">
                  <label class="text-xs text-muted-foreground">Relief palette</label>
                  <select :value="config.style?.contour_palette || 'terrain'"
                    @change="emitStyle({ contour_palette: $event.target.value })"
                    class="mt-0.5 w-full text-xs border border-border rounded px-1.5 py-1">
                    <option v-for="r in CONTOUR_PALETTES" :key="r" :value="r">{{ r }}</option>
                  </select>
                </div>
                <div>
                  <label class="text-xs text-muted-foreground block">Line colour</label>
                  <input type="color" :value="config.style?.contour_color || '#000000'"
                    @input="emitStyle({ contour_color: $event.target.value })"
                    class="mt-0.5 h-7 w-12 border border-border rounded cursor-pointer bg-transparent" />
                </div>
              </div>
              <div v-if="config.style?.algorithm === 'contours'"
                   class="flex h-2 rounded overflow-hidden" aria-hidden="true">
                <span v-for="(c, i) in contourPalettePreview" :key="i" class="flex-1"
                  :style="{ backgroundColor: c }"></span>
              </div>
              <!-- The stretch is not decoration under contours: it is the range the coloured relief
                   behind the lines is drawn over. Without it TiTiler spans −12000–8000 m and a
                   survey DEM comes out one flat colour, so say what the numbers are doing. -->
              <p v-if="config.style?.algorithm === 'contours'" class="text-xs text-muted-foreground">
                Lines every {{ contourInterval }} units, over the stretch below —
                that range colours the relief behind them.
                <button v-if="!config.style?.rescale" @click="autoStretch" :disabled="autoStretching"
                  class="text-primary hover:text-primary/80 font-medium disabled:opacity-50">
                  Set it automatically
                </button>
              </p>
            </template>

            <!-- Stretch is disabled under hillshade: the algorithm returns a finished 0–255 relief
                 image and TiTiler applies rescale AFTER it, so a data-range stretch would flatten
                 the shading to one colour. Saying so beats letting the control look available. -->
            <div :class="(isHillshade || isClassified) ? 'opacity-50' : ''">
              <div class="flex items-center justify-between mb-0.5">
                <label class="text-xs text-muted-foreground">Stretch (min / max)</label>
                <button @click="autoStretch" :disabled="autoStretching || isHillshade || isClassified"
                  class="text-xs text-primary hover:text-primary/80 font-medium disabled:opacity-50"
                  title="Compute min/max from the raster (2–98th percentile)">
                  {{ autoStretching ? 'Computing…' : '⚡ Auto' }}
                </button>
              </div>
              <div class="flex items-center gap-2">
                <input type="number" :value="rescaleMin" :disabled="isHillshade || isClassified" @input="setRescale('min', $event.target.value)" placeholder="min"
                  class="w-16 text-xs border border-border rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-primary/60 disabled:opacity-50" />
                <span class="text-muted-foreground/40">–</span>
                <input type="number" :value="rescaleMax" :disabled="isHillshade || isClassified" @input="setRescale('max', $event.target.value)" placeholder="max"
                  class="w-16 text-xs border border-border rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-primary/60 disabled:opacity-50" />
              </div>
              <p class="text-[10px] text-muted-foreground/70 mt-0.5">
                {{ isHillshade ? 'Not used while Hillshade is on — the shading is already 0–255.'
                   : isClassified ? 'Not used with unique values — each class is matched on its raw pixel value, and a stretch would change those.'
                   : isContours ? 'Under contours this is the elevation range the relief is coloured over — set it, or the whole raster draws as one flat band.'
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

        </div>

        <!-- Default style actions, PINNED as a footer rather than left at the end of the scrolling
             body. They were the last thing in a panel that can run to several screens, so "save as
             default" was only reachable by scrolling to the very bottom of everything — and when the
             popover overflowed the window it could not be reached at all. A control that writes
             something has to stay in view.

             Not applicable to external sources, and hidden when standalone: in My Data this IS the
             default style, so "use default" and "save as default" would be a control acting on
             itself — the host's own Save button writes it. -->
        <div v-if="config.layer_type !== 'external' && !standalone"
          class="flex items-center gap-2 px-3 py-2 border-t border-border/60 flex-shrink-0 bg-card rounded-b-lg">
          <button v-if="layer?.default_style" @click="useDefault" class="text-xs text-primary hover:text-primary/80 font-medium"
            title="Apply saved default style to this portal">↩ Use default</button>
          <button @click="saveDefault" :disabled="savingDefault" class="text-xs text-muted-foreground hover:text-foreground ml-auto"
            title="Save current style as the default for this layer">{{ savingDefault ? 'Saving…' : '⭐ Save as default' }}</button>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useDataStore } from '@/stores/data'
import { saveVectorDefaultStyle, saveRasterDefaultStyle, listColormaps, getRasterStats,
         getRasterUniqueValues,
         getFieldStats } from '@/api'
// The shared symbology vocabulary — twin of api/geodeploy/services/symbology.py. The swatch and
// the legend here must describe exactly what the published portal will draw.
import { RAMPS, DIVERGING, NO_OUTLINE, markerOutline, legendEntries, rampColors,
         representativeColor, pillarRadius } from '@/lib/symbology'
import LegendSwatch from '@/components/LegendSwatch.vue'
import { contourRange, defaultIncrement, rasterStyleOf } from '@/lib/mapStyle'
import { TrashIcon, LocateIcon } from '@/views/icons'

const props = defineProps({
  config: Object,
  // Render the symbology body ALONE, without the layer row, the popover chrome or the
  // default-style actions — for a host that is not a portal layer list (My Data).
  standalone: { type: Boolean, default: false },
})
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
//: Breathing room between the popover and every window edge.
const POP_MARGIN = 8
//: The height the popover tries to keep. When the swatch sits low on screen it is lifted so it still
//: gets this much, instead of being pinned to the anchor and running off the bottom.
const POP_PREFERRED_H = 420
//: Never lift it so far that it covers the whole window on a short one.
const POP_MIN_H = 220

function positionPop() {
  const el = swatchBtn.value
  if (!el) return
  const r = el.getBoundingClientRect()
  // Widened from 230: the panel now carries a colour-mode picker, a field, class count/method/ramp,
  // an editable legend, marker + outline controls and a 3D block. At 230 the labelled rows wrapped.
  const w = 288
  let left = r.right + POP_MARGIN
  if (left + w > window.innerWidth) left = Math.max(POP_MARGIN, r.left - w - POP_MARGIN)

  // HEIGHT IS DERIVED FROM THE SPACE THAT EXISTS, not assumed. The old form was
  // `top: min(r.top, innerHeight - 380)`, which hard-coded 380px as the popover's height while the
  // body was `max-h-[70vh]` plus a header — about 750px on a 1024px window. So the box was placed
  // as though it were half its real size and the bottom ran off the screen, taking "Save as
  // default" with it. Now the top is chosen first and maxHeight is whatever is left below it, so
  // the popover fits by construction at any window size and any content length.
  const room = window.innerHeight - POP_MARGIN
  let top = r.top
  if (room - top < POP_PREFERRED_H) {
    top = room - POP_PREFERRED_H          // lift it to keep a usable panel visible
  }
  top = Math.max(POP_MARGIN, Math.min(top, room - POP_MIN_H))
  popStyle.value = {
    left: left + 'px',
    top: top + 'px',
    width: w + 'px',
    maxHeight: (room - top) + 'px',
  }
}
function onDocClick(e) {
  if (!showStyle.value) return
  if (popEl.value && !popEl.value.contains(e.target) && swatchBtn.value && !swatchBtn.value.contains(e.target)) {
    showStyle.value = false
  }
}
// Re-fit on resize. The popover's height is derived from the window's, so a window that changes
// size while it is open would otherwise keep a maxHeight computed for the old one — which is the
// same overflow this fixed, arrived at a different way.
function onWinResize() { if (showStyle.value) positionPop() }
onMounted(async () => {
  document.addEventListener('mousedown', onDocClick)
  window.addEventListener('resize', onWinResize)
  if (props.config.layer_type === 'raster') {
    try { const { data } = await listColormaps(); colormaps.value = data } catch {}
  }
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocClick)
  window.removeEventListener('resize', onWinResize)
})

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
// The REQUESTED count, not the produced one. Reading it back from `classes.length` meant the box
// showed whatever came back: a column whose values tie (a real one on a live instance returns ONE
// class however many you ask for) snapped the input to 1 and there was no way to ask for more —
// the control fought the user. `classes_n` is what was asked; the note below says what happened.
const classCount = computed(() =>
  props.config.style?.classes_n || (props.config.style?.classes || []).length || 5)
const producedClasses = computed(() => (props.config.style?.classes || []).length)
const fewerThanAsked = computed(() =>
  colorMode.value === 'graduated' && producedClasses.value > 0
  && producedClasses.value < classCount.value)
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

// The column actually driving colour — null for a single symbol, where naming a field would be a
// lie. Mirrors `services/symbology.color_field`, which is what the published legend uses.
const colorField = computed(() =>
  colorMode.value === 'single' ? null : (props.config.style?.color_field || null))

// The colour a size swatch is drawn in. A data-driven layer has no single colour, so the first
// class stands for the layer — the swatch is demonstrating SIZE, and an arbitrary blue would read
// as if the layer were blue.
const baseColor = computed(() =>
  (legend.value[0] && legend.value[0].color) || props.config.style?.color || '#3b82f6')

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
// 2-100, matching the server's clamp in `field-stats`. The old ceiling of 12 was working around
// `rampColors` snapping to one of seven anchor stops — twelve classes in seven colours — which is
// fixed; QGIS has no cap here and neither should this.
function setClassCount(n) { refreshClasses({ classes_n: Math.max(2, Math.min(100, parseInt(n) || 5)) }) }
function setMethod(m) { refreshClasses({ class_method: m }) }
function setRamp(r) { refreshClasses({ color_ramp: r }) }
/**
 * Flip the ramp — INSTANTLY, and without asking the server (issue #11).
 *
 * A reversed ramp is by construction the same colours in the opposite order (`ramp_colors` reverses
 * its sampled output), so reversing the STORED class colours is exactly equal to re-classifying with
 * `reverse=true` — and it is equal for hand-edited colours too, which a re-classify would discard.
 * That makes the round trip pure latency: the map recolours on the next tick instead of after a
 * request that can also fail.
 *
 * The flag is still stored, so a later change of method or class count keeps the direction.
 */
function toggleRampReverse() {
  const style = props.config.style || {}
  const patch = { color_ramp_reverse: !rampReverse.value }
  if ((style.classes || []).length) {
    const colors = style.classes.map(c => c.color).reverse()
    patch.classes = style.classes.map((c, i) => ({ ...c, color: colors[i] }))
  }
  if ((style.categories || []).length) {
    const colors = style.categories.map(c => c.color).reverse()
    patch.categories = style.categories.map((c, i) => ({ ...c, color: colors[i] }))
  }
  emitStyle(patch)
}

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
      classes_n: over.classes_n || classCount.value,
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
    return
  }
  // Past the last category is the `match` fallback — every value the classification did not list,
  // which on a truncated column is most of them. It is a separate key, not a category with no
  // value, because that is how MapLibre's `match` and QGIS's own "all other values" both spell it.
  const cats = props.config.style?.categories || []
  if (i >= cats.length) { emitStyle({ other_color: color }); return }
  emitStyle({ categories: cats.map((c, j) => j === i ? { ...c, color } : c) })
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
// ── Size from a field (issue #21) ────────────────────────────────────────────────────────────────
// The style keys are `size_mode: 'proportional'` + `size_field` + `size_stops`, exactly as
// services/symbology.py reads them; two stops, because the server interpolates LINEARLY between
// them and a size legend is only honest if it is a straight line.
const canSizeByField = computed(() =>
  !!numericFields.value.length && (geomType.value === 'point' || geomType.value === 'line'))
const sizeField = computed(() =>
  (props.config.style?.size_mode === 'proportional' && props.config.style?.size_field) || '')
const sizeStops = computed(() => {
  const s = props.config.style?.size_stops
  return Array.isArray(s) && s.length === 2 ? s : null
})
// The data values the two stops sit at, and the pixel sizes they map to.
const sizeRange = computed(() => sizeStops.value ? [sizeStops.value[0][0], sizeStops.value[1][0]] : null)
const sizePx = computed(() => sizeStops.value
  ? [sizeStops.value[0][1], sizeStops.value[1][1]]
  : (geomType.value === 'point' ? [4, 20] : [1, 8]))
const sizeBusy = ref(false)

async function pickSizeField(field) {
  if (!field) {
    // `size_mode` back to fixed AND the field cleared: leaving a stale field behind means the next
    // person to switch it on inherits a column they never chose.
    emitStyle({ size_mode: 'fixed', size_field: null, size_stops: null })
    return
  }
  sizeBusy.value = true
  try {
    // The server already knows the column's min and max — asking beats guessing a range, and it is
    // the same endpoint the colour classification uses.
    const { data } = await getFieldStats(props.config.layer_id, { field, classes: 2 })
    const lo = data.min ?? 0
    const hi = data.max ?? (lo + 1)
    const px = sizePx.value
    emitStyle({
      size_mode: 'proportional',
      size_field: field,
      // Equal values would make MapLibre's interpolate stops non-ascending, which throws.
      size_stops: [[lo, px[0]], [hi > lo ? hi : lo + 1, px[1]]],
    })
  } catch (e) {
    statsError.value = e?.response?.data?.detail || 'Could not read that field.'
  } finally {
    sizeBusy.value = false
  }
}

function setSizePx(index, value) {
  const stops = sizeStops.value
  if (!stops) return
  const px = Math.max(0.5, Math.min(60, parseFloat(value) || 1))
  const next = stops.map(s => s.slice())
  next[index][1] = px
  emitStyle({ size_stops: next })
}

const canExtrude = computed(() => {
  // NOT gated on a numeric column any more. It was, back when a height could only come from a
  // field — which hid the whole 3D section for a building-footprints layer carrying no height
  // attribute, the single most common thing anyone wants to extrude, and made a 2.5D style pushed
  // from QGIS impossible to edit here. A flat height needs no column at all.
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

// ── Disclosure ──────────────────────────────────────────────────────────────
// One object rather than a ref per section: the panel already carries a lot of state, and "which
// sections are open" is one fact about the view, not five.
const open = reactive({ labelMore: false, more: false, more3d: false })
function toggle(name) { open[name] = !open[name] }

// Nine positions, in the order they appear in the grid. "Above the point" is what somebody means;
// `text-anchor: bottom` is how MapLibre spells it, and the two read as opposites — the anchor names
// the part of the TEXT that touches the point, so text sitting above it is anchored at its bottom.
const labelAnchors = [
  { value: 'bottom-right', mark: '↖', title: 'Above and left' },
  { value: 'bottom', mark: '↑', title: 'Above' },
  { value: 'bottom-left', mark: '↗', title: 'Above and right' },
  { value: 'right', mark: '←', title: 'Left' },
  { value: 'center', mark: '•', title: 'Centred on the point' },
  { value: 'left', mark: '→', title: 'Right' },
  { value: 'top-right', mark: '↙', title: 'Below and left' },
  { value: 'top', mark: '↓', title: 'Below' },
  { value: 'top-left', mark: '↘', title: 'Below and right' },
]

const labelOffset = computed(() => {
  const o = props.config.style?.labels?.offset
  return Array.isArray(o) && o.length === 2 ? o : [0, 0]
})

function setLabelOffset(i, raw) {
  const next = [...labelOffset.value]
  next[i] = parseFloat(raw) || 0
  setLabels({ offset: (next[0] || next[1]) ? next : undefined })
}

// ── Heatmap ─────────────────────────────────────────────────────────────────
// A renderer, not a paint option: it replaces the points entirely.
const heatmapOn = computed(() => !!props.config.style?.heatmap?.enabled)

// The ramps a heatmap can use. A NAME plus its opaque colours, low density first — the transparent
// stop is added by `heatmapColors`, never stored in this table, so reversing cannot strand it at
// the wrong end. Adding a ramp here is the whole job of adding a ramp.
const heatmapRamps = [
  { name: 'heat', label: 'Blue → green → red', colors: ['#3b82f6', '#22c55e', '#eab308', '#ef4444'] },
  { name: 'magma', label: 'Magma (dark → yellow)', colors: ['#3b0f70', '#8c2981', '#de4968', '#fe9f6d', '#fcfdbf'] },
  { name: 'inferno', label: 'Inferno (dark → pale)', colors: ['#420a68', '#932667', '#dd513a', '#fca50a', '#fcffa4'] },
  { name: 'viridis', label: 'Viridis (blue → yellow)', colors: ['#440154', '#31688e', '#35b779', '#fde725'] },
  { name: 'plasma', label: 'Plasma (blue → yellow)', colors: ['#0d0887', '#9c179e', '#ed7953', '#f0f921'] },
  { name: 'turbo', label: 'Turbo (blue → red)', colors: ['#30123b', '#28bbec', '#a4fc3c', '#fb8022', '#7a0403'] },
  { name: 'blues', label: 'Blues', colors: ['#c6dbef', '#6baed6', '#3182bd', '#08519c'] },
  { name: 'greens', label: 'Greens', colors: ['#c7e9c0', '#74c476', '#31a354', '#006d2c'] },
  { name: 'reds', label: 'Reds', colors: ['#fcbba1', '#fb6a4a', '#de2d26', '#a50f15'] },
  { name: 'purples', label: 'Purples', colors: ['#dadaeb', '#9e9ac8', '#756bb1', '#54278f'] },
  { name: 'oranges', label: 'Oranges', colors: ['#fdd0a2', '#fd8d3c', '#e6550d', '#a63603'] },
  { name: 'spectral', label: 'Spectral (diverging)', colors: ['#3288bd', '#99d594', '#e6f598', '#fc8d59', '#d53e4f'] },
]

// A `#rrggbb` as the same colour at zero alpha — so the ramp fades out through its OWN low colour
// rather than through MapLibre's blue default. The renderers force the first stop transparent
// whatever they are given, which is the real safety net; this only decides which hue it fades from.
function transparentOf(hex) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex || ''))
  if (!m) return 'rgba(0,0,255,0)'
  return `rgba(${parseInt(m[1], 16)},${parseInt(m[2], 16)},${parseInt(m[3], 16)},0)`
}

// THE STORED RAMP: a transparent stop, then the colours. Reversing flips the colours and rebuilds
// the transparent stop from whichever is now lowest — reversing the finished list instead would put
// transparency at the HIGH end, which paints the whole viewport at density zero. That is the one
// mistake that makes a heatmap look broken rather than merely wrong, so it is done here, once.
function heatmapColors(name, reverse) {
  const ramp = heatmapRamps.find(r => r.name === name) || heatmapRamps[0]
  const colors = reverse ? [...ramp.colors].reverse() : ramp.colors
  return [transparentOf(colors[0]), ...colors]
}

// The chosen ramp is remembered BY NAME beside the colours it produced — the same device
// `fill_pattern.hatch` and `line_marker.preset` use. Matching the stored colour list back against
// the table would work until the day a ramp's colours are edited or reversed, at which point the
// dropdown would silently jump to the first entry.
const heatmapRampName = computed(() => {
  const block = props.config.style?.heatmap || {}
  if (block.ramp_name && heatmapRamps.some(r => r.name === block.ramp_name)) return block.ramp_name
  // A heatmap saved before ramps had names, or one pushed from QGIS: fall back to matching colours.
  const current = JSON.stringify(block.ramp || [])
  const hit = heatmapRamps.find(r => JSON.stringify(heatmapColors(r.name, false)) === current)
  return hit ? hit.name : 'heat'
})

const heatmapReverse = computed(() => !!props.config.style?.heatmap?.reverse)

const heatmapPreview = computed(() => heatmapColors(heatmapRampName.value, heatmapReverse.value))

// A chequerboard behind the preview bar, so the transparent end reads as a fade to nothing rather
// than as a ramp that merely starts pale. ONE background image, deliberately: the first attempt
// drew each ramp as its own gradient over a chequer and the `background-size` list applied to the
// gradient layer too, repeating it every 8px — a box of vertical stripes instead of a ramp.
const CHECKER = {
  backgroundImage: 'repeating-conic-gradient(rgba(128,128,128,.3) 0% 25%, transparent 0% 50%)',
  backgroundSize: '8px 8px',
}

function setHeatmap(patch) {
  emitStyle({ heatmap: { ...(props.config.style?.heatmap || {}), ...patch, enabled: true } })
}

function setHeatmapOn(on) {
  if (!on) { emitStyle({ heatmap: undefined }); return }
  setHeatmap({ radius: props.config.style?.heatmap?.radius ?? 20,
               ramp_name: heatmapRamps[0].name, reverse: false,
               ramp: heatmapColors(heatmapRamps[0].name, false) })
}

// `ramp` is what every renderer reads; `ramp_name` and `reverse` are how this panel remembers the
// choice that produced it. All three are written together so they can never disagree.
function setHeatmapRamp(name) {
  setHeatmap({ ramp_name: name, ramp: heatmapColors(name, heatmapReverse.value) })
}

function toggleHeatmapReverse() {
  const reverse = !heatmapReverse.value
  setHeatmap({ reverse, ramp: heatmapColors(heatmapRampName.value, reverse) })
}

async function setHeatmapWeight(field) {
  if (!field) { setHeatmap({ weight_field: undefined, weight_max: undefined }); return }
  // THE MAXIMUM COMES FROM THE SERVER, not from the column list — a column here is a name and a
  // type, and nothing in the browser knows how big its values get. The weight is normalised against
  // it so the ramp spans the data instead of saturating on the first large value; without a
  // maximum the renderer leaves the weight alone and the field would silently do nothing.
  //
  // Same endpoint the classifier uses, for the same reason: one place decides what a column holds.
  setHeatmap({ weight_field: field })
  try {
    const { data } = await getFieldStats(props.config.layer_id, { field, classes: 2 })
    if (Number.isFinite(data?.max)) setHeatmap({ weight_field: field, weight_max: data.max })
  } catch (e) {
    // A weight that cannot be normalised still draws — every point simply counts the same, which
    // is the unweighted map rather than a broken one.
  }
}

// ── Hatches ─────────────────────────────────────────────────────────────────
// The tile is generated HERE, in the browser, into the same `fill_pattern` key and the same PNG
// data URI the QGIS plugin produces — so a hatch made here and one pushed from QGIS are the same
// thing to every renderer. The four angles offered are the four at which a square tile CLOSES;
// see the plugin's fills.py for why the rest cannot.
const hatchPresets = [
  { name: 'none', title: 'Solid fill' },
  { name: 'horizontal', title: 'Horizontal lines' },
  { name: 'vertical', title: 'Vertical lines' },
  { name: 'forward', title: 'Diagonal lines' },
  { name: 'back', title: 'Diagonal lines, the other way' },
  { name: 'cross', title: 'Cross-hatch' },
]

function hatchTile(name, color) {
  const side = 12
  const cv = document.createElement('canvas')
  cv.width = side
  cv.height = side
  const ctx = cv.getContext('2d')
  if (!ctx) return null
  ctx.strokeStyle = color || '#333333'
  ctx.lineWidth = 1.5
  const line = (x1, y1, x2, y2) => {
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke()
  }
  // Every stroke is drawn at BOTH edges as well as the middle, so the tile closes when repeated.
  if (name === 'horizontal' || name === 'cross') {
    line(-1, 0, side + 1, 0); line(-1, side, side + 1, side); line(-1, side / 2, side + 1, side / 2)
  }
  if (name === 'vertical' || name === 'cross') {
    line(0, -1, 0, side + 1); line(side, -1, side, side + 1); line(side / 2, -1, side / 2, side + 1)
  }
  if (name === 'forward') {
    for (const k of [-1, 0, 1]) line(k * side - 1, side + 1, k * side + side + 1, -1)
  }
  if (name === 'back') {
    for (const k of [-1, 0, 1]) line(k * side - 1, -1, k * side + side + 1, side + 1)
  }
  return cv.toDataURL('image/png')
}

const activeHatch = computed(() => props.config.style?.fill_pattern?.hatch || 'none')

function hatchPreview(name) {
  return hatchTile(name, props.config.style?.color || '#333333')
}

function setHatch(name) {
  if (name === 'none') { emitStyle({ fill_pattern: undefined }); return }
  const image = hatchTile(name, props.config.style?.color || '#333333')
  if (!image) return
  // `hatch` rides alongside the pixels so this panel can show which preset is selected. Nothing
  // renders from it — the image is what draws — so a tile pushed from QGIS simply highlights none.
  emitStyle({ fill_pattern: { image, width: 12, height: 12, hatch: name } })
}

// ── Direction arrows ────────────────────────────────────────────────────────
// Generated HERE, in the browser, into the same `line_marker` key and the same PNG data URI the
// QGIS plugin's `arrows.py` produces — so an arrow drawn here and one pushed from QGIS are the same
// thing to every renderer. The head points along +x because `symbol-placement: line` with
// `icon-rotation-alignment: map` gives an icon the line's own bearing at rotation 0; a head drawn
// pointing up would sit across the line instead of along it.
const arrowPresets = [
  { name: 'none', title: 'No arrows' },
  { name: 'forward', title: 'Arrows along the line' },
  { name: 'back', title: 'Arrows against the line' },
  { name: 'double', title: 'Arrows both ways' },
]

function arrowTile(name, color) {
  if (name === 'none') return null
  const h = 10
  const len = 9
  const w = name === 'double' ? len * 2 : len
  const cv = document.createElement('canvas')
  cv.width = w
  cv.height = h
  const ctx = cv.getContext('2d')
  if (!ctx) return null
  ctx.fillStyle = color || '#333333'
  const tri = (ax, ay, bx, by, cx, cy) => {
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.lineTo(cx, cy); ctx.closePath(); ctx.fill()
  }
  if (name === 'forward') tri(w, h / 2, 0, 0, 0, h)
  if (name === 'back') tri(0, h / 2, w, 0, w, h)
  if (name === 'double') { tri(0, h / 2, len, 0, len, h); tri(w, h / 2, len, 0, len, h) }
  return { image: cv.toDataURL('image/png'), width: w, height: h }
}

// Only an arrow this panel drew is highlighted. A `line_marker` from QGIS — ticks, chevrons, a real
// `QgsArrowSymbolLayer` — carries no `preset`, so it selects none of these rather than being
// mislabelled as one of them, and the "Styled in QGIS" block explains it instead.
const activeArrow = computed(() => props.config.style?.line_marker?.preset || 'none')

function arrowPreview(name) {
  const tile = arrowTile(name, props.config.style?.color || '#333333')
  return tile ? tile.image : null
}

function setArrow(name) {
  if (name === 'none') { emitStyle({ line_marker: undefined }); return }
  const tile = arrowTile(name, props.config.style?.color || '#333333')
  if (!tile) return
  emitStyle({
    line_marker: {
      ...tile,
      preset: name,
      spacing: props.config.style?.line_marker?.spacing ?? DEFAULT_ARROW_SPACING,
    },
  })
}

// Pixels between arrowheads. Far enough apart to read as direction markers rather than a dotted
// line; matches `arrows.DEFAULT_SPACING_PX` so a QGIS arrow and one drawn here space alike.
const DEFAULT_ARROW_SPACING = 90

function setArrowSpacing(value) {
  const marker = props.config.style?.line_marker
  if (!marker) return
  emitStyle({ line_marker: { ...marker, spacing: Math.max(10, parseFloat(value) || DEFAULT_ARROW_SPACING) } })
}

// ── Marker placement ────────────────────────────────────────────────────────
const markerOffset = computed(() => {
  const o = props.config.style?.marker_offset
  return Array.isArray(o) && o.length === 2 ? o : [0, 0]
})

function setMarkerOffset(i, raw) {
  const next = [...markerOffset.value]
  next[i] = parseFloat(raw) || 0
  emitStyle({ marker_offset: (next[0] || next[1]) ? next : undefined })
}

// ── Labels ──────────────────────────────────────────────────────────────────
// A label is a second thing drawn for the same feature, so it lives in its own block of the style
// and becomes its own MapLibre layer — see services/symbology.label_layout. Rasters have nothing to
// label, and neither does an external source we do not hold the attributes for.
const canLabel = computed(() => props.config.layer_type === 'vector' && styleFields.value.length > 0)
const labelsOn = computed(() => !!props.config.style?.labels?.enabled)

function setLabels(patch) {
  // MERGED, and enabling as soon as anything is set: naming a size on an unlabelled layer and
  // getting nothing would be a puzzle rather than a safeguard. Mirrors the CLI's `--label-*`.
  emitStyle({ labels: { ...(props.config.style?.labels || {}), ...patch, enabled: true } })
}

function setLabelsOn(on) {
  if (!on) {
    // Turned OFF rather than deleted, so the field and colours are still there when it is turned
    // back on — the same courtesy the classification controls give.
    emitStyle({ labels: { ...(props.config.style?.labels || {}), enabled: false } })
    return
  }
  const first = styleFields.value[0]
  emitStyle({
    labels: {
      field: first ? first.name : '',
      ...(props.config.style?.labels || {}),
      enabled: true,
    },
  })
}

// ── Where the layer draws ───────────────────────────────────────────────────
function setZoom(key, raw) {
  const text = String(raw ?? '').trim()
  if (!text) { emitStyle({ [key]: undefined }); return }
  const z = Math.max(0, Math.min(24, parseFloat(text)))
  emitStyle({ [key]: Number.isFinite(z) ? z : undefined })
}

// ── Symbology authored in QGIS ──────────────────────────────────────────────
// Each of these OUTRANKS the controls above — rules replace the single symbol, a pattern replaces
// the fill colour, a rendered marker replaces the shape. Naming them is what stops the colour
// picker looking broken; `clearQgisStyling` is the way back.
const qgisStyling = computed(() => {
  const st = props.config.style || {}
  const out = []
  const rules = Array.isArray(st.rules) ? st.rules.length : 0
  if (rules) out.push(`${rules} rule${rules === 1 ? '' : 's'}, each with its own filter and symbol`)
  // A hatch or an arrow this panel DREW is not QGIS styling — it is editable right above, and
  // listing it here would say "edited in QGIS" about a control the author just used. Both carry a
  // `hatch`/`preset` name for exactly this; anything from QGIS has neither.
  if (st.fill_pattern?.image && !st.fill_pattern.hatch) {
    out.push('A pattern fill — a hatch, or an image tiled across the shape')
  }
  if (st.marker_image) out.push('A marker drawn in QGIS, carried as a picture')
  if (st.line_marker?.image && !st.line_marker.preset) {
    out.push(st.line_marker.arrow ? 'An arrow line drawn in QGIS'
      : 'Markers repeated along the line')
  }
  if (st.labels?.qgis_font?.family) out.push(`Labels in ${st.labels.qgis_font.family}`)
  return out
})

function clearQgisStyling() {
  emitStyle({
    rules: undefined,
    fill_pattern: undefined,
    marker_image: undefined,
    line_marker: undefined,
  })
}

// The ramps a contour BACKGROUND can use. Only ramps GeoDeploy holds the actual colours for — the
// server builds the colormap itself, so a name it cannot resolve to RGB is a name it cannot draw.
// `terrain` is the hypsometric default and matches what TiTiler's own algorithm bakes in, so
// leaving it alone keeps the picture identical and the URL byte-for-byte what it already was.
const CONTOUR_PALETTES = ['terrain', 'viridis', 'magma', 'blues', 'greens', 'oranges', 'reds',
  'rdbu', 'brbg', 'spectral']

const contourPalettePreview = computed(() =>
  rampColors(props.config.style?.contour_palette || 'terrain', 12))

// What the interval box shows: the author's number, or the default the renderers will actually use
// — derived from the raster's own stretch, not TiTiler's global-DEM 35. Showing 35 while the tiles
// were drawn at 0.05 would be a hint that lies about the map. Mirrors
// `lib/mapStyle.js::defaultIncrement` and `services/titiler.py::_default_increment`.
const contourInterval = computed(() => {
  const own = Number(props.config.style?.increment)
  if (Number.isFinite(own) && own > 0) return own
  const [lo, hi] = contourRange(props.config.style || {})
  return defaultIncrement(lo, hi)
})

function setExtrusion(patch) {
  emitStyle({ extrusion: { ...(props.config.style?.extrusion || {}), ...patch } })
}

// ── 3D / 2.5D ───────────────────────────────────────────────────────────────
// A height for a layer that has no height column. QGIS's 2.5D renderer defaults to 10 (in map
// units); metres is what every height in GeoDeploy means, and 10 m is a three-storey building —
// so ticking the box on a footprints layer shows something recognisable rather than nothing.
const FLAT_HEIGHT_M = 10

// Which of QGIS's two 3D renderers this style is: a field makes it attribute-driven, no field
// makes it 2.5D. Derived rather than stored, so a style arriving from the plugin, the CLI or an
// older portal lands in the right mode without a migration.
const extrusionMode = computed(() => (props.config.style?.extrusion?.field ? 'field' : 'fixed'))

function setExtrusionMode(mode) {
  if (mode === 'field') {
    // `height` is left behind deliberately: switching back restores the number that was typed,
    // and the renderers all ignore it while a field is set.
    setExtrusion({ field: numericFields.value[0]?.name || '' })
  } else {
    setExtrusion({ field: '', height: props.config.style?.extrusion?.height ?? FLAT_HEIGHT_M })
  }
}

function setExtrusionOn(on) {
  if (!on) { setExtrusion({ enabled: false }); return }
  // Ticking the box must DRAW something. Without a height of some kind every renderer raises the
  // layer by zero — a control that reports itself as on while the map is unchanged, which reads as
  // a broken feature. A layer with a numeric column keeps the old behaviour (pick the column);
  // one without gets a flat height, which is the only thing that can work for it.
  const ex = props.config.style?.extrusion || {}
  if (ex.field || ex.height) { setExtrusion({ enabled: true }); return }
  if (numericFields.value.length) {
    setExtrusion({ enabled: true, field: numericFields.value[0].name })
  } else {
    setExtrusion({ enabled: true, height: FLAT_HEIGHT_M })
  }
}

// `base` is overloaded in the style — a NUMBER lifts every volume equally, a STRING names a column
// — which is exactly how MapLibre's own `fill-extrusion-base` is used, and how the paint builders
// on both sides already read it. The select turns that into three named choices.
const baseMode = computed(() => {
  const base = props.config.style?.extrusion?.base
  if (typeof base === 'string' && base) return 'field'
  return Number(base) > 0 ? 'fixed' : 'ground'
})

function setBaseMode(mode) {
  if (mode === 'ground') setExtrusion({ base: 0 })
  else if (mode === 'fixed') setExtrusion({ base: FLAT_HEIGHT_M })
  else setExtrusion({ base: numericFields.value[0]?.name || '' })
}

// Present only on a style that CAME from a 2.5D renderer — the plugin writes the angle and shadow
// it could not translate into this block so the trip back to QGIS can rebuild them.
const from25D = computed(() => !!props.config.style?.extrusion?.qgis25d)


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
const isContours = computed(() => props.config.style?.algorithm === 'contours')
// A value-lookup palette is matched on the RAW pixel values, so the stretch is not applied to it —
// see services/titiler.get_tile_url. Shown unavailable rather than as an inviting empty box.
const isClassified = computed(() => (props.config.style?.color_classes || []).length > 0)

// ── Raster: continuous ramp, or a colour per VALUE ────────────────────────────────────────────
// The mode is derived from the style rather than kept beside it, so re-opening the panel shows what
// the layer actually is instead of whatever was last clicked.
const rasterClasses = computed(() => props.config.style?.color_classes || [])
const rasterColorMode = ref(rasterClasses.value.length ? 'classes' : 'ramp')
const loadingValues = ref(false)
const valuesNote = ref('')

//: A colour per class, for a classification whose values have no order — the same qualitative set
//: the vector side uses (`services/symbology.CATEGORY_COLORS`), because a sequential ramp over
//: land-cover codes implies a ranking that is not there.
const CLASS_COLORS = ['#3b82f6', '#ef4444', '#22c55e', '#f59e0b', '#a855f7', '#06b6d4',
                      '#ec4899', '#84cc16', '#f97316', '#6366f1', '#14b8a6', '#eab308']

function setRasterColorMode(mode) {
  rasterColorMode.value = mode
  valuesNote.value = ''
  // Leaving classified mode CLEARS the classes: TiTiler gives an explicit mapping precedence over a
  // named ramp, so classes left behind would keep drawing and the palette picker would do nothing.
  if (mode !== 'classes' && rasterClasses.value.length) emitStyle({ color_classes: null })
  // Entering it clears the RAMP and its direction. `colormap_reverse` is not cosmetic here: the
  // reverse flag re-pairs an explicit palette's colours with the values in the opposite order, so a
  // flag left over from a reversed ramp silently swapped hand-picked class colours end for end —
  // class 0's colour drew on the highest class. The checkbox that sets it is hidden in this mode,
  // which is exactly why it has to be cleared rather than left to be found.
  else if (mode === 'classes') {
    if (props.config.style?.colormap || props.config.style?.colormap_reverse) {
      emitStyle({ colormap: null, colormap_reverse: false })
    }
    if (!rasterClasses.value.length) loadUniqueValues()
  }
}

function classHex(color) {
  const text = String(color || '').trim()
  return /^#[0-9a-fA-F]{6}/.test(text) ? text.slice(0, 7) : '#3b82f6'
}
function setClassColor(index, hex) {
  const next = rasterClasses.value.map((c, i) => (i === index ? { ...c, color: hex } : c))
  emitStyle({ color_classes: next })
}
function setClassLabel(index, label) {
  const next = rasterClasses.value.map((c, i) => (i === index ? { ...c, label } : c))
  emitStyle({ color_classes: next })
}

async function loadUniqueValues() {
  if (!layer.value) return
  loadingValues.value = true
  valuesNote.value = ''
  try {
    const { data } = await getRasterUniqueValues(layer.value.id, singleBand.value || undefined)
    if (!data?.categorical) {
      // Said plainly rather than by returning nothing: "this raster is continuous" is the answer,
      // not a failure, and the user needs it to stop looking for a classification that is not there.
      valuesNote.value = data?.reason || 'This raster has no usable classes.'
      return
    }
    // COLOURS ALREADY CHOSEN ARE KEPT. Re-reading after editing a colour is a normal thing to do
    // (a new class appears, a count changes), and regenerating the whole palette would throw away
    // the work every time.
    const existing = new Map(rasterClasses.value.map(c => [String(c.value), c]))
    const next = data.values.map((v, i) => {
      const had = existing.get(String(v.value))
      return {
        value: v.value,
        color: had?.color || CLASS_COLORS[i % CLASS_COLORS.length],
        label: had?.label ?? String(v.value),
      }
    })
    // `colormap_reverse` off with them: it re-pairs an explicit palette end for end, so a flag left
    // over from a reversed ramp would hand class 0 the colour chosen for the highest class.
    emitStyle({ color_classes: next, colormap: null, colormap_reverse: false })
    valuesNote.value = `${next.length} value${next.length === 1 ? '' : 's'} in the raster.`
  } catch (e) {
    valuesNote.value = e?.response?.data?.detail || 'Could not read the raster values.'
  } finally {
    loadingValues.value = false
  }
}

// Switching terrain rendering CLEARS the parameters of the mode being left. Keeping them would
// leave a zfactor on a contour layer and an interval on a hillshade — invisible in the map, but
// carried into every published portal, every share link and every round trip through QGIS, where
// something eventually reads them back and reports a change nobody made.
function setAlgorithm(value) {
  const patch = { algorithm: value || null }
  if (value !== 'hillshade') patch.zfactor = null
  if (value !== 'contours') { patch.increment = null; patch.thickness = null; patch.minz = null; patch.maxz = null }
  emitStyle(patch)
}
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
      : { opacity: props.config.opacity, ...rasterStyleOf(props.config.style) }
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
      : rasterStyleOf(ds),
    ...(props.config.layer_type === 'vector' ? { popup_fields: ds.popup_fields ?? [] } : {}),
  })
}
</script>
