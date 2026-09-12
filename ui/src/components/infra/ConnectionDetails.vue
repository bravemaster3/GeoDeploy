<template>
  <!-- Owner-only. Never fetched until asked for: the request itself is audited, so loading it on
       every visit to Settings would fill the log with views nobody performed. -->
  <section class="card overflow-hidden">
    <header class="px-5 py-3.5 border-b border-border/60 flex items-center gap-3 flex-wrap">
      <div class="flex-1 min-w-0">
        <h2 class="font-semibold text-sm">Connection details</h2>
        <p class="text-xs text-muted-foreground/70">
          Credentials for the database and object storage GeoDeploy set up for you.
        </p>
      </div>
      <button v-if="!loaded" @click="load" :disabled="busy" class="btn-secondary text-xs px-3 py-1.5">
        {{ busy ? 'Loading…' : 'Show' }}
      </button>
    </header>

    <div v-if="error" class="px-5 py-4 text-xs text-red-400">{{ error }}</div>

    <div v-else-if="loaded" class="p-5 space-y-5">
      <p class="text-[11px] text-amber-300/90 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2">
        These are the keys to your data. Anyone who has them can read and change everything in this
        instance — treat them like a password, and prefer copying to reading them aloud.
      </p>

      <div v-for="group in groups" :key="group.title">
        <h3 class="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
          {{ group.title }}
          <span v-if="!group.managed" class="ml-1 normal-case font-normal text-muted-foreground/60">
            (external — you provided this)
          </span>
        </h3>
        <!-- A MANAGED database's host and port are the INTERNAL Docker network address. They are
             correct, and they are also not dialable from anyone's laptop — so a panel that shows
             `postgres:5432` beside a password, and says nothing else, invites an operator to try a
             connection that cannot work and to conclude their credentials are wrong. External
             databases need no such note: those values are the ones the operator gave us, and they
             are reachable wherever they already were. -->
        <p v-if="group.internalOnly" class="text-[11px] text-muted-foreground/70 mb-2 leading-relaxed">
          This is the address <em>inside</em> GeoDeploy's own Docker network — the database is not
          published to this machine or to the internet, so nothing outside can connect to it yet.
          To reach it from QGIS, psql or DBeaver, see
          <a href="https://docs-geodeploy.kndev.org/data-access/#connecting-straight-to-the-database"
             target="_blank" rel="noopener" class="underline hover:text-foreground">Connecting straight to the database</a>.
        </p>
        <div class="space-y-1.5">
          <div v-for="f in group.fields" :key="f.label"
            class="flex items-center gap-2 text-xs">
            <span class="w-24 flex-shrink-0 text-muted-foreground">{{ f.label }}</span>
            <input readonly :value="display(f)"
              :type="f.secret && !revealed[f.label] ? 'password' : 'text'"
              class="flex-1 min-w-0 font-mono bg-background text-foreground border border-border rounded-lg px-2.5 py-1.5 focus:outline-none" />
            <button v-if="f.secret" @click="revealed[f.label] = !revealed[f.label]"
              :title="revealed[f.label] ? 'Hide' : 'Show'" :aria-label="revealed[f.label] ? 'Hide' : 'Show'"
              class="flex-shrink-0 w-7 h-7 rounded-md flex items-center justify-center text-muted-foreground/70 hover:text-foreground hover:bg-muted/60">
              <svg v-if="!revealed[f.label]" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" /><circle cx="12" cy="12" r="3" />
              </svg>
              <svg v-else class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M17.9 17.9A10.4 10.4 0 0 1 12 19c-6.5 0-10-7-10-7a18 18 0 0 1 5.1-5.9m3.2-1A10.4 10.4 0 0 1 12 5c6.5 0 10 7 10 7a18 18 0 0 1-2.2 3.2M1 1l22 22" />
              </svg>
            </button>
            <button @click="copy(f)" :title="copied === f.label ? 'Copied' : 'Copy'" aria-label="Copy"
              class="flex-shrink-0 w-7 h-7 rounded-md flex items-center justify-center"
              :class="copied === f.label ? 'text-green-400' : 'text-muted-foreground/70 hover:text-foreground hover:bg-muted/60'">
              <svg v-if="copied !== f.label" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" />
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
              </svg>
              <svg v-else class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
            </button>
          </div>
        </div>
      </div>

      <p class="text-[11px] text-muted-foreground/70 leading-snug">
        The hosts shown are the ones GeoDeploy uses internally. On a default install they are container
        names, reachable from this server but not from the internet — which is why the database is not
        exposed publicly.
      </p>
    </div>
  </section>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { getConnectionDetails } from '@/api'

const loaded = ref(false)
const busy = ref(false)
const error = ref('')
const data = ref(null)
const revealed = reactive({})
const copied = ref('')

const groups = computed(() => {
  if (!data.value) return []
  const d = data.value.database, s = data.value.storage
  return [
    {
      // `internalOnly` is not the same as `managed`, even though today they coincide: a managed
      // database becomes reachable the moment docker-compose.db-port.yml is enabled, and this note
      // should stop being shown then. Named for the property it describes so that stays possible.
      title: 'Database (PostGIS)', managed: d.managed, internalOnly: d.managed && !d.published,
      fields: [
        { label: 'Host', value: d.host }, { label: 'Port', value: String(d.port ?? '') },
        { label: 'Database', value: d.database }, { label: 'User', value: d.user },
        { label: 'Password', value: d.password, secret: true },
      ],
    },
    {
      title: 'Object storage (S3)', managed: s.managed,
      fields: [
        { label: 'Endpoint', value: s.endpoint }, { label: 'Bucket', value: s.bucket },
        { label: 'Region', value: s.region },
        { label: 'Access key', value: s.access_key, secret: true },
        { label: 'Secret key', value: s.secret_key, secret: true },
      ],
    },
  ]
})

// A masked field still shows SOMETHING of the right length, so an empty value reads as empty rather
// than as a secret you failed to reveal.
const display = (f) => f.value || ''

async function load() {
  busy.value = true
  error.value = ''
  try {
    data.value = (await getConnectionDetails()).data
    loaded.value = true
  } catch (e) {
    error.value = e.response?.data?.detail || 'Could not read the connection details.'
  } finally { busy.value = false }
}

async function copy(f) {
  try {
    await navigator.clipboard.writeText(f.value || '')
    copied.value = f.label
    setTimeout(() => { if (copied.value === f.label) copied.value = '' }, 1400)
  } catch { /* clipboard blocked — the field is selectable as a fallback */ }
}
</script>
