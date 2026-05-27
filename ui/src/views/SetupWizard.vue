<template>
  <div class="min-h-screen bg-gray-50 flex items-center justify-center p-4">
    <div class="w-full max-w-lg">
      <div class="text-center mb-8">
        <h1 class="text-3xl font-bold text-gray-900">GeoDeploy</h1>
        <p class="text-gray-500 mt-1">Initial setup — takes about 2 minutes</p>
      </div>

      <!-- Step progress -->
      <div class="flex items-center justify-center gap-2 mb-8">
        <template v-for="(s, i) in steps" :key="i">
          <div class="w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold"
            :class="step > i ? 'bg-brand-600 text-white' : step === i ? 'bg-brand-100 text-brand-700 ring-2 ring-brand-500' : 'bg-gray-200 text-gray-500'"
          >{{ i + 1 }}</div>
          <div v-if="i < steps.length - 1" class="w-12 h-0.5" :class="step > i ? 'bg-brand-600' : 'bg-gray-200'" />
        </template>
      </div>

      <div class="card p-6 space-y-6">
        <!-- Step 0: Database -->
        <template v-if="step === 0">
          <h2 class="text-lg font-semibold">Database setup</h2>
          <div class="space-y-3">
            <label v-for="opt in dbOptions" :key="opt.value"
              class="flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors"
              :class="db.type === opt.value ? 'border-brand-500 bg-brand-50' : 'border-gray-200 hover:border-gray-300'"
            >
              <input type="radio" v-model="db.type" :value="opt.value" class="mt-0.5" />
              <div>
                <div class="font-medium text-sm">{{ opt.label }}</div>
                <div class="text-xs text-gray-500">{{ opt.desc }}</div>
              </div>
            </label>
          </div>
          <template v-if="db.type === 'external'">
            <div class="grid grid-cols-2 gap-3">
              <div class="col-span-2"><label class="label">Host</label><input v-model="db.host" class="input" placeholder="localhost" /></div>
              <div><label class="label">Port</label><input v-model="db.port" type="number" class="input" /></div>
              <div><label class="label">Database</label><input v-model="db.db" class="input" /></div>
              <div><label class="label">User</label><input v-model="db.user" class="input" /></div>
              <div><label class="label">Password</label><input v-model="db.password" type="password" class="input" /></div>
            </div>
          </template>
        </template>

        <!-- Step 1: Storage -->
        <template v-else-if="step === 1">
          <h2 class="text-lg font-semibold">File storage setup</h2>
          <div class="space-y-3">
            <label v-for="opt in storageOptions" :key="opt.value"
              class="flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors"
              :class="storage.type === opt.value ? 'border-brand-500 bg-brand-50' : 'border-gray-200 hover:border-gray-300'"
            >
              <input type="radio" v-model="storage.type" :value="opt.value" class="mt-0.5" />
              <div>
                <div class="font-medium text-sm">{{ opt.label }}</div>
                <div class="text-xs text-gray-500">{{ opt.desc }}</div>
              </div>
            </label>
          </div>
          <template v-if="storage.type !== 'local'">
            <div class="space-y-3">
              <div><label class="label">Endpoint URL</label><input v-model="storage.endpoint" class="input" placeholder="https://s3.amazonaws.com" /></div>
              <div><label class="label">Bucket</label><input v-model="storage.bucket" class="input" /></div>
              <div><label class="label">Access Key</label><input v-model="storage.access_key" class="input" /></div>
              <div><label class="label">Secret Key</label><input v-model="storage.secret_key" type="password" class="input" /></div>
            </div>
          </template>
        </template>

        <!-- Step 2: Admin account -->
        <template v-else-if="step === 2">
          <h2 class="text-lg font-semibold">Create admin account</h2>
          <div class="space-y-3">
            <div><label class="label">Full name</label><input v-model="admin.name" class="input" /></div>
            <div><label class="label">Email</label><input v-model="admin.email" type="email" class="input" /></div>
            <div><label class="label">Password</label><input v-model="admin.password" type="password" class="input" /></div>
          </div>
        </template>

        <!-- Error -->
        <div v-if="error" class="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{{ error }}</div>

        <!-- Actions -->
        <div class="flex justify-between pt-2">
          <button v-if="step > 0" @click="step--" class="btn-secondary">Back</button>
          <button @click="next" :disabled="busy"
            class="btn-primary ml-auto"
            :class="busy ? 'opacity-60 cursor-not-allowed' : ''"
          >
            <span v-if="busy" class="animate-spin">⟳</span>
            {{ step < steps.length - 1 ? 'Continue' : 'Complete setup' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { configureDB, configureStorage, createAdmin } from '@/api'

const router = useRouter()
const step = ref(0)
const busy = ref(false)
const error = ref('')

const steps = ['Database', 'Storage', 'Admin']

const db = reactive({ type: 'local', host: '', port: 5432, db: 'geodeploy', user: 'geodeploy', password: '' })
const storage = reactive({ type: 'local', endpoint: '', bucket: 'geodeploy', access_key: '', secret_key: '', region: 'us-east-1' })
const admin = reactive({ name: '', email: '', password: '' })

const dbOptions = [
  { value: 'local', label: 'Set up PostGIS on this server (recommended)', desc: 'GeoDeploy installs and manages PostgreSQL + PostGIS for you.' },
  { value: 'external', label: 'Connect an existing PostGIS database', desc: 'Use a database you already manage.' },
]
const storageOptions = [
  { value: 'local', label: 'Use local storage on this server (recommended)', desc: 'GeoDeploy installs and manages MinIO (S3-compatible) for you.' },
  { value: 's3', label: 'Connect your own S3-compatible bucket', desc: 'AWS S3, Hetzner Object Storage, Cloudflare R2, Backblaze B2.' },
]

async function next() {
  error.value = ''
  busy.value = true
  try {
    if (step.value === 0) {
      await configureDB(db)
      step.value++
    } else if (step.value === 1) {
      await configureStorage(storage)
      step.value++
    } else {
      await createAdmin(admin)
      router.push('/login')
    }
  } catch (err) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    busy.value = false
  }
}
</script>
