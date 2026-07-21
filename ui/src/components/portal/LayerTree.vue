<template>
  <!-- Recursive folder tree (V-13). Within-level ops (reorder, delete-group, add-subfolder, rename,
       collapse, exclusive, description) mutate `nodes` in place; layer ops + cross-folder moves bubble
       to PortalEditor, which owns layerConfigs. Drag & drop is centralized in PortalEditor via `dnd`
       (reorder · drop a layer into a folder · drag whole folders); the ↑ ↓ arrows + move-to-folder
       menu remain as an explicit fallback. `filter` hides non-matching layers + empty folders. -->
  <div class="space-y-0.5">
    <div v-for="(node, i) in nodes" v-show="nodeVisible(node)"
      :key="node.children ? node.id : `${node.layer_type}-${node.layer_id}`">
      <!-- Group node -->
      <template v-if="node.children">
        <div class="flex items-center gap-1 py-1 px-1 rounded hover:bg-muted/60 group/row transition-shadow"
          :class="dropClass(node)" :style="{ marginLeft: depth * 12 + 'px' }"
          @dragover="onOver($event, node)" @drop="onDrop($event, node)">
          <span class="dnd-grip text-muted-foreground/40 hover:text-muted-foreground cursor-grab flex-shrink-0 flex items-center"
            draggable="true" @dragstart="dnd.start(node, $event)" @dragend="dnd.end()" title="Drag folder" v-html="gripSvg"></span>
          <button @click="node.collapsed = !node.collapsed"
            class="text-muted-foreground/70 hover:text-foreground flex-shrink-0 w-4 flex justify-center"
            :title="node.collapsed ? 'Expand' : 'Collapse'">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"
              stroke-linecap="round" stroke-linejoin="round" class="transition-transform"
              :style="{ transform: node.collapsed ? '' : 'rotate(90deg)' }"><polyline points="9 6 15 12 9 18" /></svg>
          </button>
          <span class="text-muted-foreground/50 flex-shrink-0" v-html="folderSvg"></span>
          <input v-model="node.name" placeholder="Folder name"
            class="text-xs font-semibold flex-1 min-w-0 bg-transparent border border-transparent hover:border-border focus:border-primary/60 rounded px-1 py-0.5 focus:outline-none" />
          <!-- Action cluster (revealed on hover) -->
          <div class="flex items-center gap-0.5 flex-shrink-0 opacity-40 group-hover/row:opacity-100 transition-opacity">
            <button @click="$emit('group-zoom', node)" title="Zoom to this folder's layers" class="ctl-btn" v-html="zoomSvg"></button>
            <button @click="node.exclusive = !node.exclusive" title="Exclusive — only one layer visible at a time"
              class="text-[10px] font-mono px-1 py-0.5 rounded leading-none"
              :class="node.exclusive ? 'bg-primary/15 text-primary' : 'text-muted-foreground/60 hover:text-foreground hover:bg-muted'">1of</button>
            <button @click="node.__desc = !node.__desc" title="Description"
              class="ctl-btn" :class="{ 'text-primary': node.__desc || node.description }" v-html="infoSvg"></button>
            <button @click="addSubfolder(node)" title="Add sub-folder" class="ctl-btn" v-html="addFolderSvg"></button>
            <span class="w-px h-4 bg-border/70 mx-0.5"></span>
            <button @click="moveUp(i)" :disabled="i === 0" class="ctl-btn disabled:opacity-25" title="Move up" v-html="upSvg"></button>
            <button @click="moveDown(i)" :disabled="i === nodes.length - 1" class="ctl-btn disabled:opacity-25" title="Move down" v-html="downSvg"></button>
            <button @click="deleteGroup(i)" title="Delete folder (keeps its layers)"
              class="ctl-btn hover:text-red-500" v-html="trashSvg"></button>
          </div>
        </div>
        <input v-if="node.__desc" v-model="node.description" placeholder="Optional description shown in the portal"
          class="text-[11px] w-full bg-transparent border border-border rounded px-1.5 py-0.5 mb-0.5 focus:outline-none focus:border-primary/60"
          :style="{ marginLeft: depth * 12 + 22 + 'px', width: `calc(100% - ${depth * 12 + 22}px)` }" />
        <div v-show="!node.collapsed || filtering" class="border-l border-border/60" :style="{ marginLeft: depth * 12 + 7 + 'px' }">
          <LayerTree :nodes="node.children" :configs="configs" :depth="0" :all-groups="allGroups" :dnd="dnd" :filter="filter"
            @layer-remove="$emit('layer-remove', $event)" @layer-update="$emit('layer-update', $event)"
            @layer-zoom="$emit('layer-zoom', $event)" @group-zoom="$emit('group-zoom', $event)" @move-to="$emit('move-to', $event)" />
          <p v-if="!node.children.length" class="text-[11px] text-muted-foreground/50 italic py-1"
            :style="{ marginLeft: '8px' }">Empty — drag layers here</p>
        </div>
      </template>

      <!-- Layer node -->
      <template v-else>
        <div class="flex items-center gap-1 rounded group/row transition-shadow" :class="dropClass(node)"
          :style="{ marginLeft: depth * 12 + 'px' }"
          @dragover="onOver($event, node)" @drop="onDrop($event, node)">
          <div class="flex-1 min-w-0">
            <LayerPanel v-if="configFor(node)" :config="configFor(node)"
              @remove="$emit('layer-remove', node)"
              @update="$emit('layer-update', { node, patch: $event })"
              @zoom="$emit('layer-zoom', node)"
              @dragstart="dnd.start(node, $event)" @dragend="dnd.end()" />
          </div>
          <!-- Move cluster (revealed on hover) -->
          <div class="flex items-center gap-0.5 flex-shrink-0 opacity-0 group-hover/row:opacity-100 transition-opacity pr-0.5">
            <button @click="moveUp(i)" :disabled="i === 0" class="ctl-btn disabled:opacity-25" title="Move up" v-html="upSvg"></button>
            <button @click="moveDown(i)" :disabled="i === nodes.length - 1" class="ctl-btn disabled:opacity-25" title="Move down" v-html="downSvg"></button>
            <div v-if="allGroups.length" class="relative flex items-center">
              <button class="ctl-btn" title="Move to folder" v-html="folderMoveSvg"></button>
              <select :value="''" @change="onMove(node, $event)" title="Move to folder"
                class="absolute inset-0 opacity-0 cursor-pointer w-full">
                <option value="" disabled>Move to…</option>
                <option value="root">↑ Top level</option>
                <option v-for="g in allGroups" :key="g.id" :value="g.id">{{ g.path }}</option>
              </select>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import LayerPanel from './LayerPanel.vue'
import { useDataStore } from '@/stores/data'

const props = defineProps({
  nodes: { type: Array, required: true },
  configs: { type: Array, required: true },
  depth: { type: Number, default: 0 },
  allGroups: { type: Array, default: () => [] },  // flat {id, path} for the move dropdown
  dnd: { type: Object, required: true },          // shared drag controller (owned by PortalEditor)
  filter: { type: String, default: '' },          // search text (name filter)
})
const emit = defineEmits(['layer-remove', 'layer-update', 'layer-zoom', 'group-zoom', 'move-to'])

const dataStore = useDataStore()
const gid = () => 'g' + Math.random().toString(36).slice(2, 9)

function configFor(node) {
  return props.configs.find(c => c.layer_type === node.layer_type && c.layer_id === node.layer_id)
}

// ── Search / filter (by layer name, resolved via the data store like LayerPanel) ──
const q = computed(() => (props.filter || '').trim().toLowerCase())
const filtering = computed(() => !!q.value)
function nameOf(node) {
  if (node.layer_type === 'external') return dataStore.externalSources.find(s => s.id === node.layer_id)?.name || ''
  const list = node.layer_type === 'vector' ? dataStore.vectorLayers : dataStore.rasterLayers
  return list.find(l => l.id === node.layer_id)?.name || ''
}
function layerMatches(node) { return !q.value || nameOf(node).toLowerCase().includes(q.value) }
function groupHasMatch(node) { return (node.children || []).some(c => c.children ? groupHasMatch(c) : layerMatches(c)) }
function nodeVisible(node) {
  if (!q.value) return true
  return node.children ? groupHasMatch(node) : layerMatches(node)
}

// ── Drag & drop (positions computed here; the move is applied centrally in PortalEditor) ──
function posFor(e, node) {
  const r = e.currentTarget.getBoundingClientRect()
  const y = (e.clientY - r.top) / (r.height || 1)
  if (node.children) return y < 0.28 ? 'before' : y > 0.72 ? 'after' : 'into'
  return y < 0.5 ? 'before' : 'after'
}
function onOver(e, node) {
  if (!props.dnd.state.draggingKey) return
  e.preventDefault(); e.stopPropagation()
  props.dnd.over(node, posFor(e, node))
}
function onDrop(e, node) {
  if (!props.dnd.state.draggingKey) return
  e.preventDefault(); e.stopPropagation()
  props.dnd.drop(node, posFor(e, node))
}
function dropClass(node) {
  if (props.dnd.state.overKey !== props.dnd.keyOf(node)) return ''
  return 'dnd-' + props.dnd.state.overPos
}

// ── Within-level structural ops ──
function addSubfolder(node) {
  node.collapsed = false
  node.children.unshift({ id: gid(), name: 'New folder', collapsed: false, exclusive: false, description: '', children: [] })
}
function deleteGroup(i) {
  const g = props.nodes[i]
  props.nodes.splice(i, 1, ...g.children)  // promote children into this position
}
function moveUp(i) { if (i > 0) props.nodes.splice(i - 1, 0, props.nodes.splice(i, 1)[0]) }
function moveDown(i) { if (i < props.nodes.length - 1) props.nodes.splice(i + 1, 0, props.nodes.splice(i, 1)[0]) }
function onMove(node, e) {
  const groupId = e.target.value
  e.target.value = ''
  if (groupId) emit('move-to', { node, groupId })
}

// ── Inline icons ──
const gripSvg = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/><circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/></svg>'
const folderSvg = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>'
const infoSvg = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><line x1="12" y1="11" x2="12" y2="16"/><circle cx="12" cy="8" r="0.6" fill="currentColor"/></svg>'
const addFolderSvg = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><line x1="12" y1="10" x2="12" y2="16"/><line x1="9" y1="13" x2="15" y2="13"/></svg>'
const folderMoveSvg = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="10 11 13 13 10 15"/></svg>'
const upSvg = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="6"/><polyline points="6 12 12 6 18 12"/></svg>'
const downSvg = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="18"/><polyline points="18 12 12 18 6 12"/></svg>'
const trashSvg = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>'
const zoomSvg = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>'
</script>

<style scoped>
.ctl-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; border-radius: 5px; flex-shrink: 0;
  color: hsl(var(--muted-foreground)); transition: background .12s, color .12s;
}
.ctl-btn:hover:not(:disabled) { background: hsl(var(--muted)); color: hsl(var(--foreground)); }
/* Drop indicators */
.dnd-before { box-shadow: inset 0 2px 0 hsl(var(--primary)); }
.dnd-after { box-shadow: inset 0 -2px 0 hsl(var(--primary)); }
.dnd-into { box-shadow: inset 0 0 0 2px hsl(var(--primary)); background: hsl(var(--primary) / 0.06); }
</style>
