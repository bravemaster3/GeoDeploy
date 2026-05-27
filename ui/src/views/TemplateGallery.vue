<template>
  <div class="p-6 space-y-6">
    <h1 class="text-xl font-semibold">Template Gallery</h1>

    <div v-if="loading" class="text-sm text-gray-400">Loading templates…</div>
    <div v-else-if="!templates.length" class="text-sm text-gray-400">No templates found.</div>

    <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
      <div v-for="t in templates" :key="t.id" class="card overflow-hidden">
        <div class="h-36 bg-gradient-to-br from-gray-700 to-gray-900 flex items-center justify-center text-gray-500">
          <img v-if="t.preview_url" :src="t.preview_url" class="w-full h-full object-cover" loading="lazy" />
          <span v-else class="text-xs">No preview</span>
        </div>
        <div class="p-4 space-y-2">
          <div class="flex items-start justify-between gap-2">
            <h3 class="font-semibold text-sm">{{ t.name }}</h3>
            <span v-if="t.is_official" class="text-xs bg-brand-100 text-brand-700 px-1.5 py-0.5 rounded font-medium flex-shrink-0">Official</span>
          </div>
          <p class="text-xs text-gray-500 line-clamp-2">{{ t.description }}</p>
          <div class="flex flex-wrap gap-1">
            <span v-for="tag in t.tags" :key="tag" class="text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">{{ tag }}</span>
          </div>
          <div class="text-xs text-gray-400 flex gap-2">
            <span>by {{ t.author }}</span>
            <span>· {{ t.language.toUpperCase() }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { listTemplates } from '@/api'

const templates = ref([])
const loading = ref(true)

onMounted(async () => {
  const { data } = await listTemplates()
  templates.value = data
  loading.value = false
})
</script>
