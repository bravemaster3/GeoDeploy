<template>
  <Teleport to="body">
  <div class="fixed inset-0 bg-gray-900/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
    <div class="card w-full max-w-md p-6 space-y-4 shadow-2xl max-h-[90vh] overflow-y-auto">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold">New portal</h2>
        <button @click="$emit('close')" class="text-muted-foreground/70 hover:text-foreground text-xl">&times;</button>
      </div>

      <div>
        <label class="label">Title</label>
        <input v-model="form.title" class="input" placeholder="My Geoportal" />
      </div>
      <div>
        <label class="label">Description (optional)</label>
        <textarea v-model="form.description" class="input" rows="2" />
      </div>
      <div>
        <label class="label">Access</label>
        <select v-model="form.access_type" class="input">
          <option value="public">Public — anyone can view</option>
          <option value="password">Password protected</option>
          <option value="private">Private — only you</option>
        </select>
      </div>
      <div v-if="form.access_type === 'password'">
        <label class="label">Portal password</label>
        <input v-model="form.access_password" type="password" class="input" />
      </div>

      <div v-if="error" class="text-sm text-red-400">{{ error }}</div>

      <div class="flex gap-3 justify-end pt-2">
        <button @click="$emit('close')" class="btn-secondary">Cancel</button>
        <button @click="submit" :disabled="busy || !form.title" class="btn-primary">
          Create portal
        </button>
      </div>
    </div>
  </div>
  </Teleport>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { usePortalsStore } from '@/stores/portals'
import { useRouter } from 'vue-router'

const emit = defineEmits(['close', 'created'])
const portalsStore = usePortalsStore()
const router = useRouter()
const busy = ref(false)
const error = ref('')

const form = reactive({
  title: '',
  description: '',
  template_id: 'minimal',
  access_type: 'public',
  access_password: '',
  layer_configs: [],
})

async function submit() {
  if (!form.title) return
  busy.value = true
  error.value = ''
  try {
    const portal = await portalsStore.create(form)
    emit('created')
    router.push(`/portals/${portal.id}/edit`)
  } catch (err) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    busy.value = false
  }
}
</script>
