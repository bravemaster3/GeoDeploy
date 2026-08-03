<template>
  <div class="min-h-screen bg-muted/40 flex items-center justify-center p-4">
    <div class="w-full max-w-lg">
      <div class="text-center mb-8">
        <h1 class="text-3xl font-bold text-foreground">GeoDeploy</h1>
        <p class="text-muted-foreground mt-1">Initial setup — takes about 2 minutes</p>
      </div>

      <!-- Step progress -->
      <div class="flex items-center justify-center gap-2 mb-8">
        <template v-for="(s, i) in steps" :key="i">
          <div class="w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold"
            :class="step > i ? 'bg-brand-600 text-white' : step === i ? 'bg-primary/15 text-primary ring-2 ring-primary/60' : 'bg-muted text-muted-foreground'"
          >{{ i + 1 }}</div>
          <div v-if="i < steps.length - 1" class="w-12 h-0.5" :class="step > i ? 'bg-brand-600' : 'bg-muted'" />
        </template>
      </div>

      <div class="card p-6 space-y-6">
        <!-- Step 0: Database -->
        <template v-if="step === 0">
          <h2 class="text-lg font-semibold">Database setup</h2>
          <div class="space-y-3">
            <label v-for="opt in dbOptions" :key="opt.value"
              class="flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors"
              :class="db.type === opt.value ? 'border-primary bg-primary/10' : 'border-border hover:border-muted-foreground/40'"
            >
              <input type="radio" v-model="db.type" :value="opt.value" class="mt-0.5" />
              <div>
                <div class="font-medium text-sm">{{ opt.label }}</div>
                <div class="text-xs text-muted-foreground">{{ opt.desc }}</div>
              </div>
            </label>
          </div>
          <template v-if="db.type === 'external'">
            <div class="grid grid-cols-2 gap-3">
              <div class="col-span-2"><label class="label">Host</label><input v-model="db.host" class="input" placeholder="localhost" /></div>
              <div><label class="label">Port</label><input v-model="db.port" type="number" class="input" /></div>
              <div><label class="label">Database</label><input v-model="db.db" class="input" /></div>
              <div><label class="label">User</label><input v-model="db.user" class="input" /></div>
              <div>
                <label class="label">Password</label>
                <!-- `new-password` is what actually stops autofill here. A browser sees a password
                     field on a host it has a saved credential for and silently substitutes it — the
                     dots look identical, so the operator submits someone else's password and reads
                     "password authentication failed" for a password they can see is correct. This
                     is not a login form: nothing saved for this host is ever the right value. -->
                <input v-model="db.password" type="password" class="input"
                  autocomplete="new-password" name="geodeploy-db-password" spellcheck="false" />
              </div>
            </div>
          </template>
        </template>

        <!-- Step 1: Storage -->
        <template v-else-if="step === 1">
          <h2 class="text-lg font-semibold">File storage setup</h2>
          <div class="space-y-3">
            <label v-for="opt in storageOptions" :key="opt.value"
              class="flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors"
              :class="storage.type === opt.value ? 'border-primary bg-primary/10' : 'border-border hover:border-muted-foreground/40'"
            >
              <input type="radio" v-model="storage.type" :value="opt.value" class="mt-0.5" />
              <div>
                <div class="font-medium text-sm">{{ opt.label }}</div>
                <div class="text-xs text-muted-foreground">{{ opt.desc }}</div>
              </div>
            </label>
          </div>
          <template v-if="storage.type !== 'local'">
            <div class="space-y-3">
              <div><label class="label">Endpoint URL</label><input v-model="storage.endpoint" class="input" placeholder="https://s3.amazonaws.com" /></div>
              <div><label class="label">Bucket</label><input v-model="storage.bucket" class="input" /></div>
              <div><label class="label">Access Key</label><input v-model="storage.access_key" class="input" /></div>
              <div>
                <label class="label">Secret Key</label>
                <input v-model="storage.secret_key" type="password" class="input"
                  autocomplete="new-password" name="geodeploy-storage-secret" spellcheck="false" />
              </div>
            </div>
          </template>
        </template>

        <!-- Step 2: Admin account -->
        <template v-else-if="step === 2">
          <h2 class="text-lg font-semibold">Create admin account</h2>
          <div class="space-y-3">
            <div><label class="label">Full name</label><input v-model="admin.name" class="input" /></div>
            <div><label class="label">Email</label><input v-model="admin.email" type="email" class="input" /></div>
            <div>
              <label class="label">Password</label>
              <input v-model="admin.password" type="password" class="input" minlength="8"
                autocomplete="new-password" name="geodeploy-new-admin-password" />
              <p class="text-xs text-muted-foreground mt-1">Minimum 8 characters</p>
            </div>
          </div>
        </template>

        <!-- Reconnected to an existing installation. A STATE, not a failure: the database already
             answered everything the remaining steps would have asked. -->
        <div v-if="reconnected" class="p-4 rounded-lg border border-primary/40 bg-primary/10 space-y-3">
          <p class="text-sm font-medium text-foreground">Reconnected to an existing GeoDeploy</p>
          <p class="text-xs text-muted-foreground leading-relaxed">
            This database already contains an installation
            <template v-if="reconnected.users">
              with {{ reconnected.users }} account{{ reconnected.users === 1 ? '' : 's' }}</template>.
            Its settings have been restored, so there is nothing left to set up — sign in with an
            existing account.
          </p>
          <p v-if="reconnected.storage_configured && reconnected.storage_secret_recovered"
            class="text-xs text-emerald-400">
            Storage reconnected: {{ reconnected.storage_bucket }} at {{ reconnected.storage_endpoint }}
          </p>
          <p v-else-if="reconnected.storage_configured" class="text-xs text-amber-300 leading-relaxed">
            Storage points at {{ reconnected.storage_bucket }}, but its secret key was encrypted with
            the previous install's <span class="font-mono">GEODEPLOY_SECRET_KEY</span> and cannot be
            read here. Put that key into <span class="font-mono">.env</span> and run setup again, or
            re-enter the storage credentials after signing in.
          </p>
          <button @click="router.push('/login')" class="btn-primary w-full justify-center">
            Go to sign in
          </button>

          <!-- The other legitimate intent: they wanted a FRESH install and reached for the wrong
               database. The credentials are already proven at this point, so the only thing missing
               is an empty database — which we can create on the same server rather than sending
               them to a psql prompt for one statement. -->
          <div class="pt-3 border-t border-border/60 space-y-2">
            <p class="text-xs text-muted-foreground">
              Wanted a fresh install instead? Create a new database on this same server:
            </p>
            <div class="flex gap-2">
              <input v-model="newDbName" class="input flex-1 text-sm" placeholder="geodeploy"
                @keydown.enter="createAndContinue" />
              <button @click="createAndContinue" :disabled="busy || !newDbName.trim()"
                class="btn-secondary text-sm px-3 disabled:opacity-60">
                {{ busy ? 'Creating…' : 'Create &amp; continue' }}
              </button>
            </div>
            <p class="text-[11px] text-muted-foreground/70">
              Letters, digits and underscores. PostGIS is enabled in it automatically.
            </p>
            <p v-if="newDbError" class="text-xs text-red-400">{{ newDbError }}</p>
          </div>
        </div>

        <!-- Error -->
        <div v-else-if="error" class="p-3 bg-red-500/15 border border-red-500/30 rounded-lg text-sm text-red-400">{{ error }}</div>

        <!-- Actions -->
        <div v-if="!reconnected" class="flex justify-between pt-2">
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
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { configureDB, configureStorage, createAdmin, getSetupStatus } from '@/api'

const router = useRouter()
const step = ref(0)
const busy = ref(false)
const error = ref('')
// Set when /configure-db reports the database already contains an installation.
const reconnected = ref(null)
const newDbName = ref('geodeploy')
const newDbError = ref('')

const steps = ['Database', 'Storage', 'Admin']

const db = reactive({ type: 'local', host: '', port: 5432, db: 'geodeploy', user: 'geodeploy', password: '' })
const storage = reactive({ type: 'local', endpoint: '', bucket: 'geodeploy', access_key: '', secret_key: '', region: 'us-east-1' })
const admin = reactive({ name: '', email: '', password: '' })

onMounted(async () => {
  try {
    const { data } = await getSetupStatus()
    if (data.admin_created) {
      router.push('/login')
    } else if (data.storage_configured) {
      step.value = 2
    } else if (data.postgis_configured) {
      step.value = 1
    }
  } catch {
    // Can't reach API — start at step 0
  }
})

// No "(recommended)" on either list. Which option is right depends on the person, not on us: someone
// who already runs PostGIS should obviously use it, and someone expecting a lot of data should
// obviously use S3. Labelling one choice as blessed makes the other look like a mistake, and pushed
// people onto local storage who then found they could not grow past their disk. Each option says
// WHO it is for, and lets the reader recognise themselves.
const dbOptions = [
  { value: 'local', label: 'Let GeoDeploy set up PostGIS',
    desc: 'Installs and manages PostgreSQL + PostGIS on this server. Nothing to configure, and it is backed up with everything else. Choose this if you do not already run a spatial database.' },
  { value: 'external', label: 'Connect a PostGIS database you already have',
    desc: 'Point GeoDeploy at an existing database — your own server, or a managed one. Choose this if you already run PostGIS, or want the database separate from this machine.' },
]
const storageOptions = [
  { value: 'local', label: 'Store files on this server',
    desc: 'Installs and manages MinIO here. Fast and free, but limited by the disk on this machine — growing later means attaching a bigger volume. Good for a modest amount of data.' },
  { value: 's3', label: 'Use S3-compatible object storage',
    desc: 'AWS S3, Hetzner Object Storage, Cloudflare R2, Backblaze B2. Grows on demand and is billed by what you use — the better choice if you expect many layers or large rasters.' },
]

// Create a new database on the server just validated, and carry on with the normal wizard.
async function createAndContinue() {
  newDbError.value = ''
  busy.value = true
  try {
    const { data } = await configureDB({ ...db, create_database: newDbName.value.trim() })
    if (data?.existing_install) {
      // Only possible if the NEW name also already holds an installation.
      reconnected.value = data.existing_install
      return
    }
    reconnected.value = null
    step.value = 1
  } catch (err) {
    const d = err.response?.data?.detail
    newDbError.value = (typeof d === 'string' ? d : d?.message) || err.message
  } finally {
    busy.value = false
  }
}

async function next() {
  error.value = ''
  busy.value = true
  try {
    if (step.value === 0) {
      const { data } = await configureDB(db)
      // The database already holds an installation. There is nothing left to configure — its own
      // settings have just been written back into .env — so stop asking and send them to sign in.
      // Continuing would walk into the storage step, which the setup guard refuses because an admin
      // exists, leaving a red error on a step that never had a problem.
      if (data?.existing_install) {
        reconnected.value = data.existing_install
        return
      }
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
