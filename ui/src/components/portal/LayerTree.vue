<template>
  <!-- Recursive folder tree (V-13). Mutates its own `nodes` array in place for within-level ops
       (reorder, delete-group, add-subfolder, rename, collapse, exclusive, description); layer ops and
       cross-folder moves bubble to PortalEditor, which owns layerConfigs. -->
  <div class="space-y-0.5">
    <div v-for="(node, i) in nodes" :key="node.children ? node.id : `${node.layer_type}-${node.layer_id}`">
      <!-- Group node -->
      <template v-if="node.children">
        <div class="flex items-center gap-1 py-1 px-1 rounded hover:bg-muted/60"
          :style="{ marginLeft: depth * 12 + 'px' }">
          <button @click="node.collapsed = !node.collapsed"
            class="text-muted-foreground/70 hover:text-foreground flex-shrink-0 w-4 flex justify-center"
            :title="node.collapsed ? 'Expand' : 'Collapse'">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"
              stroke-linecap="round" stroke-linejoin="round" class="transition-transform"
              :style="{ transform: node.collapsed ? '' : 'rotate(90deg)' }"><polyline points="9 6 15 12 9 18" /></svg>
          </button>
          <input v-model="node.name" placeholder="Folder name"
            class="text-xs font-semibold flex-1 min-w-0 bg-transparent border border-transparent hover:border-border focus:border-primary/60 rounded px-1 py-0.5 focus:outline-none" />
          <button @click="node.exclusive = !node.exclusive" title="Exclusive (only one layer visible at a time)"
            class="flex-shrink-0 text-[10px] font-mono px-1 rounded"
            :class="node.exclusive ? 'bg-primary/15 text-primary' : 'text-muted-foreground/60 hover:text-foreground'">1of</button>
          <button @click="node.__desc = !node.__desc" title="Description"
            class="flex-shrink-0 text-muted-foreground/60 hover:text-foreground text-xs">ⓘ</button>
          <button @click="addSubfolder(node)" title="Add sub-folder"
            class="flex-shrink-0 text-muted-foreground/60 hover:text-primary text-sm leading-none">＋</button>
          <button @click="moveUp(i)" :disabled="i === 0" title="Move up"
            class="flex-shrink-0 text-muted-foreground/60 hover:text-foreground disabled:opacity-30 text-xs">↑</button>
          <button @click="moveDown(i)" :disabled="i === nodes.length - 1" title="Move down"
            class="flex-shrink-0 text-muted-foreground/60 hover:text-foreground disabled:opacity-30 text-xs">↓</button>
          <button @click="deleteGroup(i)" title="Delete folder (keeps its layers)"
            class="flex-shrink-0 text-muted-foreground/60 hover:text-red-500 text-xs">🗑</button>
        </div>
        <input v-if="node.__desc" v-model="node.description" placeholder="Optional description shown in the portal"
          class="text-[11px] w-full bg-transparent border border-border rounded px-1.5 py-0.5 mb-0.5 focus:outline-none focus:border-primary/60"
          :style="{ marginLeft: depth * 12 + 16 + 'px', width: `calc(100% - ${depth * 12 + 16}px)` }" />
        <div v-show="!node.collapsed" class="border-l border-border/60" :style="{ marginLeft: depth * 12 + 7 + 'px' }">
          <LayerTree :nodes="node.children" :configs="configs" :depth="0" :all-groups="allGroups"
            @layer-remove="$emit('layer-remove', $event)" @layer-update="$emit('layer-update', $event)"
            @layer-zoom="$emit('layer-zoom', $event)" @move-to="$emit('move-to', $event)" />
        </div>
      </template>

      <!-- Layer node -->
      <template v-else>
        <div class="flex items-center gap-1" :style="{ marginLeft: depth * 12 + 'px' }">
          <div class="flex-1 min-w-0">
            <LayerPanel v-if="configFor(node)" :config="configFor(node)"
              @remove="$emit('layer-remove', node)"
              @update="$emit('layer-update', { node, patch: $event })"
              @zoom="$emit('layer-zoom', node)" />
          </div>
          <select :value="''" @change="onMove(node, $event)" title="Move to folder"
            class="flex-shrink-0 text-[10px] text-muted-foreground/60 bg-transparent border-0 max-w-4 focus:outline-none cursor-pointer">
            <option value="" disabled>⇢</option>
            <option value="root">↑ Top level</option>
            <option v-for="g in allGroups" :key="g.id" :value="g.id">{{ g.path }}</option>
          </select>
          <button @click="moveUp(i)" :disabled="i === 0" class="flex-shrink-0 text-muted-foreground/50 hover:text-foreground disabled:opacity-30 text-xs" title="Move up">↑</button>
          <button @click="moveDown(i)" :disabled="i === nodes.length - 1" class="flex-shrink-0 text-muted-foreground/50 hover:text-foreground disabled:opacity-30 text-xs" title="Move down">↓</button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import LayerPanel from './LayerPanel.vue'

const props = defineProps({
  nodes: { type: Array, required: true },
  configs: { type: Array, required: true },
  depth: { type: Number, default: 0 },
  allGroups: { type: Array, default: () => [] },  // flat {id, path} for the move dropdown
})
const emit = defineEmits(['layer-remove', 'layer-update', 'layer-zoom', 'move-to'])

const gid = () => 'g' + Math.random().toString(36).slice(2, 9)

function configFor(node) {
  return props.configs.find(c => c.layer_type === node.layer_type && c.layer_id === node.layer_id)
}
function addSubfolder(node) {
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
</script>
