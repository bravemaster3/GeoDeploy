<template>
  <div class="p-6 lg:p-8">
    <div class="max-w-4xl mx-auto space-y-6">
      <div>
        <h1 class="text-2xl font-semibold tracking-tight text-foreground">Settings</h1>
        <p class="text-sm text-muted-foreground mt-1">Manage infrastructure, storage, and your account.</p>
      </div>

      <!-- Tabs -->
      <nav class="flex flex-wrap gap-1 border-b border-border">
        <button v-for="t in tabs" :key="t.id" type="button" @click="activeTab = t.id"
          class="px-3 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap transition-colors"
          :class="activeTab === t.id ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'">
          {{ t.label }}
        </button>
      </nav>

      <!-- Infrastructure + Storage tab (admin) -->
      <div v-if="activeTab === 'infra'" class="space-y-6">
      <!-- Software updates (admin) — read-only version check against GitHub main -->
      <section v-if="auth.isAdmin" class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <span class="w-9 h-9 rounded-lg bg-emerald-500/15 text-emerald-400 flex items-center justify-center flex-shrink-0">
            <RefreshIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-foreground">Updates</h2>
            <p class="text-xs text-muted-foreground/70">Software version &amp; available updates</p>
          </div>
          <button @click="checkUpdates" :disabled="updates.loading" class="btn-secondary text-xs px-3 py-1.5">
            <RefreshIcon class="w-3.5 h-3.5" /> {{ updates.loading ? 'Checking…' : 'Check' }}
          </button>
        </header>
        <div class="p-5 space-y-3">
          <div v-if="updates.loading && !updates.data" class="text-sm text-muted-foreground/70">Checking for updates…</div>
          <template v-else-if="updates.data">
            <div class="text-sm font-medium">
              <span v-if="updates.data.up_to_date === true" class="text-green-400">✓ Up to date</span>
              <span v-else-if="updates.data.behind > 0" class="text-amber-400">
                Update available — {{ updates.data.behind }} commit{{ updates.data.behind === 1 ? '' : 's' }} behind
              </span>
              <span v-else-if="updates.data.update_available" class="text-amber-400">
                Update available — a newer version is on GitHub{{ updates.data.latest ? ' (' + updates.data.latest + ')' : '' }}
              </span>
              <span v-else-if="updates.data.status === 'offline'" class="text-muted-foreground/80">Couldn't reach GitHub to check for updates.</span>
              <span v-else class="text-muted-foreground/80">Version check unavailable.</span>
            </div>
            <div class="text-xs text-muted-foreground/80 font-mono flex flex-wrap gap-x-5 gap-y-1">
              <span>Running <b class="text-foreground">{{ updates.data.current }}</b></span>
              <span v-if="updates.data.latest">Latest <b class="text-foreground">{{ updates.data.latest }}</b></span>
            </div>
            <div v-if="updates.data.commits && updates.data.commits.length"
                 class="rounded-lg border border-border/60 bg-muted/20 divide-y divide-border/40 max-h-52 overflow-y-auto">
              <div v-for="c in updates.data.commits" :key="c.sha" class="px-3 py-1.5 text-xs flex gap-2">
                <span class="font-mono text-muted-foreground/60 flex-shrink-0">{{ c.sha }}</span>
                <span class="text-foreground/85 truncate">{{ c.message }}</span>
              </div>
            </div>
            <div v-if="updates.data.behind > 0 || updates.data.update_available" class="space-y-2">
              <button @click="startUpdate" :disabled="updates.updating"
                      class="text-xs font-semibold px-3.5 py-2 rounded-md bg-primary text-primary-foreground hover:brightness-110 disabled:opacity-50">
                {{ updates.updating ? 'Updating…' : 'Update now' }}
              </button>
              <p class="text-[11px] text-muted-foreground/70">
                Builds the new version, restarts services, and health-checks — <b>rolls back automatically</b> if it comes up unhealthy.
                Or run manually: <code class="font-mono bg-muted/40 rounded px-1.5 py-0.5 select-all">{{ updates.data.update_command }}</code>
              </p>
            </div>
            <!-- Live update progress -->
            <div v-if="updates.progress" class="text-xs rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <span class="font-semibold uppercase tracking-wide" :class="updatePhaseClass(updates.progress.phase)">{{ updates.progress.phase }}</span>
              <span class="text-foreground/85"> — {{ updates.progress.message }}</span>
            </div>
          </template>
          <div v-else class="text-sm text-muted-foreground/70">Version check unavailable.</div>
        </div>
      </section>

      <!-- Infrastructure health (admin/owner — service control is require_admin server-side) -->
      <section v-if="auth.isAdmin" class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <span class="w-9 h-9 rounded-lg bg-indigo-500/15 text-indigo-400 flex items-center justify-center flex-shrink-0">
            <ServerIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-foreground">Infrastructure</h2>
            <p class="text-xs text-muted-foreground/70">Container health &amp; controls</p>
          </div>
          <button @click="systemStore.refreshHealth()" class="btn-secondary text-xs px-3 py-1.5">
            <RefreshIcon class="w-3.5 h-3.5" /> Refresh
          </button>
          <button @click="reloadMartin" :disabled="martinBusy" class="btn-secondary text-xs px-3 py-1.5">
            {{ martinBusy ? 'Reloading…' : 'Reload Martin' }}
          </button>
        </header>
        <div class="p-2">
          <div v-if="!systemStore.health.length" class="px-3 py-6 text-sm text-muted-foreground/70 text-center">Loading…</div>
          <div v-for="svc in systemStore.health" :key="svc.name"
            class="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-muted/60">
            <span class="w-2 h-2 rounded-full flex-shrink-0" :class="dotClass(svc.status)" />
            <span class="text-sm font-medium text-foreground/85 capitalize flex-1 min-w-0 truncate">{{ svc.name }}</span>
            <span class="inline-flex items-center text-[11px] font-medium px-2 py-0.5 rounded-full" :class="pillClass(svc.status)">
              {{ svc.status }}
            </span>
            <div v-if="svc.controllable" class="flex items-center gap-1 w-16 justify-end">
              <button v-if="!['running','healthy'].includes(svc.status)"
                @click="svcAction(svc.name, 'start')" :disabled="busySvc === svc.name"
                class="svc-btn text-green-400" title="Start">▶</button>
              <button v-else @click="svcAction(svc.name, 'stop')" :disabled="busySvc === svc.name"
                class="svc-btn text-red-500" title="Stop">■</button>
              <button @click="svcAction(svc.name, 'restart')" :disabled="busySvc === svc.name"
                class="svc-btn text-muted-foreground" title="Restart">↻</button>
            </div>
            <div v-else class="w-16" />
          </div>
          <p v-if="martinMsg" class="px-3 pt-1 text-xs" :class="martinMsg.ok ? 'text-green-400' : 'text-red-400'">
            {{ martinMsg.text }}
          </p>
        </div>
      </section>

      <!-- Logs (admin) — read-only container output; the safe alternative to a shell -->
      <section v-if="auth.isAdmin" class="card overflow-hidden">
        <header class="flex flex-wrap items-center gap-2 px-5 py-3.5 border-b border-border/60">
          <span class="w-9 h-9 rounded-lg bg-sky-500/15 text-sky-400 flex items-center justify-center flex-shrink-0">
            <ServerIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-foreground">Logs</h2>
            <p class="text-xs text-muted-foreground/70">Recent container output (read-only)</p>
          </div>
          <select v-model="logs.service" @change="fetchLogs"
                  class="text-xs rounded-md border border-border bg-background text-foreground px-2 py-1.5">
            <option v-for="s in LOG_SERVICES" :key="s" :value="s">{{ s }}</option>
          </select>
          <select v-model.number="logs.tail" @change="fetchLogs"
                  class="text-xs rounded-md border border-border bg-background text-foreground px-2 py-1.5">
            <option :value="100">100</option>
            <option :value="200">200</option>
            <option :value="500">500</option>
            <option :value="2000">2000</option>
          </select>
          <button @click="fetchLogs" :disabled="logs.loading" class="btn-secondary text-xs px-3 py-1.5">
            <RefreshIcon class="w-3.5 h-3.5" /> {{ logs.loading ? 'Loading…' : 'Refresh' }}
          </button>
        </header>
        <div class="p-2">
          <pre class="text-[11px] leading-relaxed font-mono whitespace-pre-wrap break-words max-h-96 overflow-auto rounded-lg bg-[#0b0f14] text-slate-200 p-3 m-0">{{ logs.text || 'Choose a service and press Refresh.' }}</pre>
        </div>
      </section>

      <!-- Storage (admin/owner — storage-stats is require_admin server-side) -->
      <section v-if="auth.isAdmin && systemStore.stats" class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <span class="w-9 h-9 rounded-lg bg-amber-500/15 text-amber-400 flex items-center justify-center flex-shrink-0">
            <HardDriveIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-foreground">Storage</h2>
            <p class="text-xs text-muted-foreground/70">Usage across your data</p>
          </div>
        </header>
        <div class="p-5 space-y-4">
          <!-- Total + stacked proportion bar (PostGIS / rasters / GeoParquet / portal pages) -->
          <div>
            <div class="flex justify-between text-xs text-muted-foreground mb-1">
              <span>Data storage</span>
              <span class="font-semibold text-foreground">{{ formatBytes(systemStore.stats.used_bytes) }} total</span>
            </div>
            <div class="h-2 bg-muted rounded-full overflow-hidden flex">
              <div v-for="seg in storageSegments" :key="seg.label" :class="seg.bar"
                :style="{ width: seg.pct + '%' }" :title="`${seg.label}: ${formatBytes(seg.bytes)}`" />
            </div>
          </div>
          <!-- Per-store breakdown ('—' = that store couldn't be measured, e.g. DB unreachable) -->
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div v-for="seg in storageTiles" :key="seg.label" class="rounded-lg border border-border/60 bg-muted/40 p-3">
              <div class="flex items-center gap-1.5 text-xs text-muted-foreground">
                <span class="w-2 h-2 rounded-full flex-shrink-0" :class="seg.dot" />{{ seg.label }}
              </div>
              <div class="text-lg font-bold text-foreground mt-0.5">
                {{ seg.bytes === null ? '—' : formatBytes(seg.bytes) }}
              </div>
            </div>
          </div>
          <div class="grid grid-cols-3 gap-3">
            <div v-for="tile in statTiles" :key="tile.label" class="rounded-lg border border-border/60 bg-muted/40 p-4 text-center">
              <div class="text-2xl font-bold text-foreground">{{ tile.value }}</div>
              <div class="text-xs text-muted-foreground mt-0.5">{{ tile.label }}</div>
            </div>
          </div>
        </div>
      </section>

      <!-- Danger Zone (admin) — the opt-in, gated in-container terminal -->
      <section v-if="auth.isAdmin" class="rounded-xl border border-red-500/40 bg-red-500/[0.03] overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-red-500/30">
          <span class="w-9 h-9 rounded-lg bg-red-500/15 text-red-400 flex items-center justify-center flex-shrink-0">
            <TrashIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-red-400">Danger Zone</h2>
            <p class="text-xs text-muted-foreground/70">Powerful tools — use with care.</p>
          </div>
        </header>
        <div class="p-5 space-y-3">
          <div>
            <h3 class="text-sm font-semibold text-foreground">Container terminal</h3>
            <p class="text-xs text-muted-foreground/70 mt-0.5">
              Run a shell command inside a service container (e.g. <code class="font-mono">psql -c "…"</code>,
              <code class="font-mono">redis-cli INFO</code>). <b>Off by default</b> — enable with
              <code class="font-mono">GEODEPLOY_ENABLE_TERMINAL=true</code> in <code class="font-mono">.env</code>, then redeploy.
              It never touches the host or the api/celery containers, and every command is audited.
            </p>
          </div>
          <div class="flex flex-wrap gap-2">
            <select v-model="term.service"
                    class="text-xs rounded-md border border-border bg-background text-foreground px-2 py-1.5">
              <option v-for="s in TERMINAL_SERVICES" :key="s" :value="s">{{ s }}</option>
            </select>
            <input v-model="term.command" @keyup.enter="runExec" spellcheck="false"
                   placeholder='e.g. redis-cli INFO server'
                   class="flex-1 min-w-[12rem] text-xs font-mono rounded-md border border-border bg-background text-foreground px-2.5 py-1.5" />
            <button @click="runExec" :disabled="term.loading || !term.command.trim()"
                    class="text-xs px-3 py-1.5 rounded-md border border-red-500/50 text-red-400 hover:bg-red-500/10 disabled:opacity-50">
              {{ term.loading ? 'Running…' : 'Run' }}
            </button>
          </div>
          <pre v-if="term.output !== null"
               class="text-[11px] leading-relaxed font-mono whitespace-pre-wrap break-words max-h-80 overflow-auto rounded-lg bg-[#0b0f14] text-slate-200 p-3 m-0">{{ term.output || '(no output)' }}</pre>
        </div>
      </section>
      </div>

      <!-- Email tab (admin) -->
      <!-- Backups: destination + schedule + history -->
      <div v-if="activeTab === 'backups'" class="space-y-6">
        <section class="card overflow-hidden">
          <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
            <span class="w-9 h-9 rounded-lg bg-emerald-500/15 text-emerald-400 flex items-center justify-center flex-shrink-0">
              <HardDriveIcon class="w-5 h-5" />
            </span>
            <div class="min-w-0">
              <h2 class="font-semibold">Backup destination</h2>
              <p class="text-xs text-muted-foreground">
                A separate object store for PostGIS, your files, and this instance's database.
              </p>
            </div>
            <label class="ml-auto flex items-center gap-2 text-sm">
              <input type="checkbox" v-model="bk.enabled" class="w-4 h-4" /> Enabled
            </label>
          </header>

          <div class="p-5 space-y-4">
            <p class="text-xs text-amber-300/90 bg-amber-500/10 border border-amber-400/30 rounded-lg p-3">
              Use a <strong>different bucket from your data</strong> &mdash; ideally a different
              provider. A copy that dies with the original is not a backup, so the test below
              refuses a destination pointing at your live bucket.
            </p>
            <div class="grid md:grid-cols-2 gap-3">
              <div>
                <label class="text-xs text-muted-foreground block mb-1">Endpoint <span class="text-muted-foreground/60">(blank = AWS S3)</span></label>
                <input v-model="bk.endpoint" class="input w-full text-sm" placeholder="https://s3.eu-central-1.wasabisys.com" />
              </div>
              <div>
                <label class="text-xs text-muted-foreground block mb-1">Bucket</label>
                <input v-model="bk.bucket" class="input w-full text-sm" placeholder="geodeploy-backups" />
              </div>
              <div>
                <label class="text-xs text-muted-foreground block mb-1">Access key</label>
                <input v-model="bk.access_key" class="input w-full text-sm" autocomplete="off" />
              </div>
              <div>
                <label class="text-xs text-muted-foreground block mb-1">
                  Secret key <span v-if="bk.secret_set" class="text-emerald-400">&middot; stored</span>
                </label>
                <input v-model="bk.secret_key" type="password" class="input w-full text-sm"
                  autocomplete="new-password" :placeholder="bk.secret_set ? 'Leave blank to keep' : ''" />
              </div>
              <div>
                <label class="text-xs text-muted-foreground block mb-1">Region</label>
                <input v-model="bk.region" class="input w-full text-sm" placeholder="us-east-1" />
              </div>
              <div>
                <label class="text-xs text-muted-foreground block mb-1">Path prefix</label>
                <input v-model="bk.prefix" class="input w-full text-sm" placeholder="geodeploy-backups" />
              </div>
            </div>

            <div class="grid md:grid-cols-3 gap-3 pt-3 border-t border-border/60">
              <div>
                <label class="text-xs text-muted-foreground block mb-1">Schedule</label>
                <select v-model="bk.schedule" class="input w-full text-sm">
                  <option value="off">Manual only</option>
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly (Mondays)</option>
                </select>
              </div>
              <div>
                <label class="text-xs text-muted-foreground block mb-1">At (UTC hour)</label>
                <select v-model.number="bk.hour" class="input w-full text-sm" :disabled="bk.schedule === 'off'">
                  <option v-for="h in 24" :key="h - 1" :value="h - 1">{{ String(h - 1).padStart(2, '0') }}:00</option>
                </select>
              </div>
              <div>
                <label class="text-xs text-muted-foreground block mb-1">Keep last</label>
                <input v-model.number="bk.keep" type="number" min="1" max="365" class="input w-full text-sm" />
              </div>
            </div>

            <div class="flex flex-wrap gap-4">
              <label class="flex items-center gap-2 text-sm"><input type="checkbox" v-model="bk.include_postgis" class="w-4 h-4" /> PostGIS database</label>
              <label class="flex items-center gap-2 text-sm"><input type="checkbox" v-model="bk.include_objects" class="w-4 h-4" /> Files (rasters, GeoParquet, tiles)</label>
              <label class="flex items-center gap-2 text-sm"><input type="checkbox" v-model="bk.include_state" class="w-4 h-4" /> Instance database &amp; portal assets</label>
            </div>

            <div class="flex items-center gap-3 flex-wrap">
              <button @click="saveBackups" :disabled="bkSaving" class="btn-primary text-sm px-4 py-2 disabled:opacity-60">
                {{ bkSaving ? 'Saving...' : 'Save' }}
              </button>
              <button @click="testBackups" :disabled="bkTesting" class="btn-secondary text-sm px-4 py-2 disabled:opacity-60">
                {{ bkTesting ? 'Testing...' : 'Test destination' }}
              </button>
              <button @click="runBackup" :disabled="bkRunning || !bk.enabled" class="btn-secondary text-sm px-4 py-2 disabled:opacity-60">
                Back up now
              </button>
              <span v-if="bkMsg" class="text-xs" :class="bkMsg.ok ? 'text-emerald-400' : 'text-red-400'">{{ bkMsg.text }}</span>
            </div>
          </div>
        </section>

        <section class="card overflow-hidden">
          <header class="px-5 py-3.5 border-b border-border/60">
            <h2 class="font-semibold">History</h2>
            <p class="text-xs text-muted-foreground">Every run, successful or not.</p>
          </header>
          <div v-if="!bkRuns.length" class="px-5 py-8 text-center text-sm text-muted-foreground/70">
            No backups yet.
          </div>
          <table v-else class="w-full text-sm">
            <tbody>
              <tr v-for="r in bkRuns" :key="r.id" class="border-b border-border/40 last:border-0">
                <td class="px-5 py-2.5 whitespace-nowrap">
                  <span class="text-[11px] font-mono px-1.5 py-0.5 rounded"
                    :class="r.status === 'success' ? 'bg-emerald-500/15 text-emerald-400'
                      : r.status === 'error' ? 'bg-red-500/15 text-red-400' : 'bg-amber-500/15 text-amber-400'">{{ r.status }}</span>
                </td>
                <td class="px-2 py-2.5 text-xs text-muted-foreground whitespace-nowrap">{{ new Date(r.started_at).toLocaleString() }}</td>
                <td class="px-2 py-2.5 text-xs text-muted-foreground">{{ r.trigger }}</td>
                <td class="px-2 py-2.5 text-xs text-muted-foreground">
                  <span v-if="r.status === 'running'">{{ r.current_step }} &middot; {{ r.progress }}%</span>
                  <span v-else-if="r.status === 'error'" class="text-red-400">{{ r.error_message }}</span>
                  <span v-else>{{ fmtBytes(r.size_bytes) }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      <div v-if="activeTab === 'email'" class="space-y-6">
      <!-- Outgoing email (generic SMTP — admin/owner) -->
      <section v-if="auth.isAdmin" class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <span class="w-9 h-9 rounded-lg bg-sky-500/15 text-sky-400 flex items-center justify-center flex-shrink-0">
            <MailIcon class="w-5 h-5" />
          </span>
          <div class="flex-1 min-w-0">
            <h2 class="text-sm font-semibold text-foreground">Email</h2>
            <p class="text-xs text-muted-foreground/70">Invites, password resets — any SMTP provider</p>
          </div>
          <span v-if="emailForm" class="text-[11px] font-medium px-2 py-0.5 rounded-full"
            :class="emailConfigured ? 'bg-green-500/15 text-green-400' : 'bg-muted text-muted-foreground'">
            {{ emailConfigured ? 'configured' : 'not configured' }}
          </span>
        </header>
        <div v-if="emailForm" class="p-5 space-y-4">
          <p class="text-xs text-muted-foreground">
            Optional — without it, invite and reset links are copy-and-send. Works with any provider:
            <span class="font-medium text-foreground/80">Resend</span> (host <code class="font-mono">smtp.resend.com</code>,
            port 465 TLS, user <code class="font-mono">resend</code>, password = API key) ·
            <span class="font-medium text-foreground/80">Brevo</span> (host <code class="font-mono">smtp-relay.brevo.com</code>,
            port 587 STARTTLS, ~300 free emails/day) · or your organisation's mail server.
          </p>
          <div class="grid gap-x-6 gap-y-3 lg:grid-cols-2">
            <!-- Left: server connection -->
            <div class="space-y-3">
              <div class="grid grid-cols-3 gap-3">
                <div class="col-span-2">
                  <label class="text-xs text-muted-foreground block mb-1">SMTP host</label>
                  <input v-model="emailForm.smtp_host" type="text" placeholder="smtp.example.com" class="input w-full text-sm font-mono" />
                </div>
                <div>
                  <label class="text-xs text-muted-foreground block mb-1">Port</label>
                  <input v-model.number="emailForm.smtp_port" type="number" class="input w-full text-sm" />
                </div>
              </div>
              <div>
                <label class="text-xs text-muted-foreground block mb-1">Security</label>
                <div class="grid grid-cols-3 gap-2">
                  <button v-for="s in ['starttls', 'tls', 'none']" :key="s" type="button"
                    class="p-2 rounded-lg border text-xs font-medium transition-colors"
                    :class="emailForm.smtp_security === s ? 'border-primary bg-primary/10 text-primary' : 'border-border hover:border-muted-foreground/40 text-foreground/85'"
                    @click="emailForm.smtp_security = s">{{ s === 'tls' ? 'TLS (465)' : s === 'starttls' ? 'STARTTLS (587)' : 'None' }}</button>
                </div>
              </div>
            </div>
            <!-- Right: credentials + sender -->
            <div class="space-y-3">
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="text-xs text-muted-foreground block mb-1">Username</label>
                  <input v-model="emailForm.smtp_username" type="text" class="input w-full text-sm font-mono" />
                </div>
                <div>
                  <label class="text-xs text-muted-foreground block mb-1">
                    Password {{ emailHasPassword ? '(saved — blank keeps it)' : '' }}
                  </label>
                  <input v-model="emailForm.smtp_password" type="password" autocomplete="new-password" class="input w-full text-sm" />
                </div>
              </div>
              <div>
                <label class="text-xs text-muted-foreground block mb-1">From address</label>
                <input v-model="emailForm.email_from" type="email" placeholder="geodeploy@your-domain.org" class="input w-full text-sm" />
              </div>
            </div>
          </div>
          <p v-if="emailMsg" class="text-xs" :class="emailMsg.ok ? 'text-green-400' : 'text-red-400'">{{ emailMsg.text }}</p>
          <div class="flex gap-2">
            <button @click="saveEmail" :disabled="emailBusy" class="btn-primary text-xs px-3 py-1.5">
              {{ emailBusy === 'save' ? 'Saving…' : 'Save' }}
            </button>
            <button @click="testEmail" :disabled="emailBusy || !emailConfigured" class="btn-secondary text-xs px-3 py-1.5"
              title="Sends a test email to your own address">
              {{ emailBusy === 'test' ? 'Sending…' : 'Send test email' }}
            </button>
          </div>
        </div>
      </section>
      </div>

      <!-- Authentication (SSO) tab (admin) -->
      <div v-if="activeTab === 'auth'" class="space-y-6">
        <section class="card overflow-hidden">
          <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
            <span class="w-9 h-9 rounded-lg bg-teal-500/15 text-teal-400 flex items-center justify-center flex-shrink-0">
              <KeyIcon class="w-5 h-5" />
            </span>
            <div class="flex-1 min-w-0">
              <h2 class="text-sm font-semibold text-foreground">Single sign-on (OIDC)</h2>
              <p class="text-xs text-muted-foreground/70">Let members sign in with your identity provider</p>
            </div>
            <span v-if="oidcForm" class="text-[11px] font-medium px-2 py-0.5 rounded-full"
              :class="oidcForm.oidc_enabled ? 'bg-green-500/15 text-green-400' : 'bg-muted text-muted-foreground'">
              {{ oidcForm.oidc_enabled ? 'enabled' : 'disabled' }}
            </span>
          </header>
          <div v-if="oidcForm" class="p-5 space-y-4">
            <p class="text-xs text-muted-foreground">
              Generic OpenID Connect (Google, Microsoft, Keycloak, Authentik, an institutional IdP…).
              Register this <span class="font-medium text-foreground/80">redirect URI</span> with your provider:
            </p>
            <input :value="oidcRedirectUri" readonly class="input w-full text-xs font-mono"
              @focus="$event.target.select()" />
            <label class="flex items-center gap-2 text-sm">
              <input type="checkbox" v-model="oidcForm.oidc_enabled" class="accent-primary" />
              <span class="text-foreground/85">Enable single sign-on</span>
            </label>
            <div class="grid gap-x-6 gap-y-3 lg:grid-cols-2">
              <div>
                <label class="text-xs text-muted-foreground block mb-1">Issuer URL (discovery)</label>
                <input v-model="oidcForm.oidc_issuer" placeholder="https://accounts.google.com" class="input w-full text-sm font-mono" />
              </div>
              <div>
                <label class="text-xs text-muted-foreground block mb-1">Button label</label>
                <input v-model="oidcForm.oidc_label" placeholder="Sign in with Google" class="input w-full text-sm" />
              </div>
              <div>
                <label class="text-xs text-muted-foreground block mb-1">Client ID</label>
                <input v-model="oidcForm.oidc_client_id" class="input w-full text-sm font-mono" />
              </div>
              <div>
                <label class="text-xs text-muted-foreground block mb-1">
                  Client secret {{ oidcHasSecret ? '(saved — blank keeps it)' : '' }}
                </label>
                <input v-model="oidcForm.oidc_client_secret" type="password" autocomplete="new-password" class="input w-full text-sm" />
              </div>
            </div>
            <div class="border-t border-border/60 pt-3 space-y-3">
              <label class="flex items-center gap-2 text-sm">
                <input type="checkbox" v-model="oidcForm.oidc_auto_provision" class="accent-primary" />
                <span class="text-foreground/85">Auto-create accounts on first sign-in</span>
              </label>
              <p class="text-[11px] text-muted-foreground/70 -mt-1">
                Off (recommended): only people you've already invited can sign in via SSO. On: anyone
                whose email domain is allow-listed gets an account with the default role.
              </p>
              <div v-if="oidcForm.oidc_auto_provision" class="grid gap-x-6 gap-y-3 lg:grid-cols-2">
                <div>
                  <label class="text-xs text-muted-foreground block mb-1">Allowed email domains (comma-separated)</label>
                  <input v-model="oidcForm.oidc_allowed_domains" placeholder="example.org, dept.example.org" class="input w-full text-sm font-mono" />
                </div>
                <div>
                  <label class="text-xs text-muted-foreground block mb-1">Default role for new accounts</label>
                  <select v-model="oidcForm.oidc_default_role" class="input w-full text-sm">
                    <option value="viewer">viewer</option>
                    <option value="editor">editor</option>
                    <option value="admin">admin</option>
                  </select>
                </div>
              </div>
            </div>
            <p v-if="oidcMsg" class="text-xs" :class="oidcMsg.ok ? 'text-green-400' : 'text-red-400'">{{ oidcMsg.text }}</p>
            <button @click="saveOidc" :disabled="oidcBusy" class="btn-primary text-xs px-3 py-1.5">
              {{ oidcBusy ? 'Saving…' : 'Save' }}
            </button>
          </div>
          <div v-else class="p-5 text-sm text-muted-foreground/70">Loading…</div>
        </section>
      </div>

      <!-- Account tab (everyone) -->
      <div v-if="activeTab === 'account'" class="space-y-6">
      <!-- Account -->
      <section class="card overflow-hidden">
        <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
          <span class="w-9 h-9 rounded-lg bg-primary/10 text-primary flex items-center justify-center flex-shrink-0">
            <UserIcon class="w-5 h-5" />
          </span>
          <h2 class="text-sm font-semibold text-foreground">Account</h2>
        </header>
        <div class="p-5 flex items-center gap-4">
          <span class="w-12 h-12 rounded-full bg-primary/10 text-primary flex items-center justify-center font-semibold flex-shrink-0">
            {{ initials }}
          </span>
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 min-w-0">
              <span class="text-sm font-medium text-foreground truncate">{{ auth.user?.name }}</span>
              <span v-if="auth.role" class="text-[10px] px-1.5 py-0.5 rounded font-medium flex-shrink-0" :class="roleBadge">
                {{ auth.role }}
              </span>
            </div>
            <div class="text-sm text-muted-foreground/70 truncate">{{ auth.user?.email }}</div>
          </div>
          <button @click="showPwForm = !showPwForm" class="btn-secondary text-xs px-3 py-1.5">Change password</button>
          <button @click="logoutOthers" :disabled="logoutOthersBusy" class="btn-secondary text-xs px-3 py-1.5"
            title="Revoke every other browser session (this tab stays signed in)">
            {{ logoutOthersBusy ? '…' : 'Log out other sessions' }}
          </button>
          <button @click="signOut" class="btn-secondary text-xs px-3 py-1.5">Sign out</button>
        </div>
        <p v-if="logoutOthersMsg" class="px-5 pb-3 text-xs text-green-400">{{ logoutOthersMsg }}</p>
        <!-- Change password (any role) -->
        <div v-if="showPwForm" class="px-5 pb-5 border-t border-border/60 pt-4 space-y-3">
          <div class="grid gap-3 sm:grid-cols-3">
            <div>
              <label class="text-xs text-muted-foreground block mb-1">Current password</label>
              <input v-model="pwCurrent" type="password" class="input w-full text-sm" />
            </div>
            <div>
              <label class="text-xs text-muted-foreground block mb-1">New password (min 8 characters)</label>
              <input v-model="pwNew" type="password" class="input w-full text-sm" />
            </div>
            <div>
              <label class="text-xs text-muted-foreground block mb-1">Confirm new password</label>
              <input v-model="pwConfirm" type="password" class="input w-full text-sm" @keydown.enter="submitPassword" />
            </div>
          </div>
          <p v-if="pwMsg" class="text-xs" :class="pwMsg.ok ? 'text-green-400' : 'text-red-400'">{{ pwMsg.text }}</p>
          <button @click="submitPassword" :disabled="!pwCanSubmit || pwBusy" class="btn-primary text-xs px-3 py-1.5">
            {{ pwBusy ? 'Saving…' : 'Save password' }}
          </button>
        </div>
      </section>
      </div>

      <!-- API tokens tab (everyone) -->
      <div v-if="activeTab === 'api'" class="space-y-6">
        <section class="card overflow-hidden">
          <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
            <span class="w-9 h-9 rounded-lg bg-violet-500/15 text-violet-400 flex items-center justify-center flex-shrink-0">
              <KeyIcon class="w-5 h-5" />
            </span>
            <div class="flex-1 min-w-0">
              <h2 class="text-sm font-semibold text-foreground">API tokens</h2>
              <p class="text-xs text-muted-foreground/70">Headless access for scripts and the GeoLibre/QGIS plugins</p>
            </div>
            <button @click="showTokenModal = true" class="btn-primary text-xs px-3 py-1.5">Create token</button>
          </header>
          <div class="p-2">
            <div v-if="tokensLoading" class="px-3 py-6 text-sm text-muted-foreground/70 text-center">Loading…</div>
            <div v-else-if="!tokens.length" class="px-3 py-8 text-sm text-muted-foreground/70 text-center">
              No tokens yet. Create one to drive the API from a script or the GeoLibre/QGIS plugins.
            </div>
            <div v-for="t in tokens" :key="t.id" class="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-muted/60">
              <span class="w-9 h-9 rounded-lg bg-muted flex items-center justify-center flex-shrink-0">
                <KeyIcon class="w-4 h-4 text-muted-foreground" />
              </span>
              <div class="flex-1 min-w-0">
                <div class="text-sm font-medium text-foreground/85 truncate">{{ t.name }}</div>
                <div class="flex items-center gap-1.5 flex-wrap mt-0.5">
                  <span class="font-mono text-[10px] text-muted-foreground/70">{{ t.prefix }}…</span>
                  <span v-for="s in t.scopes" :key="s"
                    class="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary font-mono">{{ s }}</span>
                </div>
              </div>
              <div class="text-[11px] text-muted-foreground/70 text-right flex-shrink-0 hidden sm:block">
                <div>{{ t.last_used_at ? 'Used ' + fmtDate(t.last_used_at) : 'Never used' }}</div>
                <div>Expires {{ fmtDate(t.expires_at) }}</div>
              </div>
              <button v-if="revokeId === t.id" @click="confirmRevoke(t.id)"
                class="text-xs text-red-500 font-medium px-2 flex-shrink-0">Confirm</button>
              <button v-else @click="revokeId = t.id"
                class="px-2 text-muted-foreground/70 hover:text-red-500 transition-colors flex-shrink-0" title="Revoke">
                <TrashIcon class="w-4 h-4" />
              </button>
            </div>
          </div>
        </section>
        <p class="text-[11px] text-muted-foreground/70 px-1">
          Tokens act as you, limited to their scopes and never above your role. Send as
          <code class="font-mono">Authorization: Bearer &lt;token&gt;</code>. Secrets are shown once.
        </p>
      </div>
    </div>
  </div>

  <TokenModal v-if="showTokenModal" @close="showTokenModal = false" @created="loadTokens" />
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useSystemStore } from '@/stores/system'
import { useAuthStore } from '@/stores/auth'
import { ServerIcon, HardDriveIcon, UserIcon, RefreshIcon, MailIcon, KeyIcon, TrashIcon } from './icons'
import api, { changePassword, logoutAll, controlService, getEmailSettings, sendTestEmail,
              updateEmailSettings, listTokens, revokeToken, getOidcSettings, updateOidcSettings,
              getBackupSettings, updateBackupSettings, testBackupDestination,
              listBackupRuns, startBackup } from '@/api'
import TokenModal from '@/components/users/TokenModal.vue'

const systemStore = useSystemStore()
const auth = useAuthStore()
const router = useRouter()
const martinBusy = ref(false)
const martinMsg = ref(null)
const busySvc = ref(null)

// ── Tabs — group Settings so it doesn't sprawl. Admin-only tabs are filtered out for editors/viewers.
const TABS = [
  { id: 'account', label: 'Account' },
  { id: 'api', label: 'API tokens' },
  { id: 'infra', label: 'Infrastructure', admin: true },
  { id: 'backups', label: 'Backups', admin: true },
  { id: 'email', label: 'Email', admin: true },
  { id: 'auth', label: 'Authentication', admin: true },
]
const tabs = computed(() => TABS.filter(t => !t.admin || auth.isAdmin))
const activeTab = ref('account')

// -- Backups -------------------------------------------------------------------------------
// The destination secret is write-only: `secret_set` tells us one is stored, and we send
// `secret_key` only when the admin actually typed a new one.
const bk = reactive({
  enabled: false, endpoint: '', bucket: '', prefix: 'geodeploy-backups', access_key: '',
  secret_key: '', region: 'us-east-1', schedule: 'off', hour: 3, keep: 7,
  include_postgis: true, include_objects: true, include_state: true, secret_set: false,
})
const bkRuns = ref([])
const bkSaving = ref(false)
const bkTesting = ref(false)
const bkRunning = ref(false)
const bkMsg = ref(null)
let bkPoll = null

function fmtBytes(b) {
  if (!b) return '-'
  if (b > 1e12) return (b / 1e12).toFixed(1) + ' TB'
  if (b > 1e9) return (b / 1e9).toFixed(1) + ' GB'
  if (b > 1e6) return (b / 1e6).toFixed(1) + ' MB'
  return (b / 1e3).toFixed(0) + ' KB'
}

async function loadBackups() {
  if (!auth.isAdmin) return
  try {
    const { data } = await getBackupSettings()
    Object.assign(bk, data, { secret_key: '' })
  } catch { /* not configured yet */ }
  await refreshBackupRuns()
}

async function refreshBackupRuns() {
  try {
    bkRuns.value = (await listBackupRuns()).data
  } catch { bkRuns.value = [] }
  // Keep polling while a run is in flight so the step/percentage advances on screen.
  const running = bkRuns.value.some(r => r.status === 'running')
  bkRunning.value = running
  clearTimeout(bkPoll)
  if (running) bkPoll = setTimeout(refreshBackupRuns, 4000)
}

async function saveBackups() {
  bkSaving.value = true
  bkMsg.value = null
  try {
    const payload = { ...bk }
    delete payload.secret_set
    if (!payload.secret_key) delete payload.secret_key   // blank = keep stored
    const { data } = await updateBackupSettings(payload)
    Object.assign(bk, data, { secret_key: '' })
    bkMsg.value = { ok: true, text: 'Saved.' }
  } catch (e) {
    bkMsg.value = { ok: false, text: e.response?.data?.detail || 'Could not save.' }
  } finally {
    bkSaving.value = false
  }
}

async function testBackups() {
  bkTesting.value = true
  bkMsg.value = null
  try {
    await testBackupDestination()
    bkMsg.value = { ok: true, text: 'Destination is reachable and writable.' }
  } catch (e) {
    bkMsg.value = { ok: false, text: e.response?.data?.detail || 'Could not reach the destination.' }
  } finally {
    bkTesting.value = false
  }
}

async function runBackup() {
  bkMsg.value = null
  try {
    await startBackup()
    bkRunning.value = true
    bkMsg.value = { ok: true, text: 'Backup started.' }
    refreshBackupRuns()
  } catch (e) {
    bkMsg.value = { ok: false, text: e.response?.data?.detail || 'Could not start the backup.' }
  }
}

// ── API tokens (A-03) — each user manages their own ──
const tokens = ref([])
const tokensLoading = ref(true)
const showTokenModal = ref(false)
const revokeId = ref(null)  // two-step inline confirm

async function loadTokens() {
  tokensLoading.value = true
  try { tokens.value = (await listTokens()).data } catch { tokens.value = [] }
  finally { tokensLoading.value = false }
}
async function confirmRevoke(id) {
  try { await revokeToken(id); await loadTokens() } catch { /* ignore */ }
  revokeId.value = null
}
function fmtDate(s) { return s ? new Date(s).toLocaleDateString() : '' }

// ── OIDC SSO (A-04, admin) ──
const oidcForm = ref(null)
const oidcHasSecret = ref(false)
const oidcRedirectUri = ref('')
const oidcBusy = ref(false)
const oidcMsg = ref(null)
async function loadOidc() {
  try {
    const { data } = await getOidcSettings()
    oidcHasSecret.value = data.has_client_secret
    oidcRedirectUri.value = data.redirect_uri
    oidcForm.value = {
      oidc_enabled: data.oidc_enabled,
      oidc_issuer: data.oidc_issuer || '',
      oidc_client_id: data.oidc_client_id || '',
      oidc_client_secret: '',  // blank = keep the stored secret
      oidc_label: data.oidc_label || '',
      oidc_auto_provision: data.oidc_auto_provision,
      oidc_allowed_domains: data.oidc_allowed_domains || '',
      oidc_default_role: data.oidc_default_role || 'viewer',
    }
  } catch { /* stays in loading state */ }
}
async function saveOidc() {
  oidcBusy.value = true
  oidcMsg.value = null
  try {
    const { data } = await updateOidcSettings(oidcForm.value)
    oidcHasSecret.value = data.has_client_secret
    oidcForm.value.oidc_client_secret = ''
    oidcMsg.value = { ok: true, text: 'Saved.' }
    setTimeout(() => { oidcMsg.value = null }, 2500)
  } catch (e) {
    oidcMsg.value = { ok: false, text: e.response?.data?.detail || e.message }
  } finally {
    oidcBusy.value = false
  }
}

const initials = computed(() =>
  (auth.user?.name || '?').split(' ').map(w => w[0]).filter(Boolean).slice(0, 2).join('').toUpperCase())

const roleBadge = computed(() => ({
  owner: 'bg-amber-500/15 text-amber-400',
  admin: 'bg-violet-500/15 text-violet-400',
  editor: 'bg-blue-500/15 text-blue-400',
  viewer: 'bg-muted text-muted-foreground',
}[auth.role] || 'bg-muted text-muted-foreground'))

// Change password — martinMsg-style transient feedback (no toast system by convention)
const showPwForm = ref(false)
const pwCurrent = ref('')
const pwNew = ref('')
const pwConfirm = ref('')
const pwBusy = ref(false)
const pwMsg = ref(null)
const pwCanSubmit = computed(() =>
  pwCurrent.value && pwNew.value.length >= 8 && pwNew.value === pwConfirm.value)

async function submitPassword() {
  if (!pwCanSubmit.value || pwBusy.value) {
    if (pwNew.value && pwNew.value.length < 8) pwMsg.value = { ok: false, text: 'New password must be at least 8 characters.' }
    else if (pwConfirm.value && pwNew.value !== pwConfirm.value) pwMsg.value = { ok: false, text: 'Passwords do not match.' }
    return
  }
  pwBusy.value = true
  pwMsg.value = null
  try {
    const { data } = await changePassword({ current_password: pwCurrent.value, new_password: pwNew.value })
    auth.setToken(data.access_token)  // A-04: adopt the re-issued token; other sessions are revoked
    pwMsg.value = { ok: true, text: 'Password updated. Other sessions were signed out.' }
    pwCurrent.value = pwNew.value = pwConfirm.value = ''
    setTimeout(() => { pwMsg.value = null; showPwForm.value = false }, 2500)
  } catch (err) {
    pwMsg.value = { ok: false, text: err.response?.data?.detail || err.message }
  } finally {
    pwBusy.value = false
  }
}

const statTiles = computed(() => [
  { label: 'Vector layers', value: systemStore.stats?.vector_layers ?? 0 },
  { label: 'Raster files', value: systemStore.stats?.raster_layers ?? 0 },
  { label: 'Portals', value: systemStore.stats?.portals ?? 0 },
])

// Storage breakdown — colors follow the data-type idiom used across the app
// (vector/PostGIS blue, raster amber, GeoParquet violet, published pages emerald).
const STORES = [
  { key: 'postgis_bytes', label: 'PostGIS', dot: 'bg-blue-400', bar: 'bg-blue-500/80' },
  { key: 'raster_bytes', label: 'Rasters (COG)', dot: 'bg-amber-400', bar: 'bg-amber-500/80' },
  { key: 'geoparquet_bytes', label: 'GeoParquet', dot: 'bg-violet-400', bar: 'bg-violet-500/80' },
  { key: 'portal_bundle_bytes', label: 'Portal pages', dot: 'bg-emerald-400', bar: 'bg-emerald-500/80' },
]
const storageTiles = computed(() => STORES.map((s) => ({
  ...s, bytes: systemStore.stats?.[s.key] ?? null,
})))
const storageSegments = computed(() => {
  const total = systemStore.stats?.used_bytes || 0
  if (!total) return []
  return storageTiles.value
    .filter((s) => s.bytes)
    .map((s) => ({ ...s, pct: (s.bytes / total) * 100 }))
})

const formatBytes = (b) => !b ? '0 B'
  : b > 1e9 ? `${(b / 1e9).toFixed(1)} GB`
  : b > 1e6 ? `${(b / 1e6).toFixed(1)} MB`
  : `${(b / 1e3).toFixed(0)} KB`

function dotClass(s) {
  if (['running', 'healthy'].includes(s)) return 'bg-green-500'
  if (['unhealthy', 'stopped', 'exited'].includes(s)) return 'bg-red-500'
  return 'bg-gray-300'
}
function pillClass(s) {
  if (['running', 'healthy'].includes(s)) return 'bg-green-500/15 text-green-400'
  if (['unhealthy', 'stopped', 'exited'].includes(s)) return 'bg-red-500/15 text-red-400'
  return 'bg-muted text-muted-foreground'
}

function signOut() {
  auth.logout()
  router.push('/login')
}

// A-04: revoke every OTHER session; this tab adopts the re-issued token and stays signed in.
const logoutOthersBusy = ref(false)
const logoutOthersMsg = ref('')
async function logoutOthers() {
  logoutOthersBusy.value = true
  try {
    const { data } = await logoutAll()
    auth.setToken(data.access_token)
    logoutOthersMsg.value = 'Other sessions signed out.'
    setTimeout(() => { logoutOthersMsg.value = '' }, 3000)
  } catch { /* 401 interceptor handles an expired session */ }
  finally { logoutOthersBusy.value = false }
}

async function svcAction(name, action) {
  if (action === 'stop' &&
      !confirm(`Stop the "${name}" service? Features that depend on it will be unavailable until you start it again.`)) {
    return
  }
  busySvc.value = name
  try {
    await controlService(name, action)
  } catch (e) {
    // Restarting nginx drops the proxy mid-request, so a network error here is expected.
  } finally {
    setTimeout(async () => {
      try { await systemStore.refreshHealth() } catch {}
      busySvc.value = null
    }, 2500)
  }
}

onMounted(() => {
  loadTokens()  // per-user — everyone has an API tokens tab
  // Health/stats/email endpoints are admin-only server-side — don't fire doomed requests as editor/viewer.
  if (auth.isAdmin) {
    systemStore.refreshHealth()
    systemStore.refreshStats()
    loadEmail()
    loadOidc()
    checkUpdates()
    loadBackups()
  }
})

// Stop the backup progress poll when leaving Settings (it re-arms itself while a run is active).
onUnmounted(() => clearTimeout(bkPoll))

// Service logs (read-only). Admin-only server-side; a safe substitute for a shell.
const LOG_SERVICES = ['celery', 'api', 'nginx', 'martin', 'titiler', 'postgres', 'minio', 'redis', 'ui']
const logs = ref({ service: 'celery', tail: 200, text: '', loading: false })
async function fetchLogs() {
  logs.value.loading = true
  try {
    const { data } = await api.get(`/admin/services/${logs.value.service}/logs`, { params: { tail: logs.value.tail } })
    logs.value.text = data.logs || '(no output)'
  } catch (e) {
    logs.value.text = 'Failed to load logs: ' + (e?.response?.data?.detail || e.message)
  } finally {
    logs.value.loading = false
  }
}

// Danger Zone — in-container command runner (opt-in server-side; never api/celery).
const TERMINAL_SERVICES = ['postgres', 'redis', 'martin', 'titiler', 'minio', 'nginx', 'ui']
const term = ref({ service: 'postgres', command: '', output: null, loading: false })
async function runExec() {
  if (!term.value.command.trim()) return
  term.value.loading = true
  term.value.output = null
  try {
    const { data } = await api.post(`/admin/services/${term.value.service}/exec`, { command: term.value.command })
    term.value.output = (data.output || '') + (data.exit_code ? `\n[exit ${data.exit_code}]` : '')
  } catch (e) {
    term.value.output = 'Error: ' + (e?.response?.data?.detail || e.message)
  } finally {
    term.value.loading = false
  }
}

// Software updates: check (read-only) + one-click update with live status.
const updates = ref({ loading: false, data: null, updating: false, progress: null })
async function checkUpdates() {
  updates.value.loading = true
  try {
    const { data } = await api.get('/admin/updates')
    updates.value.data = data
  } catch {
    updates.value.data = { status: 'offline', current: '—' }
  } finally {
    updates.value.loading = false
  }
}
let updatePollTimer = null
let updatePollCount = 0
async function startUpdate() {
  if (!confirm('Update GeoDeploy now? Services restart briefly. If the new version is unhealthy it rolls back automatically.')) return
  updates.value.updating = true
  updatePollCount = 0
  updates.value.progress = { phase: 'running', message: 'Starting…' }
  try {
    await api.post('/admin/update')
    pollUpdateStatus()
  } catch (e) {
    updates.value.progress = { phase: 'error', message: e?.response?.data?.detail || e.message }
    updates.value.updating = false
  }
}
async function pollUpdateStatus() {
  updatePollCount++
  try {
    const { data } = await api.get('/admin/update/status')
    updates.value.progress = data
    if (data.phase === 'success') {
      // Update applied + healthy → reload the page to pick up the new UI + version display.
      clearTimeout(updatePollTimer)
      updates.value.progress = { ...data, message: (data.message || 'Updated.') + ' Reloading…' }
      setTimeout(() => window.location.reload(), 2500)
      return
    }
    if (['rolledback', 'error'].includes(data.phase)) {
      updates.value.updating = false
      clearTimeout(updatePollTimer)
      setTimeout(checkUpdates, 2000)
      return
    }
  } catch {
    // The API restarts mid-update — expected. Keep the last phase, note we're waiting, keep polling.
    if (updates.value.progress) updates.value.progress = { ...updates.value.progress, message: 'Restarting services… (can take a minute)' }
  }
  if (updatePollCount > 400) {   // ~20 min: a full rebuild (UI bundle + images) can outlast 5 min on a small host
    // Don't strand the user on a dead-end "took too long" message while the update may have actually
    // finished — reload so checkUpdates() reflects the REAL deployed state ("up to date" if it landed).
    updates.value.progress = { phase: 'running', message: 'Still finishing — reloading to check…' }
    setTimeout(() => window.location.reload(), 1500)
    return
  }
  updatePollTimer = setTimeout(pollUpdateStatus, 3000)
}
function updatePhaseClass(phase) {
  if (phase === 'success') return 'text-green-400'
  if (['error', 'rolledback'].includes(phase)) return 'text-red-400'
  return 'text-amber-400'
}

// Outgoing email (generic SMTP, C-08a)
const emailForm = ref(null)
const emailHasPassword = ref(false)
const emailConfigured = ref(false)
const emailBusy = ref(null)   // null | 'save' | 'test'
const emailMsg = ref(null)

async function loadEmail() {
  try {
    const { data } = await getEmailSettings()
    emailHasPassword.value = data.has_password
    emailConfigured.value = data.configured
    emailForm.value = {
      smtp_host: data.smtp_host || '',
      smtp_port: data.smtp_port || 587,
      smtp_security: data.smtp_security || 'starttls',
      smtp_username: data.smtp_username || '',
      smtp_password: '',   // blank = keep the stored secret
      email_from: data.email_from || '',
    }
  } catch { /* section simply stays in loading state */ }
}

async function saveEmail() {
  emailBusy.value = 'save'
  emailMsg.value = null
  try {
    const { data } = await updateEmailSettings(emailForm.value)
    emailHasPassword.value = data.has_password
    emailConfigured.value = data.configured
    emailForm.value.smtp_password = ''
    emailMsg.value = { ok: true, text: data.configured ? 'Saved — email is enabled.' : 'Saved (host + from address required to enable).' }
  } catch (err) {
    emailMsg.value = { ok: false, text: err.response?.data?.detail || err.message }
  } finally {
    emailBusy.value = null
    setTimeout(() => { emailMsg.value = null }, 6000)
  }
}

async function testEmail() {
  emailBusy.value = 'test'
  emailMsg.value = null
  try {
    const { data } = await sendTestEmail()
    emailMsg.value = { ok: true, text: `Test email sent to ${data.to} — check the inbox.` }
  } catch (err) {
    emailMsg.value = { ok: false, text: err.response?.data?.detail || err.message }
  } finally {
    emailBusy.value = null
  }
}

async function reloadMartin() {
  martinBusy.value = true
  martinMsg.value = null
  try {
    const { data } = await api.post('/admin/reload-martin')
    martinMsg.value = { ok: true, text: `Config reloaded — ${data.tables} table(s) registered.` }
    setTimeout(() => systemStore.refreshHealth(), 2000)
  } catch (err) {
    martinMsg.value = { ok: false, text: err.response?.data?.detail || err.message }
  } finally {
    martinBusy.value = false
    setTimeout(() => { martinMsg.value = null }, 6000)
  }
}
</script>

<style scoped>
.svc-btn {
  width: 22px; height: 22px; border-radius: 5px; font-size: 11px; line-height: 1;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid #e5e7eb; background: #fff; cursor: pointer;
}
.svc-btn:hover:not(:disabled) { background: #f9fafb; }
.svc-btn:disabled { opacity: .4; cursor: default; }
</style>
