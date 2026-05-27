<template>
  <div class="card overflow-hidden group">
    <div class="h-28 bg-gradient-to-br from-brand-700 to-brand-500 relative">
      <div class="absolute inset-0 flex items-center justify-center">
        <GlobeIcon class="w-10 h-10 text-white/40" />
      </div>
      <div v-if="portal.published" class="absolute top-2 right-2">
        <span class="text-xs bg-green-500 text-white px-2 py-0.5 rounded-full font-medium">Live</span>
      </div>
    </div>

    <div class="p-4 space-y-3">
      <div>
        <h3 class="font-semibold text-gray-900 truncate">{{ portal.title }}</h3>
        <p v-if="portal.description" class="text-xs text-gray-500 mt-0.5 line-clamp-2">{{ portal.description }}</p>
      </div>

      <div class="text-xs text-gray-400 flex gap-3">
        <span>{{ portal.layer_configs?.length || 0 }} layer{{ portal.layer_configs?.length !== 1 ? 's' : '' }}</span>
        <span>{{ portal.template_id }}</span>
        <span>{{ portal.access_type }}</span>
      </div>

      <div class="flex gap-2 pt-1">
        <button @click="$emit('edit')" class="btn-secondary flex-1 justify-center text-xs py-1.5">Edit</button>
        <button v-if="!portal.published" @click="$emit('publish')" class="btn-primary flex-1 justify-center text-xs py-1.5">Publish</button>
        <template v-else>
          <a :href="`/portals/${portal.slug}/`" target="_blank" class="btn-primary flex-1 justify-center text-xs py-1.5 no-underline">
            <ExternalLinkIcon class="w-3 h-3" /> View
          </a>
          <button @click="$emit('unpublish')" class="btn-secondary px-2 text-xs py-1.5 text-gray-500" title="Unpublish">⊘</button>
        </template>
        <button @click="$emit('delete')" class="px-2 text-gray-400 hover:text-red-500 transition-colors" title="Delete">
          <TrashIcon class="w-4 h-4" />
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { GlobeIcon, TrashIcon, ExternalLinkIcon } from '@/views/icons'

defineProps({ portal: Object })
defineEmits(['edit', 'publish', 'unpublish', 'delete'])
</script>
