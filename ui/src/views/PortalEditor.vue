<template>
  <div class="flex h-screen">
    <!-- Left panel. Wider than a normal page's sidebar: the main nav auto-collapses (60px) when the
         editor opens, and that freed ~164px is handed to these portal controls so the combined
         nav + panel width is unchanged — more room to work without eating the map. -->
    <div class="w-[484px] flex-shrink-0 bg-card border-r border-border flex flex-col overflow-hidden">

      <!-- Top bar -->
      <div class="px-4 py-3 border-b border-border flex items-center justify-between gap-2">
        <button @click="$router.push('/portals')" class="text-sm text-muted-foreground hover:text-foreground flex-shrink-0">← Back</button>
        <input v-if="renaming" ref="renameInput" v-model="renameTitle"
          @blur="commitRename" @keydown.enter.prevent="commitRename" @keydown.esc="cancelRename"
          maxlength="120"
          class="text-sm font-semibold flex-1 min-w-0 text-center bg-transparent border-b border-primary text-foreground focus:outline-none" />
        <button v-else @click="startRename" :disabled="!portal"
          class="text-sm font-semibold truncate flex-1 text-center hover:text-primary transition-colors"
          title="Rename portal (the public URL stays the same)">
          {{ portal?.title }}
        </button>
        <button @click="handlePublish" :disabled="busy || !portal"
          class="btn-primary text-xs py-1.5 flex-shrink-0">
          {{ portal?.published ? 'Re-publish' : 'Publish' }}
        </button>
      </div>

      <!-- Live URL bar -->
      <div v-if="portal?.published" class="px-4 py-2 bg-green-500/15 border-b border-green-500/30 flex items-center gap-2">
        <span class="w-2 h-2 rounded-full bg-green-500 flex-shrink-0 animate-pulse" />
        <a :href="`/portals/${portal.slug}/`" target="_blank"
          class="text-xs text-green-400 hover:text-green-900 truncate font-medium flex-1">
          /portals/{{ portal.slug }}/
        </a>
        <ExternalLinkIcon class="w-3.5 h-3.5 text-green-500 flex-shrink-0" />
      </div>

      <!-- Scrollable body -->
      <div class="flex-1 overflow-y-auto">

        <!-- Layers section -->
        <section class="p-4 border-b border-border/60">
          <div class="flex items-center justify-between mb-3">
            <h3 class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Layers</h3>
            <span class="flex items-center gap-2">
              <button v-if="availableLayers.length > 1" @click="addAllLayers" :disabled="addingAll"
                class="text-xs text-muted-foreground hover:text-foreground font-medium disabled:opacity-50">
                {{ addingAll ? 'Adding…' : `Add all (${availableLayers.length})` }}
              </button>
              <button @click="showAddLayer = !showAddLayer" class="text-xs text-primary hover:text-primary/80 font-medium">+ Add</button>
            </span>
          </div>

          <div v-if="showAddLayer" class="mb-3 p-2 bg-muted/40 rounded-lg text-xs space-y-0.5 max-h-40 overflow-y-auto border border-border">
            <p v-if="!availableLayers.length" class="text-muted-foreground/70 p-1">No ready layers available.</p>
            <div v-for="layer in availableLayers" :key="`${layer.type}-${layer.id}`"
              class="flex items-center justify-between p-1.5 hover:bg-card rounded cursor-pointer"
              @click="addLayer(layer)"
            >
              <span class="font-medium">{{ layer.name }}</span>
              <span class="text-muted-foreground/70 text-[10px] uppercase">{{ layer.type }}</span>
            </div>
          </div>

          <div v-if="!layerConfigs.length" class="text-xs text-muted-foreground/70 py-1">No layers added yet.</div>
          <template v-else>
            <!-- Catalog toolbar: search · expand/collapse all · add folder -->
            <div class="flex items-center gap-1.5 mb-1.5">
              <div class="relative flex-1">
                <svg class="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground/60 pointer-events-none"
                  viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>
                <input v-model="layerFilter" type="search" placeholder="Search layers…"
                  class="w-full text-xs bg-background border border-border rounded pl-7 pr-2 py-1 focus:outline-none focus:border-primary/60" />
              </div>
              <template v-if="hasGroups">
                <button @click="setAllCollapsed(false)" title="Expand all folders"
                  class="w-6 h-6 flex items-center justify-center rounded text-muted-foreground/70 hover:text-foreground hover:bg-muted flex-shrink-0">
                  <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="7 13 12 18 17 13"/><polyline points="7 6 12 11 17 6"/></svg>
                </button>
                <button @click="setAllCollapsed(true)" title="Collapse all folders"
                  class="w-6 h-6 flex items-center justify-center rounded text-muted-foreground/70 hover:text-foreground hover:bg-muted flex-shrink-0">
                  <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 11 12 6 7 11"/><polyline points="17 18 12 13 7 18"/></svg>
                </button>
              </template>
            </div>
            <div class="flex justify-end mb-1">
              <button @click="addRootFolder" class="text-[11px] text-muted-foreground/70 hover:text-primary">＋ Add folder</button>
            </div>
            <LayerTree :nodes="layerTree" :configs="layerConfigs" :all-groups="allGroups" :dnd="dnd" :filter="layerFilter"
              @layer-remove="removeLayerNode" @layer-update="onLayerUpdate"
              @layer-zoom="n => zoomToLayer(configForNode(n))" @group-zoom="zoomToGroup" @move-to="moveNodeToGroup" />
          </template>
        </section>

        <!-- Experience (V-11). FIRST, because the template list below is filtered to the templates
             that declare support for whatever is chosen here. -->
        <section class="p-4 border-b border-border/60">
          <h3 class="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Experience</h3>
          <div class="grid grid-cols-2 gap-2 mb-3">
            <button v-for="a in ARCHETYPES" :key="a.id"
              class="p-2 rounded-lg border text-left transition-colors"
              :class="resolvedLayout.archetype === a.id ? 'border-primary bg-primary/10' : 'border-border hover:border-muted-foreground/40'"
              @click="pickArchetype(a.id)">
              <span class="block text-xs font-medium" :class="resolvedLayout.archetype === a.id ? 'text-primary' : 'text-foreground/85'">{{ a.name }}</span>
              <span class="block text-[10px] text-muted-foreground/70 leading-snug">{{ a.desc }}</span>
            </button>
          </div>
        </section>

        <!-- Template section -->
        <section class="p-4 border-b border-border/60">
          <h3 class="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">
            Template <span class="normal-case font-normal text-muted-foreground/70">for {{ ARCHETYPE_NOUN[resolvedLayout.archetype] || 'this portal' }}</span>
          </h3>
          <p v-if="!templatesForArchetype.length" class="text-[11px] text-muted-foreground/70 mb-2">
            No template declares support for this experience yet.
          </p>
          <div class="grid grid-cols-2 gap-2">
            <button v-for="t in templatesForArchetype" :key="t.id"
              class="p-2 rounded-lg border text-xs font-medium transition-colors text-left"
              :class="selectedTemplate === t.id
                ? 'border-primary bg-primary/10 text-primary'
                : 'border-border hover:border-muted-foreground/40 text-foreground/85'"
              @click="onSelectTemplate(t)"
            >{{ t.name }}</button>
          </div>
          <!-- Basemap (was a control on the old editor map; now chosen here since the preview is an iframe) -->
          <div class="mt-3">
            <label class="text-xs text-muted-foreground block mb-1">Basemap</label>
            <select v-model="basemap"
              class="w-full text-xs bg-background border border-border rounded px-2 py-1.5 focus:outline-none focus:border-primary/60">
              <option v-for="b in basemapCatalog" :key="b.id" :value="b.id">{{ b.name || b.id }}</option>
            </select>
          </div>
        </section>

        <!-- Theme section (R3): colours + light/dark + font, layered over the template -->
        <section class="p-4 border-b border-border/60">
          <h3 class="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Theme</h3>

          <div class="flex items-center justify-between mb-3">
            <span class="text-xs text-muted-foreground">Mode</span>
            <div class="flex gap-1">
              <button v-for="m in [['auto','Auto'],['light','Light'],['dark','Dark']]" :key="m[0]"
                @click="setTheme({ mode: m[0] })"
                class="px-2 py-0.5 rounded border text-xs"
                :class="theme.mode === m[0] ? 'border-primary text-primary bg-primary/10' : 'border-border text-foreground/70'">{{ m[1] }}</button>
            </div>
          </div>

          <label class="text-xs text-muted-foreground block mb-1">Accent colour</label>
          <div class="flex items-center gap-1.5 flex-wrap mb-3">
            <button v-for="c in ACCENT_PRESETS" :key="c" @click="setTheme({ accent: c })"
              class="w-6 h-6 rounded-full border-2 transition-transform hover:scale-110"
              :class="theme.accent === c ? 'border-foreground ring-2 ring-primary/40' : 'border-white/50 dark:border-black/30'"
              :style="{ background: c }" :title="c"></button>
            <label class="relative w-6 h-6 rounded-full border border-dashed border-muted-foreground/50 flex items-center justify-center cursor-pointer overflow-hidden" title="Custom colour">
              <span class="text-[10px] text-muted-foreground">+</span>
              <input type="color" :value="theme.accent || '#2563eb'" @input="e => setTheme({ accent: e.target.value })"
                class="absolute inset-0 opacity-0 cursor-pointer" />
            </label>
            <button v-if="theme.accent" @click="setTheme({ accent: '' })"
              class="text-[11px] text-muted-foreground/70 hover:text-foreground ml-1">reset</button>
          </div>

          <div class="flex items-center justify-between">
            <span class="text-xs text-muted-foreground">Font</span>
            <div class="flex gap-1">
              <button v-for="f in [['sans','Sans'],['serif','Serif']]" :key="f[0]"
                @click="setTheme({ font: f[0] })"
                class="px-2 py-0.5 rounded border text-xs"
                :class="theme.font === f[0] ? 'border-primary text-primary bg-primary/10' : 'border-border text-foreground/70'">{{ f[1] }}</button>
            </div>
          </div>

          <!-- Story panel colour (storymap only) -->
          <div v-if="isStory" class="flex items-center justify-between mt-3">
            <span class="text-xs text-muted-foreground">Story panel colour</span>
            <div class="flex items-center gap-1.5">
              <label class="relative w-6 h-6 rounded border border-dashed border-muted-foreground/50 flex items-center justify-center cursor-pointer overflow-hidden" title="Story panel colour"
                :style="theme.storyBg ? { background: theme.storyBg } : {}">
                <span v-if="!theme.storyBg" class="text-[10px] text-muted-foreground">+</span>
                <input type="color" :value="theme.storyBg || '#0f172a'" @input="e => setTheme({ storyBg: e.target.value })"
                  class="absolute inset-0 opacity-0 cursor-pointer" />
              </label>
              <button v-if="theme.storyBg" @click="setTheme({ storyBg: '' })"
                class="text-[11px] text-muted-foreground/70 hover:text-foreground">reset</button>
            </div>
          </div>

          <!-- Panel transparency. Applied as a colour-mix server-side, NOT CSS opacity, so the map
               shows through the panel while the text stays fully solid. -->
          <div v-if="isStory && theme.storyBg" class="flex items-center justify-between mt-2 gap-2">
            <span class="text-xs text-muted-foreground flex-shrink-0">Panel opacity</span>
            <span class="flex items-center gap-2 min-w-0">
              <input type="range" min="20" max="100" step="5"
                :value="theme.storyOpacity ?? 100"
                @input="e => setTheme({ storyOpacity: Number(e.target.value) })"
                class="w-24 accent-primary" />
              <span class="text-[11px] text-muted-foreground/70 w-8 text-right">{{ theme.storyOpacity ?? 100 }}%</span>
            </span>
          </div>

          <!-- Text colour: a dark panel with the light theme's dark text is unreadable, and the
               visitor's own light/dark toggle can flip it either way. -->
          <div v-if="isStory" class="flex items-center justify-between mt-2">
            <span class="text-xs text-muted-foreground">Story text colour</span>
            <div class="flex items-center gap-1.5">
              <label class="relative w-6 h-6 rounded border border-dashed border-muted-foreground/50 flex items-center justify-center cursor-pointer overflow-hidden" title="Story text colour"
                :style="theme.storyFg ? { background: theme.storyFg } : {}">
                <span v-if="!theme.storyFg" class="text-[10px] text-muted-foreground">+</span>
                <input type="color" :value="theme.storyFg || '#f8fafc'" @input="e => setTheme({ storyFg: e.target.value })"
                  class="absolute inset-0 opacity-0 cursor-pointer" />
              </label>
              <button v-if="theme.storyFg" @click="setTheme({ storyFg: '' })"
                class="text-[11px] text-muted-foreground/70 hover:text-foreground">reset</button>
            </div>
          </div>

          <!-- Header logo / brand -->
          <label class="text-xs text-muted-foreground block mt-3 mb-1">Header logo</label>
          <div class="flex items-center gap-1.5 flex-wrap">
            <button v-for="id in LOGO_PRESET_IDS" :key="id" @click="setLogoPreset(id)" :title="id"
              class="w-8 h-8 rounded border flex items-center justify-center"
              :class="currentLogo().kind === 'preset' && currentLogo().id === id ? 'border-primary text-primary bg-primary/10' : 'border-border text-foreground/70'">
              <svg v-if="id==='layers'" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>
              <svg v-else-if="id==='globe'" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20"/></svg>
              <svg v-else-if="id==='pin'" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s7-6.5 7-12a7 7 0 1 0-14 0c0 5.5 7 12 7 12z"/><circle cx="12" cy="9" r="2.5"/></svg>
              <svg v-else class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/></svg>
            </button>
            <label class="w-8 h-8 rounded border border-dashed border-muted-foreground/50 flex items-center justify-center cursor-pointer text-muted-foreground"
              :class="currentLogo().kind === 'custom' ? 'border-primary text-primary' : ''" title="Upload custom">
              <img v-if="currentLogo().kind === 'custom' && currentLogo().url" :src="currentLogo().url" alt="" class="max-w-full max-h-full object-contain rounded" />
              <span v-else class="text-sm">↑</span>
              <input type="file" accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml" class="hidden" @change="uploadLogo" />
            </label>
            <button @click="setLogoNone" title="No logo"
              class="w-8 h-8 rounded border text-xs"
              :class="currentLogo().kind === 'none' ? 'border-primary text-primary bg-primary/10' : 'border-border text-muted-foreground'">∅</button>
          </div>
          <label v-if="currentLogo().kind === 'custom'" class="flex items-center justify-between text-xs mt-2 cursor-pointer">
            <span class="text-muted-foreground">Tint to theme colour</span>
            <input type="checkbox" :checked="!!currentLogo().tint" @change="e => setLogoTint(e.target.checked)" />
          </label>
          <p class="text-[10px] text-muted-foreground/60 mt-1">
            {{ currentLogo().kind === 'custom' && currentLogo().tint
               ? 'The mark is drawn in the accent colour — its own colours are ignored. Best for a monochrome logo.'
               : 'Recommended: a small square/wide mark, ~30px tall. SVG stays sharp at any size.' }}
          </p>
        </section>

        <!-- Layout section (V-11). The EXPERIENCE picker used to live here, far below Template —
             backwards, now that the template list is filtered by the experience. It moved directly
             above Template so the nested choice reads in the order it is made. -->
        <section class="p-4 border-b border-border/60">
          <h3 class="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Layout</h3>

          <!-- Placement toggles (quick alternative to click-to-place) -->
          <div class="space-y-2 text-xs">
            <div v-if="!isStory && !isCatalog" class="flex items-center justify-between">
              <span class="text-muted-foreground">Layer list side</span>
              <div class="flex gap-1">
                <button v-for="s in ['left','right']" :key="s" @click="setRegionOpt('layerList', { side: s })"
                  class="px-2 py-0.5 rounded border capitalize"
                  :class="resolvedLayout.regions.layerList.side === s ? 'border-primary text-primary bg-primary/10' : 'border-border text-foreground/70'">{{ s }}</button>
              </div>
            </div>
            <div v-if="!isStory && !isCatalog" class="flex items-center justify-between">
              <span class="text-muted-foreground">Layer list</span>
              <div class="flex gap-1">
                <button v-for="m in [['docked','Docked'],['floating','Floating']]" :key="m[0]" @click="setRegionOpt('layerList', { mode: m[0] })"
                  class="px-2 py-0.5 rounded border"
                  :class="resolvedLayout.regions.layerList.mode === m[0] ? 'border-primary text-primary bg-primary/10' : 'border-border text-foreground/70'">{{ m[1] }}</button>
              </div>
            </div>
            <div class="flex items-center justify-between">
              <span class="text-muted-foreground">Controls corner</span>
              <div class="grid grid-cols-2 gap-1" style="width:52px">
                <button v-for="p in ['top-left','top-right','bottom-left','bottom-right']" :key="p"
                  @click="setRegionOpt('controls', { position: p })" :title="p"
                  class="relative w-6 h-6 rounded border"
                  :class="resolvedLayout.regions.controls.position === p ? 'border-primary bg-primary/10' : 'border-border'">
                  <span class="absolute w-1.5 h-1.5 rounded-sm"
                    :class="[resolvedLayout.regions.controls.position === p ? 'bg-primary' : 'bg-muted-foreground/50',
                             p==='top-left'?'top-1 left-1':p==='top-right'?'top-1 right-1':p==='bottom-left'?'bottom-1 left-1':'bottom-1 right-1']"></span>
                </button>
              </div>
            </div>
            <!-- The projection was already SAVED with the pinned view (portal.js reports it with the
                 camera), but only if you happened to switch the preview to the globe first — there was
                 nothing here to tell you that, so the feature was invisible. This drives the preview,
                 and the pinned view follows from it. -->
            <label class="flex items-center justify-between cursor-pointer">
              <span class="text-muted-foreground">Start in 3D globe</span>
              <input type="checkbox" :checked="startsInGlobe" @change="e => setGlobe(e.target.checked)" />
            </label>
            <!-- Same story as the globe: pitch was already part of the pinned view, but the only way
                 to set it was to right-drag the preview, which nothing advertises. -->
            <label class="flex items-center justify-between cursor-pointer">
              <span class="text-muted-foreground">Start tilted</span>
              <input type="checkbox" :checked="startsTilted" @change="e => setTilt(e.target.checked)" />
            </label>
            <label v-if="!isStory && !isCatalog" class="flex items-center justify-between cursor-pointer">
              <span class="text-muted-foreground">Start collapsed</span>
              <input type="checkbox" :checked="resolvedLayout.regions.layerList.collapsed"
                @change="e => setRegionOpt('layerList', { collapsed: e.target.checked })" />
            </label>
          </div>

          <!-- Catalog settings (catalog archetype) -->
          <div v-if="isCatalog" class="mt-3 pt-3 border-t border-border/60 space-y-2 text-xs">
            <span class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Catalog</span>
            <div class="flex items-center justify-between">
              <span class="text-muted-foreground">Map side</span>
              <div class="flex gap-1">
                <button v-for="s in ['left','right']" :key="s" @click="catalogMapSide = s"
                  class="px-2 py-0.5 rounded border capitalize"
                  :class="catalogMapSide === s ? 'border-primary text-primary bg-primary/10' : 'border-border text-foreground/70'">{{ s }}</button>
              </div>
            </div>
            <div class="flex items-center justify-between">
              <span class="text-muted-foreground">Map width</span>
              <div class="flex gap-1">
                <button v-for="w in [[33,'1/3'],[50,'1/2'],[60,'3/5']]" :key="w[0]" @click="catalogMapWidth = w[0]"
                  class="px-2 py-0.5 rounded border"
                  :class="catalogMapWidth === w[0] ? 'border-primary text-primary bg-primary/10' : 'border-border text-foreground/70'">{{ w[1] }}</button>
              </div>
            </div>
            <div class="flex items-center justify-between">
              <span class="text-muted-foreground">Datasets listed</span>
              <div class="flex gap-1">
                <button v-for="o in [['portal','This portal'],['public','All public']]" :key="o[0]"
                  @click="catalogScope = o[0]" class="px-2 py-0.5 rounded border"
                  :class="catalogScope === o[0] ? 'border-primary text-primary bg-primary/10' : 'border-border text-foreground/70'">{{ o[1] }}</button>
              </div>
            </div>
            <p class="text-[11px] text-muted-foreground/70 leading-snug">
              <template v-if="catalogScope === 'public'">
                Lists every <strong>public</strong> layer on this instance, read live — a newly shared layer
                appears without re-publishing. Only the portal’s own layers are drawn on the map.
              </template>
              <template v-else>
                Lists the layers you added above. The visitor can search, filter and toggle them on the map.
              </template>
            </p>
          </div>

          <!-- Dashboard builder (dashboard archetype) -->
          <div v-if="isDashboard" class="mt-3 pt-3 border-t border-border/60">
            <p v-if="!dashboardLayers.length" class="text-[11px] text-muted-foreground/70 mb-2 leading-snug">
              Add layers to this portal first — a widget reads one of them. Rasters power the zonal
              statistics panels; vectors power everything else.
            </p>
            <DashboardBuilder v-model="dashboard" :layers="dashboardLayers" :preset="dashboardPreset" />
          </div>

          <!-- Story sections editor (storymap archetype) -->
          <div v-if="isStory" class="mt-3 pt-3 border-t border-border/60">
            <div class="flex items-center justify-between mb-2">
              <span class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Story sections</span>
              <button @click="addStorySection" class="text-xs text-primary hover:text-primary/80 font-medium">+ Add section</button>
            </div>
            <p v-if="!story.sections.length" class="text-[11px] text-muted-foreground/70 mb-1">
              Position the map + toggle layers, then “Add section” to pin that camera and layer state to a narrative step.
            </p>
            <div v-for="(s, i) in story.sections" :key="s.id" class="mb-2 p-2 rounded-lg border border-border bg-muted/30">
              <div class="flex items-center gap-1 mb-1.5">
                <span class="text-[10px] text-muted-foreground/70 font-mono">#{{ i + 1 }}</span>
                <input v-model="s.title" placeholder="Section title"
                  class="flex-1 min-w-0 text-xs font-medium bg-transparent border-b border-border/60 focus:outline-none focus:border-primary/60 px-1 py-0.5" />
                <button @click="moveStorySection(i, -1)" :disabled="i === 0" title="Move up"
                  class="w-5 h-5 leading-none text-muted-foreground/70 hover:text-foreground disabled:opacity-30">▲</button>
                <button @click="moveStorySection(i, 1)" :disabled="i === story.sections.length - 1" title="Move down"
                  class="w-5 h-5 leading-none text-muted-foreground/70 hover:text-foreground disabled:opacity-30">▼</button>
                <button @click="removeStorySection(i)" title="Remove section"
                  class="w-5 h-5 leading-none text-red-400 hover:text-red-500">✕</button>
              </div>
              <textarea v-model="s.body" rows="3" placeholder="Narrative text for this step…"
                class="w-full text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:border-primary/60 resize-y"></textarea>
              <!-- R4: per-section image -->
              <div class="flex items-center gap-2 mt-1.5">
                <img v-if="s.image" :src="s.image" alt="" class="w-10 h-10 rounded object-cover border border-border flex-shrink-0" />
                <label class="text-[11px] text-primary hover:text-primary/80 font-medium cursor-pointer">
                  {{ s.image ? 'Change image' : '+ Add image' }}
                  <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" class="hidden" @change="e => uploadStoryImage(i, e)" />
                </label>
                <button v-if="s.image" @click="setStoryImage(i, '')" class="text-[11px] text-muted-foreground/70 hover:text-foreground">remove</button>
              </div>
              <div class="flex items-center justify-between mt-1.5">
                <span class="text-[10px] text-muted-foreground/70">
                  {{ s.view && s.view.center ? `Camera z${(s.view.zoom ?? 0).toFixed(1)}${s.view.projection === 'globe' ? ' · globe' : ''}` : 'No camera pinned' }}
                </span>
                <button @click="captureStoryView(i)" class="text-[11px] text-primary hover:text-primary/80 font-medium">
                  ⦿ Capture current map view
                </button>
              </div>
            </div>
            <!-- A second Add, at the END of the list. Sections are written in order, so after
                 filling one in you are already at the bottom — scrolling back to the header to add
                 the next one is the wrong direction of travel. -->
            <button v-if="story.sections.length" @click="addStorySection"
              class="w-full mt-1 py-1.5 rounded-lg border border-dashed border-border text-xs font-medium
                     text-muted-foreground hover:text-primary hover:border-primary/60 transition-colors">
              + Add section
            </button>
          </div>
        </section>

        <!-- Access control section -->
        <section class="p-4">
          <h3 class="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">Access</h3>
          <div ref="accessRoot">
            <button type="button" ref="accessBtn" @click="toggleAccess"
              class="w-full flex items-center gap-2 p-2 rounded-lg border transition-colors text-left"
              :class="accessOpen ? 'border-primary bg-primary/10' : 'border-border hover:border-muted-foreground/40'">
              <component :is="currentAccess.icon" class="w-4 h-4 flex-shrink-0" :class="currentAccess.color" />
              <span class="flex-1 min-w-0">
                <span class="block text-xs font-medium text-foreground/90">{{ currentAccess.label }}</span>
                <span class="block text-[10px] text-muted-foreground/70 leading-snug truncate">{{ currentAccess.desc }}</span>
              </span>
              <svg class="w-3.5 h-3.5 opacity-60 flex-shrink-0 transition-transform" :class="accessOpen ? 'rotate-180' : ''"
                viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
            </button>

            <!-- Teleported to body + fixed so the scrolling sidebar's overflow can't clip it. -->
            <Teleport to="body">
              <div v-if="accessOpen" ref="accessMenu"
                class="fixed z-[60] rounded-lg border border-border bg-card shadow-xl py-1"
                :style="accessMenuStyle">
                <button v-for="opt in accessOptions" :key="opt.value" type="button" @click="chooseAccess(opt.value)"
                  class="w-full flex items-start gap-2 px-3 py-2 text-left hover:bg-muted transition-colors"
                  :class="opt.value === accessType ? 'bg-muted/50' : ''">
                  <component :is="opt.icon" class="w-4 h-4 mt-0.5 flex-shrink-0" :class="opt.color" />
                  <span class="flex-1 min-w-0">
                    <span class="block text-xs font-medium">{{ opt.label }}</span>
                    <span class="block text-[11px] text-muted-foreground leading-snug">{{ opt.desc }}</span>
                  </span>
                  <CheckIcon v-if="opt.value === accessType" class="w-3.5 h-3.5 text-primary mt-0.5 flex-shrink-0" />
                </button>
              </div>
            </Teleport>
          </div>
          <div v-if="accessType === 'password'" class="mt-3">
            <label class="text-xs text-muted-foreground block mb-1">Password</label>
            <input v-model="accessPassword" type="password" placeholder="Set portal password"
              class="w-full text-xs border border-border rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary/60"
            />
          </div>
        </section>

        <!-- About / documentation: shown in the published portal's About panel together with
             each layer's catalog metadata and public data links -->
        <section class="p-4 border-t border-border/60">
          <p class="text-xs font-semibold text-muted-foreground/70 uppercase tracking-wide mb-2">About this portal</p>
          <p v-if="description" class="text-xs text-muted-foreground line-clamp-3 whitespace-pre-line mb-2">{{ description }}</p>
          <p v-else class="text-xs text-muted-foreground/70 italic mb-2">No documentation yet.</p>
          <button type="button" @click="showAboutEditor = true"
            class="w-full text-xs font-medium border border-border hover:border-primary/60 text-foreground/85 rounded px-2 py-1.5">
            {{ description ? 'Edit About page' : 'Write the About page' }}
          </button>
          <p class="text-[10px] text-muted-foreground/70 mt-1">
            Shown to portal visitors, together with each layer's abstract, license and public data
            links (set those via the globe icon in My Data).
          </p>
        </section>

        <!-- About page editor: WYSIWYG (TipTap) — stored as markdown, rendered safely in the portal -->
        <Teleport to="body">
          <div v-if="showAboutEditor"
            class="fixed inset-0 bg-gray-900/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
            <div class="card w-full max-w-3xl p-6 shadow-2xl flex flex-col" style="max-height: 88vh">
              <div class="flex items-center justify-between mb-3">
                <h2 class="text-lg font-semibold">About this portal</h2>
                <button @click="closeAboutEditor"
                  class="text-muted-foreground/70 hover:text-foreground text-xl leading-none">&times;</button>
              </div>
              <!-- Toolbar -->
              <div v-if="aboutEditor" class="flex flex-wrap items-center gap-1 border border-border rounded-t-lg bg-muted/40 px-2 py-1.5">
                <button v-for="btn in toolbarButtons" :key="btn.label" type="button"
                  class="px-2 py-1 rounded text-xs font-semibold transition-colors"
                  :class="btn.active() ? 'bg-primary/15 text-primary' : 'text-muted-foreground hover:bg-muted'"
                  :title="btn.title" @click="btn.run()">
                  <span v-html="btn.label"></span>
                </button>
              </div>
              <!-- Editor -->
              <div class="flex-1 min-h-0 overflow-y-auto border border-t-0 border-border rounded-b-lg"
                style="min-height: 320px; max-height: 52vh">
                <EditorContent :editor="aboutEditor" class="gd-tiptap h-full" />
              </div>
              <!-- Footer stays INSIDE the card: fixed row under the editor -->
              <div class="flex items-center justify-between gap-3 pt-3 mt-3 border-t border-border/60 flex-shrink-0">
                <p class="text-[10px] text-muted-foreground/70">
                  Published as the portal's About page (about.html). Save changes + re-publish to
                  update it.
                </p>
                <button @click="closeAboutEditor" class="btn-secondary text-sm flex-shrink-0">Done</button>
              </div>
              <input ref="aboutImageInput" type="file" accept="image/png,image/jpeg,image/gif,image/webp"
                class="hidden" id="portal-about-image" name="portal-about-image" @change="insertAboutImage" />
            </div>
          </div>
        </Teleport>

      </div>

      <!-- Save footer -->
      <div class="p-4 border-t border-border space-y-2">
        <button @click="save" :disabled="busy" class="btn-secondary w-full justify-center text-sm">
          Save changes
        </button>
        <div v-if="saveMsg" class="flex items-start gap-2 text-xs rounded-lg px-2.5 py-1.5"
          :class="saveMsg.type === 'ok' ? 'text-green-400 bg-green-500/10' : 'text-red-400 bg-red-500/10'">
          <span class="flex-1 min-w-0 leading-snug">{{ saveMsg.text }}</span>
          <button @click="saveMsg = null" title="Dismiss" aria-label="Dismiss"
            class="flex-shrink-0 w-4 h-4 leading-none opacity-60 hover:opacity-100">&times;</button>
        </div>
      </div>
    </div>

    <!-- Live preview (R2): the REAL portal runtime in a same-origin iframe = faithful WYSIWYG. The
         legacy MapLibre editor map (#portal-preview-map) stays mounted invisibly behind it ONLY to keep
         existing bindings valid; the build watch is neutered so it loads no data. -->
    <div class="flex-1 relative bg-muted">
      <div id="portal-preview-map" class="absolute inset-0 w-full h-full opacity-0 pointer-events-none" />

      <iframe v-if="previewUrl" ref="previewFrame" :src="previewUrl" title="Portal preview"
        class="absolute inset-0 w-full h-full border-0 z-10 bg-background" />

      <!-- Preview (re)building -->
      <div v-if="previewBusy"
        class="absolute top-3 left-1/2 -translate-x-1/2 z-20 flex items-center gap-2 bg-card/95 shadow-md border border-border rounded-full px-3.5 py-1.5 text-xs font-medium text-foreground/85 pointer-events-none">
        <span class="inline-block w-3 h-3 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
        Updating preview…
      </div>


      <div v-if="!previewUrl"
        class="absolute inset-0 flex items-center justify-center pointer-events-none z-0">
        <span class="text-xs text-muted-foreground/70 bg-card/80 px-3 py-1.5 rounded-full">Loading preview…</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { useEditor, EditorContent } from '@tiptap/vue-3'
import StarterKit from '@tiptap/starter-kit'
import TipTapLink from '@tiptap/extension-link'
import TipTapImage from '@tiptap/extension-image'
import { Markdown } from 'tiptap-markdown'
import { usePortalsStore } from '@/stores/portals'
import { useDataStore } from '@/stores/data'
import { listTemplates, listBasemaps, getRasterStats, getVectorFeatures, identifyVectorFeatures, uploadPortalAsset, previewPortal, syncSession } from '@/api'
import { useMaplibre } from '@/composables/useMaplibre'
// Aliased on import so every call site in this 2000-line file reads as "the shared symbology",
// not as a local helper someone might be tempted to tweak in place. The Python twin is
// services/symbology.py and the two must stay identical (CLAUDE.md parity rule).
import {
  NO_OUTLINE,
  colorExpression as symColorExpression,
  sizeExpression as symSizeExpression,
  extrusionPaint as symExtrusionPaint,
  isExtruded as symIsExtruded,
  iconImageExpression as symIconImageExpression,
  iconSizeExpression as symIconSizeExpression,
  markerImages as symMarkerImages,
} from '@/lib/symbology'
import { buildMapStyle, lonLatBbox, rasterStyleOf, rasterTilesUrl } from '@/lib/mapStyle'
import { BASEMAPS } from '@/lib/basemaps'
import { registerMarkerImages, setMarkerSpecs } from '@/lib/markerImage'
import { capturePortalThumbnail } from '@/composables/portalThumbnail'
import maplibregl from 'maplibre-gl'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { GeoJsonLayer } from '@deck.gl/layers'
import { ExternalLinkIcon, GlobeIcon, KeyIcon, UsersIcon, UserIcon, CheckIcon } from '@/views/icons'
import LayerTree from '@/components/portal/LayerTree.vue'
import DashboardBuilder from '@/components/portal/DashboardBuilder.vue'

const route = useRoute()
const portalsStore = usePortalsStore()
const dataStore = useDataStore()

const portal = ref(null)
const layerConfigs = ref([])   // flat per-layer STYLE (edited by LayerPanel)
const layerTree = ref([])      // V-13: folder tree over the layers (structure + order)

const _gid = () => 'g' + Math.random().toString(36).slice(2, 9)
const _key = (n) => `${n.layer_type}:${n.layer_id}`
function configForNode(n) {
  return layerConfigs.value.find(c => c.layer_type === n.layer_type && c.layer_id === n.layer_id)
}
function flattenTreeRefs(nodes, out = []) {
  for (const n of nodes) { if (n.children) flattenTreeRefs(n.children, out); else out.push(n) }
  return out
}
// Reconcile a saved tree with the current configs (drop dangling layer nodes, append missing configs
// at root) — mirrors portal_generator._reconcile_layer_tree so editor + published agree.
function reconcileTree(tree, configs) {
  const cfgKeys = new Set(configs.map(_key)), seen = new Set()
  const clean = (nodes) => (nodes || []).reduce((out, n) => {
    if (n.children) out.push({ ...n, children: clean(n.children) })
    else if (cfgKeys.has(_key(n)) && !seen.has(_key(n))) { seen.add(_key(n)); out.push({ layer_type: n.layer_type, layer_id: n.layer_id }) }
    return out
  }, [])
  const cleaned = (tree && tree.length) ? clean(tree) : []
  for (const c of configs) if (!seen.has(_key(c))) { seen.add(_key(c)); cleaned.push({ layer_type: c.layer_type, layer_id: c.layer_id }) }
  return cleaned
}
function _removeFromTree(nodes, node) {
  for (let i = 0; i < nodes.length; i++) {
    if (nodes[i].children) { if (_removeFromTree(nodes[i].children, node)) return true }
    else if (nodes[i].layer_type === node.layer_type && nodes[i].layer_id === node.layer_id) { nodes.splice(i, 1); return true }
  }
  return false
}
function _findGroup(nodes, id) {
  for (const n of nodes) if (n.children) { if (n.id === id) return n; const f = _findGroup(n.children, id); if (f) return f }
  return null
}

// ── V-11 Template Experiences: layout manifest + story ──────────────────────
const layoutConfig = ref({ archetype: 'webmap' })  // {archetype, regions?, panels?} — mirrors Portal.layout_config
const story = ref({ sections: [] })                // {sections:[{id,title,body,view,layers}]} for the storymap archetype
const theme = ref({ mode: 'auto', accent: '', font: 'sans' })  // R3: colour theme baked over the template
const ACCENT_PRESETS = ['#2563eb', '#0ea5e9', '#059669', '#b5502f', '#7c3aed', '#db2777', '#d97706', '#334155']
function setTheme(patch) { theme.value = Object.assign({}, theme.value, patch) }
// Header logo/branding (R3)
const LOGO_PRESET_IDS = ['layers', 'globe', 'pin', 'compass']
function currentLogo() { return theme.value.logo || { kind: 'preset', id: 'layers' } }
function setLogoPreset(id) { setTheme({ logo: { kind: 'preset', id } }) }
function setLogoNone() { setTheme({ logo: { kind: 'none' } }) }
function setLogoTint(on) { setTheme({ logo: Object.assign({}, currentLogo(), { tint: !!on }) }) }
async function uploadLogo(e) {
  const file = e.target.files && e.target.files[0]
  if (!file || !portal.value) { if (e.target) e.target.value = ''; return }
  try {
    const { data } = await uploadPortalAsset(portal.value.id, file)
    // Tint defaults ON for SVG. An SVG logo is nearly always a monochrome mark exported dark on
    // transparent, which is invisible against a dark header — the default that surprises least is
    // the one that shows up. A raster upload is more often a full-colour picture, so it defaults off.
    if (data && data.url) {
      const isSvg = /\.svg($|\?)/i.test(data.url) || file.type === 'image/svg+xml'
      setTheme({ logo: { kind: 'custom', url: data.url, tint: isSvg } })
    }
  } catch (err) { /* ignore */ }
  e.target.value = ''
}

const ARCHETYPES = [
  { id: 'webmap',   name: 'Web map',   desc: 'Map-first with a layer list.' },
  { id: 'storymap', name: 'Story map', desc: 'Scrollytelling — scroll drives the map.' },
  { id: 'catalog',  name: 'Catalog',   desc: 'Browse datasets — searchable list beside a map.' },
  { id: 'dashboard', name: 'Dashboard', desc: 'A grid of widgets that cross-filter each other.' },
]
// PARITY mirror of portal_generator.resolve_layout / portal.js resolveLayout — change all three together.
const _ARCH_DEFAULTS = {
  webmap:   { regions: { layerList: { side: 'left', mode: 'docked', collapsed: true, width: null, x: null, y: null }, controls: { position: 'top-right' }, header: { style: 'bar' } },     panels: { layerCatalog: true,  legend: true, basemap: true, about: true,  story: false } },
  storymap: { regions: { layerList: { side: 'left', mode: 'floating', collapsed: true, width: null, x: null, y: null }, controls: { position: 'top-right' }, header: { style: 'minimal' } }, panels: { layerCatalog: true, legend: true, basemap: true, about: false, story: true } },
  // V-14 catalog: the dataset list is the page and the map is a panel beside it, so layerCatalog is
  // off (the facet rail replaces the switcher) and `catalog` carries the split.
  catalog:  { regions: { layerList: { side: 'right', mode: 'floating', collapsed: true, width: null, x: null, y: null }, controls: { position: 'top-right' }, header: { style: 'bar' }, catalog: { scope: 'portal', mapSide: 'right', mapWidth: 50, railWidth: 20, perPage: 12 } }, panels: { catalog: true, layerCatalog: false, legend: true, basemap: true, about: false, story: false } },
  // V-16 dashboard: the MAP IS A WIDGET — #layout becomes the widget grid and #map-wrap takes the
  // map widget's cell. `layerCatalog` is off by default because the widgets name their own data.
  dashboard: { regions: { layerList: { side: 'left', mode: 'floating', collapsed: true, width: null, x: null, y: null }, controls: { position: 'top-right' }, header: { style: 'bar' }, dashboard: { density: 'comfortable', mapControls: true } }, panels: { dashboard: true, layerCatalog: false, legend: true, basemap: true, about: false, story: false } },
}
// `webmap+catalog` is still UNBUILT and degrades to a working map on purpose. `catalog` used to be
// here too, which is why choosing it silently rendered a plain web map.
const _ARCH_ALIASES = { 'webmap+catalog': 'webmap' }
function resolveLayout(config) {
  let arch = (config && config.archetype) || 'webmap'
  arch = _ARCH_ALIASES[arch] || arch
  if (!_ARCH_DEFAULTS[arch]) arch = 'webmap'
  const base = _ARCH_DEFAULTS[arch]
  const out = { archetype: arch, regions: JSON.parse(JSON.stringify(base.regions)), panels: JSON.parse(JSON.stringify(base.panels)) }
  if (config) for (const g of ['regions', 'panels']) {
    const src = config[g] || {}
    for (const k of Object.keys(src)) {
      if (src[k] && typeof src[k] === 'object' && out[g][k] && typeof out[g][k] === 'object') Object.assign(out[g][k], src[k])
      else out[g][k] = src[k]
    }
  }
  return out
}
const resolvedLayout = computed(() => resolveLayout(layoutConfig.value))
const isStory = computed(() => resolvedLayout.value.archetype === 'storymap')
const isCatalog = computed(() => resolvedLayout.value.archetype === 'catalog')
const isDashboard = computed(() => resolvedLayout.value.archetype === 'dashboard')
// V-16: the widget grid + cross-filter wiring. `null` until the archetype is chosen, which is also
// what tells the server "no dashboard" — the same convention `story` and `theme` use.
const dashboard = ref(null)
// The layers a dashboard widget may bind. Vectors and rasters ONLY (an external WMS has no
// attribute table to summarise and no COG to sample), and the layers this portal actually places —
// binding a widget to a layer the portal does not publish would 404 for every visitor.
const dashboardLayers = computed(() => {
  const out = []
  for (const cfg of layerConfigs.value) {
    if (cfg.layer_type === 'vector') {
      const l = dataStore.vectorLayers.find(v => v.id === cfg.layer_id)
      if (l) out.push({ id: l.id, type: 'vector', name: l.name, columns: l.columns || [],
                        geometry_type: l.geometry_type })
    } else if (cfg.layer_type === 'raster') {
      const l = dataStore.rasterLayers.find(r => r.id === cfg.layer_id)
      if (l) out.push({ id: l.id, type: 'raster', name: l.name, band_count: l.band_count })
    }
  }
  return out
})
// The chosen template's starting widget set, handed to the builder. It seeds an EMPTY grid only;
// everything in it stays removable and re-bindable afterwards (see DashboardBuilder).
const dashboardPreset = computed(() => {
  if (!isDashboard.value) return null
  const t = templates.value.find(x => x.id === selectedTemplate.value)
  return (t && t.dashboard) || null
})
// Which datasets the published catalog lists. 'portal' = this portal's own layers (baked at publish).
// 'public' reads the instance's PUBLIC layers live, so adding a public layer shows up without a
// re-publish — it can never widen past public, because a published portal is browsed anonymously.
const catalogScope = computed({
  get: () => resolvedLayout.value.regions.catalog?.scope || 'portal',
  set: (v) => {
    const c = { ...(layoutConfig.value || {}) }
    c.regions = { ...(c.regions || {}) }
    c.regions.catalog = { ...(c.regions.catalog || {}), scope: v }
    layoutConfig.value = c
  },
})
const catalogMapWidth = computed({
  get: () => resolvedLayout.value.regions.catalog?.mapWidth ?? 50,
  set: (v) => {
    const c = { ...(layoutConfig.value || {}) }
    c.regions = { ...(c.regions || {}) }
    c.regions.catalog = { ...(c.regions.catalog || {}), mapWidth: v }
    layoutConfig.value = c
  },
})
const catalogMapSide = computed({
  get: () => resolvedLayout.value.regions.catalog?.mapSide || 'right',
  set: (v) => {
    const c = { ...(layoutConfig.value || {}) }
    c.regions = { ...(c.regions || {}) }
    c.regions.catalog = { ...(c.regions.catalog || {}), mapSide: v }
    layoutConfig.value = c
  },
})
const sidebarWireWidth = computed(() => '28%')

function pickArchetype(id) {
  layoutConfig.value = { archetype: id }  // reset to the preset defaults; toggles below re-customize
}
function setRegionOpt(regionKey, patch) {
  const c = JSON.parse(JSON.stringify(layoutConfig.value || {}))
  c.regions = c.regions || {}
  c.regions[regionKey] = Object.assign({}, c.regions[regionKey], patch)
  layoutConfig.value = c
}

// Story editor (MVP: title + body + captured camera + layer visibility). Full rich-text + media = V-15.
function captureLayerVis() {
  const m = {}
  for (const c of layerConfigs.value) m[`${c.layer_type}:${c.layer_id}`] = c.visible !== false
  return m
}
function addStorySection() {
  const s = { id: _gid(), title: '', body: '', view: currentView(), layers: captureLayerVis() }
  story.value = { sections: [...(story.value.sections || []), s] }
}
function removeStorySection(i) {
  const arr = (story.value.sections || []).slice(); arr.splice(i, 1)
  story.value = { sections: arr }
}
function moveStorySection(i, dir) {
  const arr = (story.value.sections || []).slice(); const j = i + dir
  if (j < 0 || j >= arr.length) return
  const [x] = arr.splice(i, 1); arr.splice(j, 0, x)
  story.value = { sections: arr }
}
function captureStoryView(i) {
  const arr = (story.value.sections || []).slice()
  if (!arr[i]) return
  arr[i] = { ...arr[i], view: currentView(), layers: captureLayerVis() }
  story.value = { sections: arr }
}
// R4: per-section image (reuses the About-page asset upload — a same-origin URL).
function setStoryImage(i, url) {
  const arr = (story.value.sections || []).slice()
  if (!arr[i]) return
  arr[i] = { ...arr[i], image: url }
  story.value = { sections: arr }
}
// Mirrors the server's limits (routers/portals.py _MAX_ASSET_SIZE / _ASSET_EXTENSIONS) so an image
// that will be rejected is reported INSTANTLY, instead of after uploading megabytes only to be told
// no. The server still enforces them — this is the courtesy, not the gate.
const ASSET_MAX_MB = 15
const ASSET_TYPES = ['.png', '.jpg', '.jpeg', '.gif', '.webp']
const ASSET_MIMES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
function assetProblem(file) {
  const name = (file.name || '').toLowerCase()
  const ext = name.includes('.') ? name.slice(name.lastIndexOf('.')) : ''
  // MIME first, extension only as the fallback: a PASTED screenshot often arrives with no filename
  // at all, and judging it by a missing extension would reject the very case paste exists for.
  const type = (file.type || '').toLowerCase()
  const ok = type ? ASSET_MIMES.includes(type) : ASSET_TYPES.includes(ext)
  if (!ok) {
    return `${ext || type || 'That file'} is not a supported image type. Use ${ASSET_TYPES.join(', ')}.`
  }
  const mb = file.size / 1024 / 1024
  if (mb > ASSET_MAX_MB) {
    return `Image is ${mb.toFixed(1)} MB — the limit is ${ASSET_MAX_MB} MB. Resize it, or export at a lower quality.`
  }
  return null
}

async function uploadStoryImage(i, e) {
  const file = e.target.files && e.target.files[0]
  if (!file || !portal.value) { if (e.target) e.target.value = ''; return }
  const bad = assetProblem(file)
  if (bad) { saveMsg.value = { type: 'err', text: bad }; e.target.value = ''; return }
  try {
    const { data } = await uploadPortalAsset(portal.value.id, file)
    if (data && data.url) setStoryImage(i, data.url)
  } catch (err) {
    // This used to be swallowed silently, so a rejected image looked like "nothing happened" — the
    // server DOES say why (unsupported type, or over 10 MB), we just threw the message away. There is
    // no limit on the NUMBER of images; a failure here is always about the individual file.
    saveMsg.value = { type: 'err', text: err.response?.data?.detail || 'Image upload failed' }
  }
  e.target.value = ''
}
// The choice is NESTED: experience first, then the templates that declare support for it. A template
// is a designed treatment of a particular layout, so offering all of them for every experience let
// you put, say, the narrative serif theme on a catalog it was never drawn for.
const templatesForArchetype = computed(() => {
  const arch = resolvedLayout.value.archetype
  return templates.value.filter(t => (t.archetypes || ['webmap']).includes(arch))
})
const ARCHETYPE_NOUN = { webmap: 'a web map', storymap: 'a story map', catalog: 'a catalog',
                         dashboard: 'a dashboard' }

function onSelectTemplate(t) {
  selectedTemplate.value = t.id
  // Only the LAYOUT details are seeded now — the experience is already chosen above, and re-seeding
  // `archetype` from the template would let picking a theme silently change the layout you selected.
  if (t.layout) layoutConfig.value = Object.assign({}, layoutConfig.value || {}, t.layout)
}

// Changing the experience can strand the current template. Fall back to the first that supports the
// new one rather than leaving a template applied to a layout it does not support.
watch(() => resolvedLayout.value.archetype, () => {
  if (!ready.value) return
  const ok = templatesForArchetype.value
  if (ok.length && !ok.some(t => t.id === selectedTemplate.value)) selectedTemplate.value = ok[0].id
})

const allGroups = computed(() => {
  const out = []
  const walk = (nodes, prefix) => nodes.forEach(n => {
    if (n.children) { const path = prefix ? `${prefix} / ${n.name || 'Folder'}` : (n.name || 'Folder'); out.push({ id: n.id, path }); walk(n.children, path) }
  })
  walk(layerTree.value, '')
  return out
})

function addRootFolder() {
  layerTree.value.unshift({ id: _gid(), name: 'New folder', collapsed: false, exclusive: false, description: '', children: [] })
}
function removeLayerNode(node) {
  _removeFromTree(layerTree.value, node)
  const idx = layerConfigs.value.findIndex(c => c.layer_type === node.layer_type && c.layer_id === node.layer_id)
  if (idx !== -1) layerConfigs.value.splice(idx, 1)
}
function onLayerUpdate({ node, patch }) {
  const idx = layerConfigs.value.findIndex(c => c.layer_type === node.layer_type && c.layer_id === node.layer_id)
  if (idx !== -1) layerConfigs.value[idx] = { ...layerConfigs.value[idx], ...patch }
}
function moveNodeToGroup({ node, groupId }) {
  const detached = { layer_type: node.layer_type, layer_id: node.layer_id }
  _removeFromTree(layerTree.value, node)
  const grp = groupId === 'root' ? null : _findGroup(layerTree.value, groupId)
  ;(grp ? grp.children : layerTree.value).push(detached)
}

// ── V-13 drag & drop — centralized so cross-level moves act on the single tree ──
// LayerTree fires start/over/drop with a node reference + a position; the move is applied here by
// relocating that exact node (identity match) within layerTree. Positions: before | after | into.
const layerFilter = ref('')
let _dragNode = null
const dnd = {
  state: reactive({ draggingKey: null, overKey: null, overPos: null }),
  keyOf(n) { return n.children ? 'g:' + n.id : `${n.layer_type}:${n.layer_id}` },
  start(node, ev) {
    _dragNode = node
    dnd.state.draggingKey = dnd.keyOf(node)
    if (ev?.dataTransfer) { try { ev.dataTransfer.setData('text/plain', ''); ev.dataTransfer.effectAllowed = 'move' } catch (_) {} }
  },
  end() { _dragNode = null; dnd.state.draggingKey = null; dnd.state.overKey = null; dnd.state.overPos = null },
  over(node, pos) {
    if (!_dragNode || _dragNode === node) { dnd.state.overKey = null; return }
    if (_dragNode.children && _isDescendantOrSelf(_dragNode, node)) { dnd.state.overKey = null; return }  // no folder into itself
    dnd.state.overKey = dnd.keyOf(node); dnd.state.overPos = pos
  },
  drop(node, pos) {
    const src = _dragNode
    dnd.end()
    if (!src || src === node) return
    if (src.children && _isDescendantOrSelf(src, node)) return
    _moveNode(src, node, pos)
  },
}
function _isDescendantOrSelf(anc, node) {
  if (anc === node) return true
  return !!anc.children && anc.children.some(c => _isDescendantOrSelf(c, node))
}
function _locate(nodes, node) {
  for (let i = 0; i < nodes.length; i++) {
    if (nodes[i] === node) return { arr: nodes, index: i }
    if (nodes[i].children) { const r = _locate(nodes[i].children, node); if (r) return r }
  }
  return null
}
function _moveNode(src, target, pos) {
  const from = _locate(layerTree.value, src)
  if (!from) return
  from.arr.splice(from.index, 1)
  if (pos === 'into' && target.children) { target.collapsed = false; target.children.push(src); return }
  const to = _locate(layerTree.value, target)   // re-locate AFTER removal (indices may have shifted)
  if (!to) { layerTree.value.push(src); return }
  to.arr.splice(pos === 'after' ? to.index + 1 : to.index, 0, src)
}

// ── Collapse / expand every folder ──
function setAllCollapsed(v) {
  const walk = (nodes) => nodes.forEach(n => { if (n.children) { n.collapsed = v; walk(n.children) } })
  walk(layerTree.value)
}
const hasGroups = computed(() => allGroups.value.length > 0)
const selectedTemplate = ref('minimal')
const templates = ref([])
const showAddLayer = ref(false)
const lastAddedKey = ref(null)
const accessType = ref('public')
const basemap = ref(null)  // chosen basemap catalog id; null → first catalog entry (see basemapCatalog)
// Basemap catalog. The single source of truth is the server (GET /api/basemaps → the API's
// BASEMAP_CATALOG); it's fetched in onMounted and REPLACES this list, so adding a basemap is a
// one-place change on the server. The inline list is only an instant bootstrap/offline fallback so
// the preview never flashes blank before the fetch resolves. (Declared here — above the watches
// that reference it at setup time — to avoid a temporal-dead-zone error.)
const basemapCatalog = ref(BASEMAPS)

// Drag-to-reorder layers (top of list = top of map)
const dragIndex = ref(null)
function onDragStart(i) { dragIndex.value = i }
function onDragOver(i) {
  if (dragIndex.value === null || dragIndex.value === i) return
  const arr = layerConfigs.value
  const [moved] = arr.splice(dragIndex.value, 1)
  arr.splice(i, 0, moved)
  dragIndex.value = i
}
function onDragEnd() { dragIndex.value = null }
const accessPassword = ref('')
const busy = ref(false)
const saveMsg = ref(null)
// Every producer just assigns to saveMsg, so the dismissal lives HERE rather than at a dozen call
// sites — a rejected image used to leave a red line with no way to clear it short of saving again.
// Errors linger longer than confirmations because they carry something to act on.
let saveMsgTimer = null
watch(saveMsg, (m) => {
  clearTimeout(saveMsgTimer)
  if (!m) return
  saveMsgTimer = setTimeout(() => { saveMsg.value = null }, m.type === 'ok' ? 3000 : 10000)
})

// ── Rename the portal (click the title) ─────────────────────────────────────
// The slug (and therefore the public /portals/{slug}/ URL) is fixed at creation and never changes,
// so a rename only updates the display title. The editor route uses the portal id, unaffected either way.
const renaming = ref(false)
const renameTitle = ref('')
const renameInput = ref(null)
function startRename() {
  if (!portal.value) return
  renameTitle.value = portal.value.title
  renaming.value = true
  nextTick(() => { renameInput.value?.focus(); renameInput.value?.select() })
}
function cancelRename() { renaming.value = false }
async function commitRename() {
  if (!renaming.value) return           // guard against blur firing after Enter already committed
  const name = renameTitle.value.trim()
  renaming.value = false
  if (!name || name === portal.value.title) return
  busy.value = true
  try {
    const updated = await portalsStore.update(portal.value.id, { title: name })
    portal.value = updated
    saveMsg.value = { type: 'ok', text: 'Renamed' }
  } catch (err) {
    saveMsg.value = { type: 'err', text: err.response?.data?.detail || err.message }
  } finally {
    busy.value = false
  }
}
const description = ref('')  // About-panel documentation (markdown), baked at publish
const showAboutEditor = ref(false)

// WYSIWYG About editor (TipTap) — the document is STORED as markdown (tiptap-markdown), so the
// published portal keeps rendering through its safe escape-first mini-markdown (portal.js
// mdToHtml) and no HTML sanitizer is needed anywhere.
const aboutEditor = useEditor({
  extensions: [
    StarterKit.configure({ heading: { levels: [1, 2, 3] } }),
    TipTapLink.configure({ openOnClick: false }),
    TipTapImage,
    Markdown.configure({ html: false, linkify: true }),
  ],
  content: '',
  onUpdate: ({ editor }) => { description.value = editor.storage.markdown.getMarkdown() },
  editorProps: {
    // Paste or drag an image straight in — screenshots and images copied from another page are how
    // people actually add pictures, and "browse for a file" makes them save to disk first.
    handlePaste: (view, event) => consumeImages(Array.from(event.clipboardData?.items || [])
      .filter(i => i.kind === 'file' && i.type.startsWith('image/')).map(i => i.getAsFile()), event),
    handleDrop: (view, event) => consumeImages(
      Array.from(event.dataTransfer?.files || []).filter(f => f.type.startsWith('image/')), event),
  },
})

// Shared by paste and drop. Returns true only when images were actually taken, so ProseMirror still
// handles ordinary text/HTML pastes normally — swallowing those would break copying formatted text in.
function consumeImages(files, event) {
  const imgs = (files || []).filter(Boolean)
  if (!imgs.length || !portal.value) return false
  event.preventDefault()
  uploadAboutImages(imgs)
  return true
}

async function uploadAboutImages(files) {
  const bad = files.map(assetProblem).find(Boolean)
  if (bad) { saveMsg.value = { type: 'err', text: bad }; return }
  uploadingImage.value = true
  try {
    // Sequential, not Promise.all: each upload inserts at the cursor, and parallel completions would
    // land in whatever order they finished rather than the order they were pasted.
    for (const file of files) {
      const { data } = await uploadPortalAsset(portal.value.id, file)
      const alt = (file.name || 'image').replace(/\.\w+$/, '')
      aboutEditor.value.chain().focus().setImage({ src: data.url, alt }).run()
    }
  } catch (err) {
    saveMsg.value = { type: 'err', text: err.response?.data?.detail || 'Image upload failed' }
  } finally { uploadingImage.value = false }
}

// Image embedding: pick a file → upload to the portal's asset store → insert the public URL.
const aboutImageInput = ref(null)
const uploadingImage = ref(false)
async function insertAboutImage(e) {
  const file = e.target.files && e.target.files[0]
  e.target.value = ''
  if (!file || !portal.value) return
  await uploadAboutImages([file])
}

// Load the saved markdown whenever the modal opens (the portal may load after editor setup).
watch(showAboutEditor, (open) => {
  if (open && aboutEditor.value) {
    aboutEditor.value.commands.setContent(description.value || '')
  }
})

function closeAboutEditor() {
  if (aboutEditor.value) description.value = aboutEditor.value.storage.markdown.getMarkdown()
  showAboutEditor.value = false
}

function promptLink() {
  const prev = aboutEditor.value?.getAttributes('link')?.href || ''
  const url = window.prompt('Link URL', prev)
  if (url === null) return
  if (!url) aboutEditor.value.chain().focus().unsetLink().run()
  else aboutEditor.value.chain().focus().setLink({ href: url }).run()
}

const toolbarButtons = [
  { label: 'H2', title: 'Section heading', run: () => aboutEditor.value.chain().focus().toggleHeading({ level: 2 }).run(), active: () => aboutEditor.value?.isActive('heading', { level: 2 }) },
  { label: 'H3', title: 'Subheading', run: () => aboutEditor.value.chain().focus().toggleHeading({ level: 3 }).run(), active: () => aboutEditor.value?.isActive('heading', { level: 3 }) },
  { label: '<b>B</b>', title: 'Bold', run: () => aboutEditor.value.chain().focus().toggleBold().run(), active: () => aboutEditor.value?.isActive('bold') },
  { label: '<i>I</i>', title: 'Italic', run: () => aboutEditor.value.chain().focus().toggleItalic().run(), active: () => aboutEditor.value?.isActive('italic') },
  { label: '• List', title: 'Bullet list', run: () => aboutEditor.value.chain().focus().toggleBulletList().run(), active: () => aboutEditor.value?.isActive('bulletList') },
  { label: '1. List', title: 'Numbered list', run: () => aboutEditor.value.chain().focus().toggleOrderedList().run(), active: () => aboutEditor.value?.isActive('orderedList') },
  { label: '&ldquo;&rdquo;', title: 'Quote', run: () => aboutEditor.value.chain().focus().toggleBlockquote().run(), active: () => aboutEditor.value?.isActive('blockquote') },
  { label: '&lt;/&gt;', title: 'Inline code', run: () => aboutEditor.value.chain().focus().toggleCode().run(), active: () => aboutEditor.value?.isActive('code') },
  { label: '🔗', title: 'Link', run: promptLink, active: () => aboutEditor.value?.isActive('link') },
  { label: '🖼', title: 'Insert image', run: () => aboutImageInput.value && aboutImageInput.value.click(), active: () => false },
]

const accessOptions = [
  { value: 'public',       label: 'Public',       icon: GlobeIcon, color: 'text-emerald-400', desc: 'Anyone with the URL can view' },
  { value: 'password',     label: 'Password',     icon: KeyIcon,   color: 'text-violet-400',  desc: 'Require a password to view' },
  { value: 'organization', label: 'Organization', icon: UsersIcon, color: 'text-sky-400',     desc: 'Only signed-in members of your workspace can view' },
  { value: 'owner',        label: 'Private',      icon: UserIcon,  color: 'text-amber-400',   desc: 'Only you (the creator) and workspace admins can view' },
]
const currentAccess = computed(() => accessOptions.find(o => o.value === accessType.value) || accessOptions[0])

// Access dropdown (icon picker, teleported + fixed so the scrolling sidebar can't clip it).
const ACCESS_MENU_W = 256  // w-64
const accessOpen = ref(false)
const accessRoot = ref(null)
const accessBtn = ref(null)
const accessMenu = ref(null)
const accessMenuStyle = ref({})

function placeAccessMenu() {
  const r = accessBtn.value?.getBoundingClientRect()
  if (!r) return
  const left = Math.max(8, Math.min(r.left, window.innerWidth - ACCESS_MENU_W - 8))
  accessMenuStyle.value = { top: `${r.bottom + 4}px`, left: `${left}px`, width: `${r.width}px` }
}
function toggleAccess() { accessOpen.value ? closeAccess() : openAccess() }
function openAccess() {
  accessOpen.value = true
  nextTick(placeAccessMenu)
  document.addEventListener('click', onAccessDocClick, true)
  window.addEventListener('scroll', closeAccess, true)
  window.addEventListener('resize', closeAccess, true)
}
function closeAccess() {
  if (!accessOpen.value) return
  accessOpen.value = false
  document.removeEventListener('click', onAccessDocClick, true)
  window.removeEventListener('scroll', closeAccess, true)
  window.removeEventListener('resize', closeAccess, true)
}
function chooseAccess(v) {
  closeAccess()
  accessType.value = v
}
function onAccessDocClick(e) {
  if (accessRoot.value?.contains(e.target) || accessMenu.value?.contains(e.target)) return
  closeAccess()
}
onBeforeUnmount(closeAccess)

// Mount BLANK (no basemap) so the preview never shows the OSM default before the chosen basemap —
// the first paint (gated on `ready` below) is already the chosen basemap + layers.
const { map, loaded, applyStyle, fitToBbox, jumpTo, addTopRightControlFirst } =
  useMaplibre('portal-preview-map', { version: 8, sources: {}, layers: [] })

// Admin-pinned view (center/zoom/bearing/pitch/projection) for the published portal;
// null = fit to all layers.
const savedView = ref(null)
// Gates the first preview build until the portal + its data + the basemap catalog are all loaded, so
// the map paints ONCE (chosen basemap + layers) instead of flashing through several applyStyle calls.
const ready = ref(false)

// R2: faithful iframe preview (the REAL portal runtime) + click-to-place.
const previewFrame = ref(null)
const previewUrl = ref('')       // /portals/_preview/{id}/?edit=1&t=… (cache-busted per rebuild)
const previewBusy = ref(false)
const lastView = ref(null)       // camera reported by the iframe on moveend → used for save/story

onMounted(async () => {
  // Load the portal, its data, AND the basemap catalog (single source of truth — replaces the inline
  // bootstrap list) BEFORE flipping `ready`, so the first preview build already knows the chosen
  // basemap's final tiles. Catalog fetch is non-fatal — the bootstrap list keeps the picker working.
  await Promise.all([
    portalsStore.refresh(),
    dataStore.refresh(),
    listBasemaps().then(({ data }) => {
      if (Array.isArray(data) && data.length) basemapCatalog.value = data
    }).catch(() => { /* keep the bootstrap fallback */ }),
    // R2: ensure the gd_session cookie exists so the same-origin preview iframe passes the nginx gate.
    syncSession().catch(() => { /* best-effort */ }),
  ])
  portal.value = portalsStore.portals.find(p => p.id === parseInt(route.params.id))
  if (portal.value) {
    layerConfigs.value = portal.value.layer_configs || []
    // V-13: build the folder tree from the saved groups (reconciled against configs), else a flat tree.
    layerTree.value = reconcileTree(portal.value.layer_groups, layerConfigs.value)
    selectedTemplate.value = portal.value.template_id
    // Legacy 'private' == organization (members-only) — show it as the Organization option.
    accessType.value = portal.value.access_type === 'private'
      ? 'organization'
      : (portal.value.access_type || 'public')
    basemap.value = portal.value.basemap || basemapCatalog.value[0].id
    savedView.value = portal.value.initial_view || null
    description.value = portal.value.description || ''
    // V-11: layout manifest + story sections (null → webmap default / empty story)
    layoutConfig.value = portal.value.layout_config || { archetype: 'webmap' }
    story.value = portal.value.story && Array.isArray(portal.value.story.sections)
      ? portal.value.story : { sections: [] }
    theme.value = Object.assign({ mode: 'auto', accent: '', font: 'sans' }, portal.value.theme || {})
    // V-16: the saved widget grid (null → the builder seeds from the template preset, once).
    dashboard.value = portal.value.dashboard && Array.isArray(portal.value.dashboard.widgets)
      ? portal.value.dashboard : null
  }
  ready.value = true  // inputs set → the watcher may now build the preview (once, on the chosen basemap)
  const { data } = await listTemplates()
  templates.value = data
})

// ── R2: iframe preview + click-to-place ─────────────────────────────────────
let previewTimer = null
function schedulePreview() {
  // A rebuild is a server round-trip AND a full iframe reload, so the window has to outlast normal
  // typing/dragging — 350ms fired between words and between slider nudges. Anything that should feel
  // instant (theme) is applied live by postMessage instead of coming through here.
  clearTimeout(previewTimer)
  previewTimer = setTimeout(refreshPreview, 900)
}
async function refreshPreview() {
  if (!portal.value) return
  previewBusy.value = true
  try {
    const payload = {
      title: portal.value.title,
      description: description.value,
      template_id: selectedTemplate.value,
      layer_configs: layerConfigs.value,
      layer_groups: layerTree.value,
      layout_config: layoutConfig.value,
      story: story.value,
      theme: theme.value,
      dashboard: dashboard.value || {},   // {} clears — a preview must be able to show an empty grid
      initial_view: currentView() || savedView.value || undefined,
      basemap: basemap.value || (basemapCatalog.value[0] && basemapCatalog.value[0].id),
    }
    const { data } = await previewPortal(portal.value.id, payload)
    previewUrl.value = `${data.url}?edit=1&t=${Date.now()}`  // cache-bust → the iframe reloads
  } catch (e) {
    /* keep the previous preview on error */
  } finally {
    previewBusy.value = false
  }
}
function postToFrame(msg) {
  const w = previewFrame.value && previewFrame.value.contentWindow
  if (!w) return
  // Strip Vue reactivity: a reactive Proxy array (e.g. a layer's bbox) fails structured-clone, so the
  // postMessage throws and the action silently no-ops (that was the zoom-to-layer bug). JSON round-trip
  // yields plain data. zoom-to-group worked only because it built a fresh plain array.
  let payload
  try { payload = JSON.parse(JSON.stringify(Object.assign({ gd: 1 }, msg))) } catch (e) { return }
  try { w.postMessage(payload, location.origin) } catch (e) { /* ignore */ }
}
function onFrameMessage(e) {
  if (e.origin !== location.origin || !e.data || e.data.gd == null) return
  const d = e.data
  // 'snapshot' replies are handled by composables/portalThumbnail.js, which owns its own frame and
  // its own listener — this editor no longer requests one.
  if (d.type === 'view' && d.view) lastView.value = d.view
}
onMounted(() => window.addEventListener('message', onFrameMessage))
onBeforeUnmount(() => { window.removeEventListener('message', onFrameMessage); clearTimeout(previewTimer) })

// Rebuild the iframe preview whenever config that shapes the published MAP changes. Two deliberate
// exclusions:
//   THEME — applies LIVE via postMessage (below), so colour/mode/font tweaks never reload the iframe.
//   DESCRIPTION — it only feeds the About PAGE, which the map preview does not render. It used to be
//   watched, so every pause while typing in the About editor triggered a full server-side bundle
//   rebuild and an iframe reload, to preview text you could not see in the preview. That was the
//   "re-renders on every letter" problem. It is still baked on save/publish.
// `dashboard` is in the list because a dashboard IS the published page — a widget added, moved or
// re-bound changes what the preview shows, and unlike the theme there is no live channel for it
// (the widgets are built by the runtime from the baked config, not restyled by a CSS variable).
watch([layoutConfig, story, dashboard, layerConfigs, layerTree, basemap, selectedTemplate, ready],
  () => { if (ready.value) schedulePreview() }, { deep: true })

// B: theme changes apply live in the preview (no reload). The theme is still baked on the next full
// rebuild + on save/publish, so live and published stay consistent.
let themeTimer = null
watch(theme, () => {
  if (!ready.value) return
  clearTimeout(themeTimer)
  themeTimer = setTimeout(() => postToFrame({ type: 'theme', theme: theme.value }), 100)
}, { deep: true })

// ── deck.gl overlay for GeoParquet layers ───────────────────────────────────
// GeoParquet layers are too big for a MapLibre geojson source, so they render in a deck.gl
// MapboxOverlay (added as a control → survives setStyle) fed by the viewport query
// (getVectorFeatures → covering-column-pruned GeoJSON). Refetched on pan/zoom (moveend) and when a
// new layer first appears; pure style edits rebuild from cached data without a network refetch.
let deckOverlay = null
let basemapControl = null
const deckData = {}        // layer_id → cached FeatureCollection for the current view
const deckFetched = {}     // layer_id → { bbox:[w,s,e,n], band } region already loaded (see below)
const deckLoading = ref(0) // detail fetches in flight → shows the "Loading features…" pill
// In-flight viewport fetches are ABORTED when a newer view supersedes them (and before Save/Publish)
// so rapid pans over a heavy GeoParquet can't pile up requests and saturate the browser's ~6
// per-host connection limit — which otherwise starves the Save/Publish request and made it "hang".
let deckAbort = null
function abortDeckFetches() { if (deckAbort) { deckAbort.abort(); deckAbort = null } }
// Incremental viewport loading (mirrors portal.js fetchDeck): fetch a BUFFERED bbox (bigger than the
// screen) and skip refetching while the viewport stays inside the region already loaded at this zoom,
// so panning doesn't reload data already on screen and returning to a loaded area is instant. The row
// limit is scaled to the buffer's area so on-screen density is preserved.
const DECK_FETCH_PAD = 0.35
const DECK_PAD_AREA = (1 + 2 * DECK_FETCH_PAD) ** 2
const DECK_FETCH_MAX = 150000
const bboxContains = (o, i) => !!o && i[0] >= o[0] && i[1] >= o[1] && i[2] <= o[2] && i[3] <= o[3]
const padBbox = (b, f) => { const dx = (b[2] - b[0]) * f, dy = (b[3] - b[1]) * f; return [b[0] - dx, b[1] - dy, b[2] + dx, b[3] + dy] }
// Per-viewport feature cap, scaled by zoom (matches portal.js): a zoomed-out view is a capped
// subset either way, and a flat 50k limit made low-zoom responses tens of MB (slow query,
// slow JSON parse). More than the eye resolves at each band.
function deckLimit() {
  const z = map.value ? map.value.getZoom() : 10
  return z < 7 ? 10000 : z < 10 ? 25000 : 50000
}

// ── detail/overview switch (mirrors templates/shared/portal.js — keep in sync) ──────────────
// A HEAVY prepped layer whose viewport spans more than DECK_MAX_FILES partition files renders as
// a density-shaded partition-grid overview built from the layer's manifest (per-cell counts —
// instant, zero data reads) instead of per-feature detail; zooming in loads real features.
// LIGHT layers (total features ≤ DECK_DETAIL_MAX) always show full detail at every zoom.
const DECK_MAX_FILES = 16  // keep equal to portal.js WASM_MAX_FILES (same switch moment)
const DECK_DETAIL_MAX = 50000
const DECK_DETAIL_MAX_ROWS = 400000  // keep equal to portal.js DETAIL_MAX_ROWS
const deckManifests = {}   // layer_id → manifest object | 'none'

async function deckManifest(id) {
  if (deckManifests[id] !== undefined) return deckManifests[id]
  try {
    const r = await fetch(`/api/data/vector/${id}/parquet/manifest.json`)
    const m = r.ok ? await r.json() : null
    deckManifests[id] = (m && m.grid && m.cells && (!m.crs || m.crs === 'EPSG:4326')) ? m : 'none'
  } catch { deckManifests[id] = 'none' }
  return deckManifests[id]
}

// Same grid math as the server/portal.js: cell = ix*grid + iy, +1-cell pad for the FILE list.
// Rows are weighted by the fraction of each cell the viewport covers (whole-cell sums locked
// dense regions in overview mode forever — the pad alone spans ≥9 cells at any deep zoom).
// Keep in sync with portal.js viewportLoad.
function deckViewportLoad(m, b) {
  const g = m.grid, gsz = g.grid | 0, pad = 1, dx = g.spanx / gsz, dy = g.spany / gsz
  const ci = (v, lo, span) => Math.floor((v - lo) / (span || 1.0) * gsz)
  const ix0 = Math.max(0, ci(b[0], g.minx, g.spanx) - pad)
  const ix1 = Math.min(gsz - 1, ci(b[2], g.minx, g.spanx) + pad)
  const iy0 = Math.max(0, ci(b[1], g.miny, g.spany) - pad)
  const iy1 = Math.min(gsz - 1, ci(b[3], g.miny, g.spany) + pad)
  let files = 0, rows = 0
  if (ix0 <= ix1 && iy0 <= iy1)
    for (let ix = ix0; ix <= ix1; ix++)
      for (let iy = iy0; iy <= iy1; iy++) {
        const list = m.cells[String(ix * gsz + iy)] || []
        if (!list.length) continue
        const x0 = g.minx + ix * dx, y0 = g.miny + iy * dy
        const ox = Math.max(0, Math.min(b[2], x0 + dx) - Math.max(b[0], x0))
        const oy = Math.max(0, Math.min(b[3], y0 + dy) - Math.max(b[1], y0))
        const frac = Math.min(1, (ox * oy) / (dx * dy || 1))
        for (const f of list) { files += 1; rows += (f.rows || 0) * frac }
      }
  return { files, rows }
}

function deckOverviewGeojson(m) {
  if (m.__overviewFc) return m.__overviewFc
  const g = m.grid, gsz = g.grid | 0, dx = g.spanx / gsz, dy = g.spany / gsz
  let max = 0
  const counts = {}
  for (const k of Object.keys(m.cells)) {
    counts[k] = (m.cells[k] || []).reduce((a, f) => a + (f.rows || 0), 0)
    if (counts[k] > max) max = counts[k]
  }
  const features = Object.keys(m.cells).map(k => {
    const c = +k, ix = Math.floor(c / gsz), iy = c % gsz
    const x0 = g.minx + ix * dx, y0 = g.miny + iy * dy
    return {
      type: 'Feature',
      properties: { count: counts[k], density: max ? Math.sqrt(counts[k] / max) : 0 },
      geometry: { type: 'Polygon', coordinates: [[[x0, y0], [x0 + dx, y0], [x0 + dx, y0 + dy], [x0, y0 + dy], [x0, y0]]] },
    }
  })
  const fc = { type: 'FeatureCollection', features }
  fc.__overview = true
  m.__overviewFc = fc
  return fc
}

function hexToRgb(hex) {
  const h = String(hex || '#3b82f6').replace('#', '')
  const f = h.length === 3 ? h.split('').map(c => c + c).join('') : h
  const n = parseInt(f, 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

// Visible GeoParquet layer configs that the deck overlay (not MapLibre) is responsible for.
// A layer that was explicitly tiled (ready PMTiles) uses the pmtiles:// fallback in the style instead.
function deckConfigs() {
  return [...layerConfigs.value].filter(cfg => {
    if (cfg.visible === false || cfg.layer_type !== 'vector') return false
    const layer = dataStore.vectorLayers.find(l => l.id === cfg.layer_id)
    return layer && layer.status === 'ready' && layer.storage_backend === 'geoparquet' &&
      !(layer.tile_status === 'ready' && layer.pmtiles_key)
  })
}

function makeDeckLayer(cfg) {
  const data = deckData[cfg.layer_id]
  if (!data) return null
  const layer = dataStore.vectorLayers.find(l => l.id === cfg.layer_id)
  const geom = (layer?.geometry_type || '').toLowerCase()
  const rgb = hexToRgb(cfg.style?.color || '#3b82f6')
  const opacity = cfg.opacity ?? 1.0
  const outline = hexToRgb(_safeOutline(cfg.style?.outline_color))
  const isPoly = geom.includes('polygon'), isLine = geom.includes('line')
  if (data.__overview) {
    // Large-scale representation: partition grid shaded by feature density (see portal.js twin).
    return new GeoJsonLayer({
      id: `deck_${cfg.layer_id}`,
      data,
      pickable: false,
      filled: true,
      stroked: true,
      getFillColor: f => [...rgb, Math.round(200 * opacity * f.properties.density)],
      getLineColor: [...rgb, Math.round(60 * opacity)],
      lineWidthUnits: 'pixels',
      getLineWidth: 0.5,
    })
  }
  // 3D for a deck-rendered POLYGON layer. GeoParquet layers emit no MapLibre layer, so
  // `fill-extrusion` never reaches them — deck extrudes them directly. Mirrors
  // templates/shared/portal.js::makeDeckLayer; if these disagree the preview shows a style the
  // portal will not render. Points are excluded (deck extrudes polygons; a pillar needs the
  // geometry buffered, which only the PostGIS tile path does).
  const ex = cfg.style?.extrusion || {}
  const extruded = isPoly && !!ex.enabled && !!ex.field
  // "none" is a sentinel, not a colour — hex-parsing it yields NaN, which deck renders as BLACK.
  const noOutline = cfg.style?.outline_color === NO_OUTLINE
  const exScale = Number(ex.scale) || 1
  return new GeoJsonLayer({
    id: `deck_${cfg.layer_id}`,
    data,
    pickable: false,
    filled: !isLine,
    // Lines ARE their stroke, so noOutline must not erase them — it is a POLYGON outline setting.
    stroked: isLine || (!extruded && !(isPoly && noOutline)),
    extruded,
    // Non-numeric or missing → 0, not NaN: NaN propagates into the mesh and drops the whole layer.
    getElevation: extruded
      ? (f) => { const v = Number((f.properties || {})[ex.field]); return (isFinite(v) ? v : 0) * exScale }
      : 0,
    getFillColor: [...rgb, Math.round(255 * opacity * (isPoly ? (cfg.style?.fill_opacity ?? 0.45) : 1))],
    getLineColor: isPoly ? [...outline, Math.round(255 * opacity)] : [...rgb, Math.round(255 * opacity)],
    lineWidthUnits: 'pixels',
    getLineWidth: cfg.style?.line_width ?? (isLine ? 2 : 1),
    lineWidthMinPixels: isLine ? (cfg.style?.line_width ?? 2) : 1,
    pointType: 'circle',
    pointRadiusUnits: 'pixels',
    getPointRadius: cfg.style?.radius ?? 5,
    pointRadiusMinPixels: 2,
  })
}

// "none" is a sentinel, not a colour: hexToRgb would return NaN components and deck renders those
// as BLACK — the most visible outline available, for the setting that asks for none. `stroked` is
// what actually removes it; this only guarantees no NaN ever reaches the GPU.
function _safeOutline(v) { return (!v || v === NO_OUTLINE) ? '#1d4ed8' : v }

// 3D-Z: transform Z (scale·z+offset) so deck.gl's GeoJsonLayer draws at altitude (mirror portal.js).
function transformElevationGeojson(geojson, elev) {
  const scale = Number.isFinite(elev?.vertical_scale) ? elev.vertical_scale : 1
  const offset = Number.isFinite(elev?.offset) ? elev.offset : 0
  const tz = c => (Array.isArray(c) && c.length && typeof c[0] === 'number')
    ? [c[0], c[1], (typeof c[2] === 'number' && Number.isFinite(c[2]) ? c[2] : 0) * scale + offset]
    : (Array.isArray(c) ? c.map(tz) : c)
  return { type: 'FeatureCollection', features: ((geojson?.features) || []).map(f => {
    const g = f?.geometry
    return g ? { ...f, geometry: { ...g, coordinates: tz(g.coordinates) } } : f
  }) }
}

function makeElevationDeckLayer(cfg, idx) {
  if (!cfg.geojson) return null
  const data = transformElevationGeojson(cfg.geojson, cfg.elevation)
  const geom = (cfg.geometry || 'line').toLowerCase()
  const isPoly = geom.includes('polygon'), isLine = geom.includes('line')
  const rgb = hexToRgb(cfg.style?.color || '#3b82f6')
  const outline = hexToRgb(_safeOutline(cfg.style?.outline_color))
  const opacity = cfg.opacity ?? 1.0
  return new GeoJsonLayer({
    id: `deck_elev_${idx}`, data, pickable: false, filled: !isLine, stroked: true,
    getFillColor: [...rgb, Math.round(255 * opacity * (isPoly ? (cfg.style?.fill_opacity ?? 0.45) : 1))],
    getLineColor: isPoly ? [...outline, Math.round(255 * opacity)] : [...rgb, Math.round(255 * opacity)],
    lineWidthUnits: 'pixels',
    getLineWidth: cfg.style?.line_width ?? (isLine ? 2 : 1),
    lineWidthMinPixels: isLine ? (cfg.style?.line_width ?? 2) : 1,
    pointType: 'circle', pointRadiusUnits: 'pixels',
    getPointRadius: cfg.style?.radius ?? 5, pointRadiusMinPixels: 2,
  })
}

async function refreshDeck(refetch) {
  if (!deckOverlay || !map.value) return
  const configs = deckConfigs()
  if (refetch || configs.some(c => !deckData[c.layer_id])) {
    const b = map.value.getBounds()
    const nb = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    const band = Math.round(map.value.getZoom())
    // Supersede any still-running fetch from a previous view before starting this one.
    abortDeckFetches()
    deckAbort = new AbortController()
    const signal = deckAbort.signal
    await Promise.all(configs.map(async cfg => {
      if (!refetch && deckData[cfg.layer_id]) return
      // Already loaded a buffered region covering this viewport at this zoom → nothing to fetch.
      const cached = deckFetched[cfg.layer_id]
      if (refetch && deckData[cfg.layer_id] && cached && cached.band === band && bboxContains(cached.bbox, nb)) return
      // Fetch a BUFFERED bbox (bigger than the screen) so nearby pans stay within it; decide
      // overview-vs-detail on this SAME padded bbox so we only load detail when the area-capped
      // fetch would be reasonably complete (matches portal.js fetchDeckLayer).
      const fb = padBbox(nb, DECK_FETCH_PAD)
      const lim = Math.min(DECK_FETCH_MAX, Math.round(deckLimit() * DECK_PAD_AREA))
      try {
        // Heavy prepped layer at large scale → density-grid overview from the manifest
        // (instant, no feature query). Light layers and zoomed-in views load real features.
        const m = await deckManifest(cfg.layer_id)
        if (m !== 'none' && (m.feature_count || 0) > DECK_DETAIL_MAX) {
          const load = deckViewportLoad(m, fb)
          // Gate on ROWS only. The editor fetches detail from the SERVER in one request
          // (getVectorFeatures), so the partition-FILE count is irrelevant — gating on it (like
          // portal.js's disabled wasm path did) locked dense city cells, split into many files,
          // into the overview at every zoom (mirrors portal.js fitsDetail).
          if (load.rows > DECK_DETAIL_MAX_ROWS) {
            deckData[cfg.layer_id] = deckOverviewGeojson(m)
            deckFetched[cfg.layer_id] = { bbox: [-180, -90, 180, 90], band } // grid spans the extent
            return
          }
        }
        // Detail fetch: clear a stale overview grid immediately (never show the whole-extent
        // grid at a zoomed-in view while features load) — mirrors portal.js.
        if (deckData[cfg.layer_id] && deckData[cfg.layer_id].__overview) {
          deckData[cfg.layer_id] = { type: 'FeatureCollection', features: [] }
          deckOverlay.setProps({ layers: [...configs].reverse().map(makeDeckLayer).filter(Boolean) })
        }
        deckLoading.value++
        try {
          const { data } = await getVectorFeatures(cfg.layer_id, fb.join(','), lim, signal)
          deckData[cfg.layer_id] = data
          deckFetched[cfg.layer_id] = { bbox: fb, band }
        } finally { deckLoading.value-- }
      } catch (err) {
        // An aborted fetch (newer view or a save in progress) is expected — keep the last data.
        if (err?.code === 'ERR_CANCELED' || err?.name === 'CanceledError') return
        deckData[cfg.layer_id] = deckData[cfg.layer_id] || { type: 'FeatureCollection', features: [] }
      }
    }))
  }
  // config[0] = top of list → must draw on top → last in the deck layer array.
  const layers = [...configs].reverse().map(makeDeckLayer).filter(Boolean)
  // 3D-Z inline elevation layers (no fetch): appended on top of the fetched GeoParquet layers.
  layerConfigs.value.filter(c => c.layer_type === 'elevation' && c.visible !== false)
    .slice().reverse().forEach((cfg, i) => { const l = makeElevationDeckLayer(cfg, i); if (l) layers.push(l) })
  deckOverlay.setProps({ layers })
}

// ── Preview identify popup ──────────────────────────────────────────────────
// Clicking the preview shows feature attributes, like the published portal: MVT (PostGIS)
// layers via queryRenderedFeatures; GeoParquet (deck-rendered) layers via the server identify
// endpoint — the deck data is geometry-only/capped, so attributes are fetched per click.
// Mirrors the popup logic in templates/shared/portal.js (keep in sync).
const previewPopup = new maplibregl.Popup({ closeButton: true, closeOnClick: false, maxWidth: '300px' })
const escAttr = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

function attrTableHtml(title, props, fields) {
  const keys = fields && fields.length
    ? fields.filter(k => props[k] != null)
    : Object.keys(props).filter(k => props[k] != null).slice(0, 8)
  const body = keys.length
    ? '<table style="font-size:12px;border-collapse:collapse">' + keys.map(k =>
        `<tr><th style="text-align:left;padding:2px 8px 2px 0;color:#6b7280">${escAttr(k)}</th><td style="padding:2px 0">${escAttr(props[k])}</td></tr>`).join('') + '</table>'
    : '<div style="font-size:12px;color:#6b7280">No attributes</div>'
  return `<div style="font-weight:600;font-size:12px;margin-bottom:4px">${escAttr(title)}</div>` + body
}

async function onPreviewClick(e) {
  if (!map.value) return
  const sections = []
  // MVT layers under the click (small pixel pad so lines/points are hittable).
  const pad = 5
  const box = [[e.point.x - pad, e.point.y - pad], [e.point.x + pad, e.point.y + pad]]
  const vecIds = (map.value.getStyle().layers || [])
    .map(l => l.id).filter(id => id.startsWith('vector_') && map.value.getLayer(id))
  try {
    const feats = vecIds.length ? map.value.queryRenderedFeatures(box, { layers: vecIds }) : []
    if (feats.length) {
      const f = feats[0]
      const lid = Number(String(f.layer.id).replace('vector_', ''))
      const layer = dataStore.vectorLayers.find(l => l.id === lid)
      const cfg = layerConfigs.value.find(c => c.layer_type === 'vector' && c.layer_id === lid)
      sections.push(attrTableHtml(layer?.name || f.layer.id, f.properties || {}, cfg?.popup_fields))
    }
  } catch { /* style mid-rebuild */ }

  // GeoParquet layers showing real detail (clicking the density-grid overview is meaningless).
  const deckQ = deckConfigs().filter(cfg => {
    const d = deckData[cfg.layer_id]
    return d && !d.__overview
  })
  if (!sections.length && !deckQ.length) return
  const p1 = map.value.unproject([e.point.x - pad, e.point.y])
  const p2 = map.value.unproject([e.point.x + pad, e.point.y])
  const tol = Math.max(Math.abs(p2.lng - p1.lng) / 2, 1e-7)

  previewPopup.setLngLat(e.lngLat)
    .setHTML(sections.join('<hr style="margin:6px 0;border-color:#e5e7eb">') +
      (deckQ.length ? '<div style="font-size:12px;color:#6b7280">Reading attributes…</div>' : ''))
    .addTo(map.value)

  if (!deckQ.length) return
  const results = await Promise.all(deckQ.map(async cfg => {
    try {
      const { data } = await identifyVectorFeatures(cfg.layer_id, e.lngLat.lng, e.lngLat.lat, tol, 5)
      if (!data.features?.length) return null
      const layer = dataStore.vectorLayers.find(l => l.id === cfg.layer_id)
      let html = attrTableHtml(layer?.name || `Layer ${cfg.layer_id}`, data.features[0], cfg.popup_fields)
      if (data.features.length > 1)
        html += `<div style="font-size:11px;color:#6b7280;margin-top:2px">+${data.features.length - 1} more feature${data.features.length > 2 ? 's' : ''} here</div>`
      return html
    } catch { return null }
  }))
  const all = sections.concat(results.filter(Boolean))
  if (!all.length) { previewPopup.remove(); return }
  previewPopup.setHTML(all.join('<hr style="margin:6px 0;border-color:#e5e7eb">'))
}

// Rebuild the preview style on any config/layer change, but only move the camera on
// the FIRST build (restore the saved view, else fit to all layers). After that, style
// edits (band/colour/etc.) must NOT yank the view — setStyle keeps the current camera.
let viewInitialized = false
let lastStyleJson = ''  // last applied MapLibre style → skip redundant setStyle repaints (each = a flash)
let pitched3D = false   // the one automatic tilt when an extruded layer first appears
// When the catalog arrives from /api/basemaps (or changes), refresh the picker's list and, if the
// chosen basemap's tiles differ, rebuild the preview.
watch(basemapCatalog, () => basemapControl?.refresh())

watch([layerConfigs, layerTree, loaded, basemap, basemapCatalog, ready], () => {
  if (!loaded.value || !ready.value) return
  const { style, bounds } = buildPreviewStyle()
  // A setStyle repaint is a visible flash; only apply when the style actually changed (e.g. the
  // /api/basemaps catalog resolving to the same tiles produces an identical style — skip it). Deck
  // layers live outside the MapLibre style, so refreshDeck runs regardless.
  const json = JSON.stringify(style)
  if (json !== lastStyleJson) { lastStyleJson = json; applyStyle(style) }
  // Straight down, an extruded polygon and a plain fill are the same shape — so turning 3D on
  // while looking at a flat preview appears to do NOTHING. Tilt once, the first time an extrusion
  // shows up, and never again: after that the camera is the author's to place (the same reasoning
  // as the camera watcher above, which only moves on the first build).
  if (!pitched3D && style.layers.some(l => l.type === 'fill-extrusion')) {
    pitched3D = true
    if (map.value && map.value.getPitch() === 0) map.value.easeTo({ pitch: 45, duration: 600 })
  }
  refreshDeck(false)  // rebuild deck layers (fetch only newly-appeared geoparquet layers)
  if (!viewInitialized) {
    if (savedView.value) { jumpTo(savedView.value); viewInitialized = true }
    else if (bounds) { fitToBbox(bounds); viewInitialized = true }
  }
}, { deep: true })

// Point marker icons — mirror of templates/shared/portal.js. Generated on demand when a symbol
// layer references a missing icon image; the specs come back from `buildMapStyle`.
watch(loaded, (v) => {
  if (!v || !map.value) return
  registerMarkerImages(map.value, {})
  // deck.gl overlay (once): a control so it survives setStyle; refetch the viewport on pan/zoom.
  if (!deckOverlay) {
    // Basemap picker control — top-right, above the globe/zoom controls, mirroring the published
    // portal's switcher exactly (same grid icon, same flyout menu).
    basemapControl = new BasemapControl()
    addTopRightControlFirst(basemapControl)
    deckOverlay = new MapboxOverlay({ interleaved: false, layers: [] })
    map.value.addControl(deckOverlay)
    map.value.on('click', onPreviewClick)
    map.value.on('moveend', () => refreshDeck(true))
    // Mid-gesture: hide the coarse overview grid the moment the viewport qualifies for detail —
    // don't wait for moveend + the fetch (mirrors portal.js).
    let moveRaf = false
    map.value.on('move', () => {
      if (moveRaf) return
      moveRaf = true
      requestAnimationFrame(() => {
        moveRaf = false
        if (!map.value) return
        const b = map.value.getBounds()
        const vb = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
        let changed = false
        for (const cfg of deckConfigs()) {
          const data = deckData[cfg.layer_id]
          if (!data || !data.__overview) continue
          const m = deckManifests[cfg.layer_id]
          if (!m || m === 'none' || !m.grid) continue
          const load = deckViewportLoad(m, padBbox(vb, DECK_FETCH_PAD))  // same padded bbox as the fetch
          const light = (m.feature_count || 0) <= DECK_DETAIL_MAX
          // Rows-only gate (server fetch → file count irrelevant; see the refetch branch above).
          if (light || load.rows <= DECK_DETAIL_MAX_ROWS) {
            deckData[cfg.layer_id] = { type: 'FeatureCollection', features: [] }
            delete deckFetched[cfg.layer_id]  // drop the cached region so moveend refetches detail
            changed = true
          }
        }
        if (changed) refreshDeck(false)
      })
    })
    refreshDeck(true)
  }
})

// (basemapCatalog ref is declared up top with the other state — it's referenced by watches that
// run at setup time, so it must exist before them.)
function currentBasemap() {
  return basemapCatalog.value.find(b => b.id === basemap.value) || basemapCatalog.value[0]
}

const _escHtml = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

// MapLibre control mirroring the published portal's BasemapControl (templates/shared/portal.js):
// a grid-icon button that opens a flyout list of basemaps. Picking one sets `basemap`, which the
// watcher rebuilds the preview from. Same markup/classes as the portal so the shared .gd-basemap-*
// CSS (in this component's global <style> block) styles it identically.
const _checkIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" ' +
  'stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>'
class BasemapControl {
  // Rebuild the menu's option rows from the current catalog + selection. Called on add and whenever
  // the catalog changes (e.g. after the /api/basemaps fetch resolves with more basemaps).
  _renderMenu() {
    if (!this._menu) return
    const cur = currentBasemap().id
    this._menu.innerHTML = '<div class="gd-basemap-title">Basemap</div>' +
      basemapCatalog.value.map(bm =>
        '<label class="gd-basemap-opt"><input type="radio" name="gd-basemap-editor" value="' + bm.id + '"' +
        (bm.id === cur ? ' checked' : '') + '>' +
        '<img class="gd-basemap-thumb" src="' + bm.thumb + '" alt="" loading="lazy">' +
        '<span class="gd-basemap-name">' + _escHtml(bm.name) + '</span>' +
        '<span class="gd-basemap-check">' + _checkIcon + '</span></label>').join('')
  }
  refresh() { this._renderMenu() }
  onAdd() {
    const gridIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round">' +
      '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>' +
      '<rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>'
    const c = document.createElement('div')
    c.className = 'maplibregl-ctrl maplibregl-ctrl-group gd-basemap-ctrl'
    c.innerHTML =
      '<button type="button" class="gd-basemap-btn" title="Basemaps" aria-label="Choose basemap">' + gridIcon + '</button>' +
      '<div class="gd-basemap-menu"></div>'
    const btn = c.querySelector('.gd-basemap-btn')
    const menu = c.querySelector('.gd-basemap-menu')
    this._menu = menu
    this._renderMenu()
    btn.addEventListener('click', ev => {
      ev.stopPropagation()
      // Re-render so the checked row matches the current basemap (it may have loaded from the portal
      // AFTER this control was built) and reflects any catalog update.
      this._renderMenu()
      c.classList.toggle('open')
    })
    menu.addEventListener('change', ev => { basemap.value = ev.target.value })
    menu.addEventListener('click', ev => ev.stopPropagation())
    this._docClose = () => c.classList.remove('open')
    document.addEventListener('click', this._docClose)
    this._c = c
    return c
  }
  onRemove() { document.removeEventListener('click', this._docClose); this._c?.remove() }
}

function buildPreviewStyle() {
  // The editor's own inputs, handed to the shared builder. Draw order comes from the folder tree
  // (flattened, top-first); falls back to the config order when a node has no config.
  const orderedForDraw = flattenTreeRefs(layerTree.value).map(configForNode).filter(Boolean)
  const built = buildMapStyle({
    configs: orderedForDraw.length ? orderedForDraw : layerConfigs.value,
    layers: dataStore.vectorLayers,
    rasters: dataStore.rasterLayers,
    sources: dataStore.externalSources,
    basemap: currentBasemap(),
  })
  // The icons the new style will ask for. Registered against the map rather than a module global,
  // so two maps in one session cannot overwrite each other's markers.
  setMarkerSpecs(map.value, built.markerSpecs)
  return built
}

/**
 * A stored bbox as [w, s, e, n] in lon/lat, or null. Mirrors
 * `portal_generator._lonlat_bounds` — including WHY the range check exists: a raster bbox is
 * reprojected to EPSG:4326 at ingest, but that reprojection falls back to the SOURCE CRS when it
 * fails. Projected metres used as MapLibre `bounds` would hide the layer completely, which is far
 * worse than the 404s being fixed, so anything not plausibly lon/lat is dropped.
 */



const availableLayers = computed(() => [
  ...dataStore.vectorLayers.filter(l => l.status === 'ready').map(l => ({ ...l, type: 'vector' })),
  ...dataStore.rasterLayers.filter(l => l.status === 'ready').map(l => ({ ...l, type: 'raster' })),
  ...dataStore.externalSources.map(s => ({ ...s, type: 'external' })),
].filter(l => !layerConfigs.value.some(c => c.layer_id === l.id && c.layer_type === l.type)))

const LAYER_COLORS = [
  '#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6',
  '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1',
]

function nextColor() {
  const used = layerConfigs.value.map(c => c.style?.color).filter(Boolean)
  return LAYER_COLORS.find(c => !used.includes(c)) || LAYER_COLORS[layerConfigs.value.length % LAYER_COLORS.length]
}

// Bulk add — the common opening move for a catalog portal (list everything, then prune), and just
// as useful for a web map. Iterates in REVERSE because addLayer() unshifts onto the top of the
// list, so reversing leaves the final order matching the picker's.
const addingAll = ref(false)
async function addAllLayers() {
  if (addingAll.value) return
  addingAll.value = true
  try {
    for (const l of availableLayers.value.slice().reverse()) await addLayer(l)
  } finally {
    addingAll.value = false
    showAddLayer.value = false
  }
}

async function addLayer(layer) {
  const ds = layer.default_style
  let style
  if (layer.type === 'vector') {
    style = ds?.style ?? { color: nextColor() }
  } else if (layer.type === 'external') {
    // External vector (WFS) gets a colour; external raster (WMS/XYZ) has no style.
    style = layer.kind === 'vector' ? { color: nextColor() } : {}
  } else {
    // The layer's whole raster style, not a hand-picked five of it — see `rasterStyleOf`.
    style = rasterStyleOf(ds)
  }
  // Add to the top of the list (and the top of the map).
  layerConfigs.value.unshift({
    layer_id: layer.id,
    layer_type: layer.type,
    visible: true,
    opacity: ds?.opacity ?? 1.0,
    style,
    popup_fields: ds?.popup_fields ?? [],
  })
  layerTree.value.unshift({ layer_type: layer.type, layer_id: layer.id })  // V-13: mirror into the tree (root, top)
  lastAddedKey.value = `${layer.type}-${layer.id}`
  showAddLayer.value = false

  // Auto-stretch a freshly added raster that has no stretch yet
  if (layer.type === 'raster' && !style.rescale) {
    try {
      const { data } = await getRasterStats(layer.id)
      if (data?.rescale) {
        const idx = layerConfigs.value.findIndex(c => c.layer_id === layer.id && c.layer_type === 'raster')
        if (idx !== -1) {
          layerConfigs.value[idx] = {
            ...layerConfigs.value[idx],
            style: { ...layerConfigs.value[idx].style, rescale: data.rescale },
          }
        }
      }
    } catch { /* leave unstretched */ }
  }
}

function zoomToLayer(cfg) {
  const list = cfg.layer_type === 'external' ? dataStore.externalSources
    : cfg.layer_type === 'vector' ? dataStore.vectorLayers : dataStore.rasterLayers
  const layer = list.find(l => l.id === cfg.layer_id)
  if (layer?.bbox) postToFrame({ type: 'fitbbox', bbox: layer.bbox })  // R2: fit the iframe preview
}

// Fit the preview to the merged extent of every layer inside a folder (recursively).
function zoomToGroup(node) {
  let bounds = null
  const merge = (b) => {
    const ok = Array.isArray(b) && b.length === 4 &&
      b[0] >= -180 && b[2] <= 180 && b[0] < b[2] && b[1] >= -90 && b[3] <= 90 && b[1] < b[3]
    if (!ok) return
    bounds = bounds
      ? [Math.min(bounds[0], b[0]), Math.min(bounds[1], b[1]), Math.max(bounds[2], b[2]), Math.max(bounds[3], b[3])]
      : b.slice()
  }
  const walk = (nodes) => (nodes || []).forEach(n => {
    if (n.children) return walk(n.children)
    const cfg = configForNode(n)
    if (!cfg) return
    const list = cfg.layer_type === 'external' ? dataStore.externalSources
      : cfg.layer_type === 'vector' ? dataStore.vectorLayers : dataStore.rasterLayers
    const layer = list.find(l => l.id === cfg.layer_id)
    if (layer?.bbox) merge(layer.bbox)
  })
  walk(node.children)
  if (bounds) postToFrame({ type: 'fitbbox', bbox: bounds })  // R2: fit the iframe preview
}

// Fit the preview to the merged extent of all layers (the iframe computes the union itself).
function zoomToAll() {
  postToFrame({ type: 'zoomall' })
}

// The published portal's start view. R2: the iframe reports its live camera on every moveend
// (lastView); prefer that, else the previously-saved view. Spread WHOLE rather than picked apart —
// the camera shape is owned by portal.js::currentViewObj (center/zoom/bearing/pitch/projection), so
// a key added there flows through here without a matching edit.
// Reads from the live preview first (lastView), falling back to what was saved — same precedence as
// currentView(), so the checkbox always shows what WOULD be pinned right now.
const startsInGlobe = computed(() =>
  (lastView.value?.projection ?? savedView.value?.projection) === 'globe')
function setGlobe(on) {
  postToFrame({ type: 'projection', value: on ? 'globe' : 'mercator' })
  // Optimistic: the iframe reports the real camera back within a frame, but a portal that has never
  // reported one yet would otherwise leave the box unticked after a click.
  if (lastView.value) lastView.value = { ...lastView.value, projection: on ? 'globe' : 'mercator' }
}

// The on-map tilt control's two states, as an authoring decision. Mirrors portal.js TILT_PITCH /
// TILT_ON_AT — a pitch is "tilted" when it is past the threshold, not only when it equals 60, so
// a camera the author dragged to some other angle still reads as tilted here.
const TILT_PITCH = 60
const TILT_ON_AT = 5
const startsTilted = computed(() =>
  ((lastView.value?.pitch ?? savedView.value?.pitch) || 0) >= TILT_ON_AT)
function setTilt(on) {
  postToFrame({ type: 'tilt', value: on })
  // Optimistic for the same reason as setGlobe, plus one of its own: the preview EASES over half a
  // second, so waiting for moveend would leave the box visibly lagging the click.
  if (lastView.value) lastView.value = { ...lastView.value, pitch: on ? TILT_PITCH : 0 }
}

function currentView() {
  if (lastView.value && Array.isArray(lastView.value.center)) return { ...lastView.value }
  return savedView.value || null
}

async function save() {
  if (!portal.value) return
  busy.value = true
  saveMsg.value = null
  // Free any connections held by in-flight viewport fetches so the save request isn't queued
  // behind them (the "sometimes it never saves" symptom on heavy GeoParquet layers).
  abortDeckFetches()
  try {
    const view = currentView()
    if (view) savedView.value = view
    const payload = {
      layer_configs: layerConfigs.value,
      layer_groups: layerTree.value,   // V-13: the folder tree (structure + order)
      layout_config: layoutConfig.value,           // V-11: {archetype, regions, panels}
      story: story.value,                          // V-11: storymap sections (baked only for storymap)
      theme: theme.value,                          // V-11 R3: colour theme (mode/accent/font)
      dashboard: dashboard.value || {},            // V-16: widget grid (baked only for dashboard)
      template_id: selectedTemplate.value,
      access_type: accessType.value,
      initial_view: view,
      description: description.value,
      basemap: basemap.value || basemapCatalog.value[0].id,
    }
    if (accessType.value === 'password' && accessPassword.value) {
      payload.access_password = accessPassword.value
    }
    const updated = await portalsStore.update(portal.value.id, payload)
    portal.value = updated
    accessPassword.value = ''
    saveMsg.value = { type: 'ok', text: 'Saved' }
    setTimeout(() => { saveMsg.value = null }, 3000)
  } catch (err) {
    saveMsg.value = { type: 'err', text: err.response?.data?.detail || err.message }
  } finally {
    busy.value = false
  }
}

// Card thumbnail — THE shared implementation, the same one the Portals list uses.
//
// The editor used to photograph its own visible preview iframe, which looked like the cheaper
// option: the frame is already there and warm. It was also the only path that worked, and it drifted
// from the list's path in every detail that mattered — the size floor, the reply handling, whether a
// failure was reported at all. Worse, it inherited the bug that made the list version fail for
// weeks: a request posted at a moment when portal.js might not yet be listening.
//
// One implementation. The extra second spent mounting a fresh off-screen frame buys a capture that
// behaves identically wherever publishing happens, and a fix here fixes both.
async function captureThumbnail() {
  const { url, error } = await capturePortalThumbnail(
    portal.value.id, { hasExisting: !!portal.value.thumbnail_url })
  if (!url) {
    // Visible, not silent. Publishing succeeded; the picture did not, and the operator was left to
    // infer that from a card that never changed.
    saveMsg.value = { type: 'err',
                      text: 'Published, but the card preview could not be captured: ' + error }
    return
  }
  // Write the new URL back into BOTH the open portal and the cached list. publish() replaced the
  // store entry moments ago with a response whose thumbnail_url predates this upload, so without
  // this the card kept its gradient until something refetched the list — which looked like the
  // snapshot taking minutes to appear when it had actually been ready all along.
  portal.value = { ...portal.value, thumbnail_url: url }
  const list = portalsStore.portals
  const idx = list.findIndex(p => p.id === portal.value.id)
  if (idx !== -1) list[idx] = { ...list[idx], thumbnail_url: url }
}

async function handlePublish() {
  await save()
  if (saveMsg.value?.type === 'err') return
  busy.value = true
  try {
    const updated = await portalsStore.publish(portal.value.id)
    portal.value = updated
    // AFTER publishing, so a failed publish leaves no thumbnail for a portal that isn't live, and
    // awaited so the Portals list shows the new card image immediately on navigating back.
    await captureThumbnail()
  } catch (err) {
    saveMsg.value = { type: 'err', text: err.response?.data?.detail || err.message }
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
/* TipTap editing surface — Tailwind preflight strips heading/list styling, restore it so the
   editor reads like the published About page. */
.gd-tiptap :deep(.ProseMirror) { min-height: 340px; padding: 14px 16px; outline: none; font-size: .875rem; line-height: 1.6; }
.gd-tiptap :deep(h1) { font-size: 1.2rem; font-weight: 700; margin: .7rem 0 .35rem; }
.gd-tiptap :deep(h2) { font-size: 1.05rem; font-weight: 600; margin: .6rem 0 .3rem; }
.gd-tiptap :deep(h3) { font-size: .95rem; font-weight: 600; margin: .5rem 0 .25rem; }
.gd-tiptap :deep(p) { margin: .3rem 0; }
.gd-tiptap :deep(ul) { list-style: disc; margin: .3rem 0 .3rem 1.2rem; }
.gd-tiptap :deep(ol) { list-style: decimal; margin: .3rem 0 .3rem 1.2rem; }
.gd-tiptap :deep(blockquote) { border-left: 3px solid hsl(var(--border)); padding-left: .8rem; color: hsl(var(--muted-foreground)); margin: .4rem 0; }
.gd-tiptap :deep(a) { color: hsl(var(--primary)); text-decoration: underline; }
.gd-tiptap :deep(code) { font-size: .8rem; background: hsl(var(--muted)); border: 1px solid hsl(var(--border)); border-radius: 4px; padding: 0 3px; }
.gd-tiptap :deep(img) { max-width: 100%; border-radius: 8px; border: 1px solid hsl(var(--border)); margin: .5rem 0; }
.gd-tiptap :deep(img.ProseMirror-selectednode) { outline: 2px solid hsl(var(--primary)); }
</style>

<!-- Non-scoped: the basemap control DOM is created imperatively (MapLibre control), so scoped
     styles wouldn't reach it. Mirrors templates/shared/portal.css .gd-basemap-* but colored from the
     editor's theme tokens (which flip under html.dark) so it matches the published portal. -->
<style>
.gd-basemap-ctrl { position: relative; }
/* Selector is intentionally specific (.gd-basemap-ctrl .gd-basemap-btn) so it beats MapLibre's own
   `.maplibregl-ctrl-group button` rule — otherwise its display/box-model wins and the child SVG
   isn't centered. */
.gd-basemap-ctrl .gd-basemap-btn {
  width: 29px; height: 29px; display: flex; align-items: center; justify-content: center;
  padding: 0; background: transparent; border: none; cursor: pointer; color: hsl(var(--foreground));
}
.gd-basemap-ctrl .gd-basemap-btn svg { width: 18px; height: 18px; display: block; }
.gd-basemap-menu {
  display: none; position: absolute; top: 0; right: 40px; width: 250px;
  background: hsl(var(--card)); border: 1px solid hsl(var(--border)); border-radius: 12px;
  box-shadow: 0 10px 25px -5px rgb(0 0 0 / 0.35); padding: 8px;
}
.gd-basemap-ctrl.open .gd-basemap-menu { display: block; }
.gd-basemap-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em;
  color: hsl(var(--muted-foreground)); padding: 3px 6px 8px;
}
.gd-basemap-opt {
  display: flex; align-items: center; gap: 12px; padding: 7px 8px;
  font-size: 13px; border-radius: 9px; cursor: pointer; position: relative;
  border: 1.5px solid transparent; transition: background .12s, border-color .12s;
}
.gd-basemap-opt:hover { background: hsl(var(--muted) / .6); }
.gd-basemap-opt input { position: absolute; opacity: 0; width: 0; height: 0; pointer-events: none; }
.gd-basemap-opt:has(input:checked) { border-color: hsl(var(--primary)); background: hsl(var(--primary) / .1); }
.gd-basemap-name { flex: 1; font-weight: 500; color: hsl(var(--foreground)); white-space: nowrap; }
.gd-basemap-check { display: flex; color: hsl(var(--primary)); opacity: 0; flex-shrink: 0; }
.gd-basemap-check svg { width: 17px; height: 17px; }
.gd-basemap-opt:has(input:checked) .gd-basemap-check { opacity: 1; }
.gd-basemap-thumb {
  width: 68px; height: 46px; object-fit: cover; border-radius: 7px;
  border: 1px solid hsl(var(--border)); flex-shrink: 0; background: hsl(var(--muted));
}
/* Dark-mode MapLibre controls (globe/zoom) — recolour to the theme surface + invert the built-in
   glyphs so the whole top-right stack matches (the basemap button uses currentColor, not
   .maplibregl-ctrl-icon, so the filter leaves it alone). */
.dark .maplibregl-ctrl-group { background: hsl(var(--card)); box-shadow: 0 0 0 1px hsl(var(--border)), 0 2px 6px rgb(0 0 0 / .3); }
.dark .maplibregl-ctrl-group button + button { border-top-color: hsl(var(--border)); }
.dark .maplibregl-ctrl-group button:hover { background: hsl(var(--muted)); }
.dark .maplibregl-ctrl button .maplibregl-ctrl-icon { filter: invert(1) hue-rotate(180deg) brightness(1.05); }
</style>
