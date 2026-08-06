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
          <button @click="checkUpdates(true)" :disabled="updates.loading" class="btn-secondary text-xs px-3 py-1.5">
            <RefreshIcon class="w-3.5 h-3.5" /> {{ updates.loading ? 'Checking…' : 'Check' }}
          </button>
        </header>
        <div class="p-5 space-y-3">
          <div v-if="updates.loading && !updates.data" class="text-sm text-muted-foreground/70">Checking for updates…</div>
          <template v-else-if="updates.data">
            <div class="text-sm font-medium">
              <!-- An instance PINNED to a release is judged against releases: being many commits
                   behind the development branch is the state its operator chose, not a problem. -->
              <template v-if="updates.data.channel === 'release'">
                <span v-if="updates.data.release_update_available" class="text-amber-400">
                  New release available — {{ updates.data.latest_release?.tag }}
                </span>
                <span v-else class="text-green-400">✓ Up to date — on the latest release</span>
              </template>
              <!-- A branch or a bare commit: report what it follows, don't nag. -->
              <span v-else-if="updates.data.channel === 'branch' || updates.data.channel === 'pinned'"
                    class="text-sky-400">
                Pinned to {{ updates.data.current_ref }} — not tracking main
              </span>
              <span v-else-if="updates.data.up_to_date === true" class="text-green-400">✓ Up to date</span>
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
              <span>Running
                <b class="text-foreground">{{ updates.data.current_tag || updates.data.current }}</b>
                <span v-if="updates.data.current_tag" class="text-muted-foreground/60"> ({{ updates.data.current }})</span>
              </span>
              <span v-if="updates.data.latest">Latest on main <b class="text-foreground">{{ updates.data.latest }}</b></span>
              <span v-if="updates.data.latest_release">Latest release <b class="text-foreground">{{ updates.data.latest_release.tag }}</b></span>
              <span v-if="updates.data.current_ref">Following <b class="text-foreground">{{ updates.data.current_ref }}</b></span>
            </div>
            <div v-if="updates.data.commits && updates.data.commits.length && updateChoice === 'main'"
                 class="rounded-lg border border-border/60 bg-muted/20 divide-y divide-border/40 max-h-52 overflow-y-auto">
              <div v-for="c in updates.data.commits" :key="c.sha" class="px-3 py-1.5 text-xs flex gap-2">
                <span class="font-mono text-muted-foreground/60 flex-shrink-0">{{ c.sha }}</span>
                <span class="text-foreground/85 truncate">{{ c.message }}</span>
              </div>
            </div>
            <!-- WHICH version. Tracking `main` is what every install did before there were
                 releases; a tag pins a released version, so an operator can hold back on one or step
                 down after a bad one; a branch is unreleased work, for trying a feature before it
                 merges. Always shown — "reinstall the version I am on" is a legitimate action, and
                 hiding the picker until an update exists is what made a pinned instance look stuck. -->
            <div class="rounded-lg border border-border/60 bg-muted/20 p-3 space-y-2">
              <div class="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground/70">Version to install</div>
              <label v-for="opt in updateChoices" :key="opt.value"
                     class="flex items-start gap-2.5 text-xs cursor-pointer">
                <input type="radio" v-model="updateChoice" :value="opt.value" class="mt-0.5 accent-primary" />
                <span class="min-w-0">
                  <span class="font-medium text-foreground">{{ opt.label }}</span>
                  <span v-if="opt.hint" class="text-muted-foreground/70"> — {{ opt.hint }}</span>
                </span>
              </label>
              <div v-if="updateChoice === 'pick'" class="pl-6">
                <select v-model="updatePickedTag" class="input text-xs py-1.5">
                  <option v-for="r in (updates.data.releases || [])" :key="r.tag" :value="r.tag">
                    {{ r.name }}{{ r.is_current ? ' — running now' : '' }}{{ r.prerelease ? ' (pre-release)' : '' }}
                  </option>
                </select>
              </div>
              <div v-if="updateChoice === 'branch'" class="pl-6 space-y-1.5">
                <!-- A list, not a text box: a typo in a ref fails minutes later, inside a container,
                     as "No such version". -->
                <select v-model="updatePickedBranch" class="input text-xs py-1.5">
                  <option v-for="b in (updates.data.branches || [])" :key="b.name" :value="b.name">
                    {{ b.name }}{{ b.sha === updates.data.current_full ? ' — running now' : '' }}
                  </option>
                </select>
                <p class="text-[11px] text-amber-300/80">
                  A branch is work in progress — unreleased, and possibly broken. Take a backup first,
                  and come back to “Latest release” when you are done.
                </p>
              </div>
            </div>
            <div class="space-y-2">
              <button @click="startUpdate" :disabled="updates.updating || !updateTarget"
                      class="text-xs font-semibold px-3.5 py-2 rounded-md bg-primary text-primary-foreground hover:brightness-110 disabled:opacity-50">
                {{ updates.updating ? 'Updating…' : (updateIsReinstall ? 'Reinstall ' + updateTarget : 'Update to ' + updateTarget) }}
              </button>
              <p class="text-[11px] text-muted-foreground/70">
                Builds the new version, restarts services, and health-checks — <b>rolls back automatically</b> if it comes up unhealthy.
                Or run manually: <code class="font-mono bg-muted/40 rounded px-1.5 py-0.5 select-all">{{ manualUpdateCommand }}</code>
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
      <!-- Everything per-service — status, actions, logs, terminal, deployments — in ONE place.
           Replaces the separate health list / logs card / danger-zone terminal, which each made
           you pick the service again. -->
      <InfrastructurePanel />

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

      <!-- Below Storage on purpose: this is where someone goes wondering "what ARE my credentials?"
           after seeing how much space they use. Owner-only, and it fetches nothing until asked. -->
      <ConnectionDetails v-if="auth.isOwner" />

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
            <span v-if="bk.enabled" class="ml-auto text-[11px] font-medium px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400">Enabled</span>
            <span v-else class="ml-auto text-[11px] font-medium px-2 py-0.5 rounded-full bg-muted text-muted-foreground">Off</span>
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
                <label class="text-xs text-muted-foreground block mb-1">
                  Region <span class="text-muted-foreground/60">(optional)</span>
                </label>
                <input v-model="bk.region" list="gd-s3-regions" class="input w-full text-sm"
                  :placeholder="guessedRegion || 'auto-detected from the endpoint'" />
                <datalist id="gd-s3-regions">
                  <option value="auto" /><option value="us-east-1" /><option value="eu-central-1" />
                  <option value="eu-west-1" /><option value="eu-central" />
                </datalist>
                <p class="text-[11px] text-muted-foreground/70 mt-1">
                  <span v-if="guessedRegion">Leave blank to use <span class="font-mono">{{ guessedRegion }}</span>, read from your endpoint.</span>
                  <span v-else>Leave blank unless your provider rejects the default. Only AWS checks it strictly.</span>
                </p>
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
              <!-- The toggle lives WITH the buttons, not in the card header. It was up there, far
                   from the actions it gates, and it is server state — so ticking it without saving
                   left "Back up now" clickable in the browser and refused by the API. Beside Save,
                   the sequence is visible: tick, save, test, run. -->
              <label class="flex items-center gap-2 text-sm mr-1 select-none cursor-pointer">
                <input type="checkbox" v-model="bk.enabled" class="w-4 h-4" /> Enable backups
              </label>
              <button @click="saveBackups" :disabled="bkSaving" class="btn-primary text-sm px-4 py-2 disabled:opacity-60">
                {{ bkSaving ? 'Saving...' : 'Save' }}
              </button>
              <button @click="testBackups" :disabled="bkTesting" class="btn-secondary text-sm px-4 py-2 disabled:opacity-60">
                {{ bkTesting ? 'Testing...' : 'Test destination' }}
              </button>
              <button @click="runBackup" :disabled="!!runBlockedReason"
                :title="runBlockedReason" class="btn-secondary text-sm px-4 py-2 disabled:opacity-60">
                Back up now
              </button>
              <span v-if="bkMsg" class="text-xs" :class="bkMsg.ok ? 'text-emerald-400' : 'text-red-400'">{{ bkMsg.text }}</span>
              <!-- A disabled button must say why it is disabled. The Enabled toggle is in the card
                   HEADER and this button is at the foot of the card, so the cause is off-screen from
                   the effect: clicking did nothing, silently, with no request to inspect. -->
              <span v-else-if="runBlockedReason" class="text-xs text-muted-foreground">{{ runBlockedReason }}</span>
            </div>

            <!-- Shown only when the test proved the credentials work and the bucket simply is not
                 there. Sending someone to a provider console to type a name the app already knows
                 is friction with no safety gain — creating a bucket destroys nothing. -->
            <div v-if="bkMissingBucket" class="flex items-center gap-3 flex-wrap p-3 rounded-lg border border-amber-500/30 bg-amber-500/10">
              <span class="text-xs text-amber-200/90">
                Create <span class="font-mono">{{ bkMissingBucket }}</span> at this endpoint now?
              </span>
              <button @click="createBucket" :disabled="bkCreating"
                class="btn-secondary text-xs px-3 py-1.5 disabled:opacity-60">
                {{ bkCreating ? 'Creating...' : 'Create it' }}
              </button>
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
              <tr v-for="r in bkRunsPage" :key="r.id" class="border-b border-border/40 last:border-0">
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
          <!-- Paginated rather than scrolled: this list only grows, and a page that gets taller
               forever pushes everything below it out of reach. -->
          <div v-if="bkRunPages > 1" class="flex items-center justify-between gap-3 px-5 py-3 border-t border-border/60">
            <span class="text-[11px] text-muted-foreground/70">
              {{ bkRunPage * BK_PER_PAGE + 1 }}–{{ Math.min((bkRunPage + 1) * BK_PER_PAGE, bkRuns.length) }}
              of {{ bkRuns.length }}
            </span>
            <span class="flex items-center gap-1.5">
              <button @click="bkRunPage--" :disabled="bkRunPage === 0"
                class="btn-secondary text-xs px-2 py-1 disabled:opacity-40">Prev</button>
              <span class="text-xs text-muted-foreground px-1">{{ bkRunPage + 1 }} / {{ bkRunPages }}</span>
              <button @click="bkRunPage++" :disabled="bkRunPage >= bkRunPages - 1"
                class="btn-secondary text-xs px-2 py-1 disabled:opacity-40">Next</button>
            </span>
          </div>
        </section>

        <!-- Manage backups — DANGER ZONE. Same red treatment as the container terminal, because
             restoring replaces the database and the files, and deleting a backup is the one
             unrecoverable action in the app. -->
        <section class="rounded-xl border border-red-500/40 bg-red-500/[0.03] overflow-hidden">
          <header class="flex items-center gap-3 px-5 py-3.5 border-b border-red-500/30">
            <span class="w-9 h-9 rounded-lg bg-red-500/15 text-red-400 flex items-center justify-center flex-shrink-0">
              <AlertIcon class="w-5 h-5" />
            </span>
            <div class="min-w-0">
              <h2 class="font-semibold text-red-300">Manage backups</h2>
              <p class="text-xs text-muted-foreground">
                What is actually stored at the destination. Restoring replaces everything.
              </p>
            </div>
            <button @click="loadStored" :disabled="storedLoading"
              class="ml-auto btn-secondary text-xs px-3 py-1.5">
              {{ storedLoading ? 'Loading…' : 'Refresh' }}
            </button>
          </header>

          <div class="p-5 space-y-3">
            <p v-if="!bk.bucket" class="text-sm text-muted-foreground/70">
              Configure a destination first.
            </p>
            <p v-else-if="!stored.length && !storedLoading" class="text-sm text-muted-foreground/70">
              No backups stored yet.
            </p>

            <div v-for="b in storedPage" :key="b.key"
              class="rounded-lg border border-border bg-card p-3 flex flex-wrap items-center gap-3">
              <div class="min-w-0 flex-1">
                <div class="text-sm font-medium font-mono truncate">{{ b.name }}</div>
                <div class="text-[11px] text-muted-foreground mt-0.5">
                  <span v-if="b.manifest">
                    {{ fmtBytes(b.manifest.total_bytes) }} ·
                    {{ Object.keys(b.manifest.parts || {}).join(' · ') }}
                  </span>
                  <span v-else class="text-amber-400">
                    incomplete — no manifest, cannot be restored
                  </span>
                </div>
              </div>
              <button v-if="b.manifest && auth.isOwner" @click="openRestore(b)"
                class="text-xs px-3 py-1.5 rounded-md border border-red-500/50 text-red-300
                       hover:bg-red-500/10">Restore</button>
              <button @click="removeStored(b)" :disabled="storedBusy === b.key"
                class="text-xs px-3 py-1.5 rounded-md border border-border text-muted-foreground
                       hover:text-red-400 hover:border-red-500/50 disabled:opacity-40">
                {{ storedBusy === b.key ? 'Deleting…' : 'Delete' }}
              </button>
            </div>

            <div v-if="storedPages > 1" class="flex items-center justify-between gap-3 pt-1">
              <span class="text-[11px] text-muted-foreground/70">
                {{ storedPageIdx * BK_PER_PAGE + 1 }}–{{ Math.min((storedPageIdx + 1) * BK_PER_PAGE, stored.length) }}
                of {{ stored.length }}
              </span>
              <span class="flex items-center gap-1.5">
                <button @click="storedPageIdx--" :disabled="storedPageIdx === 0"
                  class="btn-secondary text-xs px-2 py-1 disabled:opacity-40">Prev</button>
                <span class="text-xs text-muted-foreground px-1">{{ storedPageIdx + 1 }} / {{ storedPages }}</span>
                <button @click="storedPageIdx++" :disabled="storedPageIdx >= storedPages - 1"
                  class="btn-secondary text-xs px-2 py-1 disabled:opacity-40">Next</button>
              </span>
            </div>

            <p v-if="stored.length && !auth.isOwner" class="text-[11px] text-muted-foreground/70">
              Only the workspace owner can restore a backup.
            </p>
            <p v-if="storedMsg" class="text-xs" :class="storedMsg.ok ? 'text-emerald-400' : 'text-red-400'">
              {{ storedMsg.text }}
            </p>

            <!-- LIVE restore progress. Previously the page reloaded 4 seconds after starting a
                 restore, which says nothing about whether it worked — a restore takes minutes, so
                 the reload landed mid-way and the operator was left guessing. Now it waits for the
                 real verdict and only reloads on success. -->
            <div v-if="activeRestore" class="rounded-lg border p-3 space-y-2"
              :class="activeRestore.status === 'error' ? 'border-red-500/50 bg-red-500/10'
                : activeRestore.status === 'success' ? 'border-emerald-500/50 bg-emerald-500/10'
                : 'border-amber-500/40 bg-amber-500/10'">
              <div class="flex items-center gap-2 text-sm">
                <span v-if="activeRestore.status === 'running'" class="animate-spin">⟳</span>
                <span class="font-medium"
                  :class="activeRestore.status === 'error' ? 'text-red-300'
                    : activeRestore.status === 'success' ? 'text-emerald-300' : 'text-amber-200'">
                  {{ activeRestore.status === 'success' ? 'Restore complete'
                    : activeRestore.status === 'error' ? 'Restore failed' : 'Restoring…' }}
                </span>
                <span class="text-xs text-muted-foreground">{{ activeRestore.current_step }}</span>
              </div>
              <div v-if="activeRestore.status === 'running'" class="h-1.5 rounded-full bg-muted overflow-hidden">
                <div class="h-full bg-amber-400 transition-all" :style="{ width: (activeRestore.progress || 0) + '%' }" />
              </div>
              <p v-if="activeRestore.error_message" class="text-xs text-red-300">{{ activeRestore.error_message }}</p>
              <p v-if="activeRestore.status === 'success'" class="text-xs text-emerald-300/90">
                Your data has been replaced from that backup. Reloading in {{ reloadIn }}s —
                <button @click="reloadNow" class="underline">reload now</button>.
              </p>
            </div>
          </div>
        </section>

        <!-- Restore history. It existed in the API from the start and was never shown; the restore
             is the most consequential action in the app and left no visible trace. -->
        <section v-if="auth.isOwner" class="card overflow-hidden">
          <header class="px-5 py-3.5 border-b border-border/60">
            <h2 class="font-semibold">Restore history</h2>
            <p class="text-xs text-muted-foreground">Every restore, who confirmed it, and how it ended.</p>
          </header>
          <div v-if="!rsRuns.length" class="px-5 py-8 text-center text-sm text-muted-foreground/70">
            No restores yet.
          </div>
          <table v-else class="w-full text-sm">
            <tbody>
              <tr v-for="r in rsRuns" :key="r.id" class="border-b border-border/40 last:border-0">
                <td class="px-5 py-2.5 whitespace-nowrap">
                  <span class="text-[11px] font-mono px-1.5 py-0.5 rounded"
                    :class="r.status === 'success' ? 'bg-emerald-500/15 text-emerald-400'
                      : r.status === 'error' ? 'bg-red-500/15 text-red-400' : 'bg-amber-500/15 text-amber-400'">{{ r.status }}</span>
                </td>
                <td class="px-2 py-2.5 text-xs text-muted-foreground whitespace-nowrap">
                  {{ new Date(r.started_at).toLocaleString() }}
                </td>
                <td class="px-2 py-2.5 text-xs text-muted-foreground font-mono truncate max-w-[14rem]"
                  :title="r.key">{{ r.key.split('/').pop() }}</td>
                <td class="px-2 py-2.5 text-xs text-muted-foreground">{{ r.confirmed_by }}</td>
                <td class="px-5 py-2.5 text-xs text-muted-foreground">
                  <span v-if="r.status === 'error'" class="text-red-400">{{ r.error_message }}</span>
                  <span v-else>{{ r.current_step }}</span>
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

    <!-- Restore confirmation. Deliberately NOT a one-click button: it replaces the database and
         the files, and the encryption-key verdict has to be read before anyone commits. -->
    <Teleport to="body">
      <div v-if="restoreTarget" class="fixed inset-0 bg-gray-900/60 backdrop-blur-sm flex items-center justify-center z-50 p-4"
        @click.self="restoreTarget = null">
        <div class="card w-full max-w-lg p-6 space-y-4 shadow-2xl border border-red-500/40">
          <h2 class="text-lg font-semibold text-red-300">Restore this backup?</h2>

          <div v-if="preflight" class="space-y-3">
            <p class="text-sm text-muted-foreground">
              This <strong class="text-foreground">replaces</strong> the database and files with the
              contents of <span class="font-mono text-xs">{{ restoreTarget.name }}</span>.
              Anything created since that backup is lost.
            </p>

            <div class="text-xs rounded-lg border border-border bg-muted/40 p-3 space-y-1">
              <div class="text-muted-foreground">Currently in this instance:</div>
              <div>{{ preflight.current.vector_layers }} vector · {{ preflight.current.raster_layers }} raster ·
                   {{ preflight.current.portals }} portals · {{ preflight.current.users }} users</div>
            </div>

            <p class="text-xs rounded-lg p-3 border"
              :class="preflight.secret_key.matches === false
                ? 'border-red-500/40 bg-red-500/10 text-red-300'
                : preflight.secret_key.matches === true
                  ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                  : 'border-amber-400/30 bg-amber-500/10 text-amber-200'">
              {{ preflight.secret_key.message }}
            </p>

            <div>
              <label class="text-xs text-muted-foreground block mb-1">
                Type <span class="font-mono text-foreground">{{ restoreTarget.name }}</span> to confirm
              </label>
              <input v-model="restoreConfirm" class="input w-full text-sm font-mono" spellcheck="false" />
            </div>
          </div>
          <p v-else class="text-sm text-muted-foreground">Checking the backup…</p>

          <div class="flex items-center justify-end gap-3">
            <span v-if="restoreMsg" class="text-xs text-red-400 mr-auto">{{ restoreMsg }}</span>
            <button @click="restoreTarget = null" class="text-sm text-muted-foreground hover:text-foreground px-3 py-2">
              Cancel
            </button>
            <button @click="confirmRestore"
              :disabled="restoreBusy || !preflight || restoreConfirm !== restoreTarget.name"
              class="text-sm font-semibold text-white bg-red-600 hover:bg-red-700 disabled:opacity-40
                     rounded-lg px-4 py-2">
              {{ restoreBusy ? 'Starting…' : 'Restore' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import ConnectionDetails from '@/components/infra/ConnectionDetails.vue'
import { useSystemStore } from '@/stores/system'
import { useAuthStore } from '@/stores/auth'
import { ServerIcon, HardDriveIcon, UserIcon, RefreshIcon, MailIcon, KeyIcon, TrashIcon, AlertIcon } from './icons'
import api, { changePassword, logoutAll, controlService, getEmailSettings, sendTestEmail,
              updateEmailSettings, listTokens, revokeToken, getOidcSettings, updateOidcSettings,
              getBackupSettings, updateBackupSettings, testBackupDestination, createBackupBucket,
              listRestoreRuns,
              listBackupRuns, startBackup, listStoredBackups, deleteStoredBackup,
              restorePreflight, startRestore } from '@/api'
import TokenModal from '@/components/users/TokenModal.vue'
import InfrastructurePanel from '@/components/infra/InfrastructurePanel.vue'

const systemStore = useSystemStore()
const auth = useAuthStore()
const router = useRouter()
const martinBusy = ref(false)
const martinMsg = ref(null)
const busySvc = ref(null)

// ── Tabs — group Settings so it doesn't sprawl. Admin-only tabs are filtered out for editors/viewers.
// Order = how often an operator reaches for them: infrastructure first, personal settings last.
const TABS = [
  { id: 'infra', label: 'Infrastructure', admin: true },
  { id: 'backups', label: 'Backups', admin: true },
  { id: 'email', label: 'Email', admin: true },
  { id: 'auth', label: 'Authentication', admin: true },
  { id: 'api', label: 'API tokens' },
  { id: 'account', label: 'Account' },
]
const tabs = computed(() => TABS.filter(t => !t.admin || auth.isAdmin))
// Land on the first tab this user can actually see: admins open on Infrastructure,
// editors/viewers (for whom the admin tabs are filtered out) on API tokens.
const activeTab = ref(tabs.value[0]?.id || 'account')

// -- Backups -------------------------------------------------------------------------------
// The destination secret is write-only: `secret_set` tells us one is stored, and we send
// `secret_key` only when the admin actually typed a new one.
const bk = reactive({
  enabled: false, endpoint: '', bucket: '', prefix: 'geodeploy-backups', access_key: '',
  secret_key: '', region: 'us-east-1', schedule: 'off', hour: 3, keep: 7,
  include_postgis: true, include_objects: true, include_state: true, secret_set: false,
})
// Mirrors services/backup.py::infer_region for the PLACEHOLDER only; the server does the real
// derivation when the field is left blank. Region is just a SigV4 signing input — most
// S3-compatible providers ignore it, AWS validates it, R2 wants a literal "auto", and
// Backblaze/Hetzner put the location in the hostname where it can simply be read off.
const guessedRegion = computed(() => {
  let h = (bk.endpoint || '').trim().toLowerCase().replace(/^https?:\/\//, '').split('/')[0].split(':')[0]
  if (!h) return ''
  const p = h.split('.')
  if (h.endsWith('r2.cloudflarestorage.com')) return 'auto'
  if (h.endsWith('amazonaws.com')) {
    if (p[0] === 's3' && p.length >= 3 && p[1] !== 'amazonaws') return p[1]
    if (p[0].startsWith('s3-')) return p[0].slice(3)
    return 'us-east-1'
  }
  if (h.endsWith('backblazeb2.com') && p.length >= 3) return p[1]
  if (h.endsWith('wasabisys.com') && p[0] === 's3' && p.length >= 3) return p[1]
  if (h.endsWith('your-objectstorage.com')) return 'eu-central'  // network zone, not the hel1/fsn1 location
  return ''
})

const bkRuns = ref([])
const bkSaving = ref(false)
const bkTesting = ref(false)
const bkRunning = ref(false)
const bkMsg = ref(null)
// Set only by a test that failed with "bucket_missing"; clearing it hides the offer again, so the
// button cannot linger next to a message it no longer belongs to.
const bkMissingBucket = ref('')
const bkCreating = ref(false)
// What the SERVER last told us `enabled` is, as distinct from the checkbox the admin just ticked.
const bkSavedEnabled = ref(false)
let bkPoll = null

// Why "Back up now" is disabled, in the user's terms. Mirrors the server's own guard in
// routers/backups.py::start_backup, so the button refuses for the same reasons the API would —
// rather than being enabled into a 400.
const runBlockedReason = computed(() => {
  if (bkRunning.value) return 'A backup is already running.'
  if (!bk.enabled) return 'Tick Enable backups, then Save, to run one.'
  // Enabled is SERVER state. Ticking the box only changes it here, so a button that trusted the
  // checkbox was clickable while the API still refused with "Configure and enable a backup
  // destination first" — the button promising something the server would not honour.
  if (!bkSavedEnabled.value) return 'Save first — the change has not been saved yet.'
  if (!bk.bucket || !(bk.secret_key || bk.secret_set)) return 'Set the destination bucket and credentials first.'
  return ''
})

const bkRunPages = computed(() => Math.max(1, Math.ceil(bkRuns.value.length / BK_PER_PAGE)))
const bkRunsPage = computed(() =>
  bkRuns.value.slice(bkRunPage.value * BK_PER_PAGE, (bkRunPage.value + 1) * BK_PER_PAGE))
const storedPages = computed(() => Math.max(1, Math.ceil(stored.value.length / BK_PER_PAGE)))
const storedPage = computed(() =>
  stored.value.slice(storedPageIdx.value * BK_PER_PAGE, (storedPageIdx.value + 1) * BK_PER_PAGE))

function fmtBytes(b) {
  if (!b) return '-'
  if (b > 1e12) return (b / 1e12).toFixed(1) + ' TB'
  if (b > 1e9) return (b / 1e9).toFixed(1) + ' GB'
  if (b > 1e6) return (b / 1e6).toFixed(1) + ' MB'
  return (b / 1e3).toFixed(0) + ' KB'
}

// -- Manage backups (danger zone) ------------------------------------------------------------
// `stored` comes from the DESTINATION's own manifests, not our run history: that history lives in
// the state database, which is itself one of the things being backed up, so it can never be the
// authority on what exists.
// Both lists here grow without bound (a run per backup, a folder per stored backup), so they are
// PAGINATED rather than left to stretch the page — the same reasoning as the Activity log. These
// are small enough to page client-side; if either ever needs server paging, follow /audit's
// {items,total,limit,offset} shape rather than fetching everything.
const BK_PER_PAGE = 10
const bkRunPage = ref(0)
const storedPageIdx = ref(0)

const stored = ref([])
const storedLoading = ref(false)
const storedBusy = ref(null)
const storedMsg = ref(null)
const restoreTarget = ref(null)
const preflight = ref(null)
const restoreConfirm = ref('')
const restoreBusy = ref(false)
const restoreMsg = ref(null)
// The restore in flight (or the one that just ended), polled until it reaches a verdict.
const activeRestore = ref(null)
const rsRuns = ref([])
const reloadIn = ref(8)
let rsPoll = null, reloadTimer = null

async function loadRestoreRuns() {
  if (!auth.isOwner) return
  try { rsRuns.value = (await listRestoreRuns()).data } catch { /* not configured yet */ }
}

// Poll until the restore stops running. Errors are SWALLOWED rather than treated as failure:
// the restore replaces the database this API reads, so requests genuinely fail for a few seconds
// mid-way. Giving up there would report a failure for a restore that is working.
function reloadNow() { window.location.reload() }

function pollRestore(id) {
  clearTimeout(rsPoll)
  rsPoll = setTimeout(async () => {
    let run = null
    try {
      run = ((await listRestoreRuns()).data || []).find(r => r.id === id)
    } catch { /* database is being replaced — keep waiting */ }
    if (run) activeRestore.value = run
    if (!run || run.status === 'running') return pollRestore(id)
    await loadRestoreRuns()
    // Only a SUCCESSFUL restore reloads. On failure the instance is whatever the restore left
    // behind, and reloading would replace the one screen explaining what happened.
    if (run.status === 'success') {
      reloadIn.value = 8
      clearInterval(reloadTimer)
      reloadTimer = setInterval(() => {
        if (--reloadIn.value <= 0) { clearInterval(reloadTimer); window.location.reload() }
      }, 1000)
    }
  }, 2500)
}

async function loadStored() {
  if (!auth.isAdmin) return
  storedLoading.value = true
  storedMsg.value = null
  try {
    stored.value = (await listStoredBackups()).data
    if (storedPageIdx.value >= storedPages.value) storedPageIdx.value = storedPages.value - 1
  } catch (e) {
    stored.value = []
    storedMsg.value = { ok: false, text: e.response?.data?.detail || 'Could not list the destination.' }
  } finally {
    storedLoading.value = false
  }
}

async function removeStored(b) {
  if (!confirm(`Delete the backup ${b.name}? This cannot be undone.`)) return
  storedBusy.value = b.key
  storedMsg.value = null
  try {
    await deleteStoredBackup(b.key)
    stored.value = stored.value.filter(x => x.key !== b.key)
    storedMsg.value = { ok: true, text: 'Deleted.' }
  } catch (e) {
    storedMsg.value = { ok: false, text: e.response?.data?.detail || 'Could not delete.' }
  } finally {
    storedBusy.value = null
  }
}

async function openRestore(b) {
  restoreTarget.value = b
  preflight.value = null
  restoreConfirm.value = ''
  restoreMsg.value = null
  try {
    preflight.value = (await restorePreflight(b.key)).data
  } catch (e) {
    restoreMsg.value = e.response?.data?.detail || 'Could not read this backup.'
  }
}

async function confirmRestore() {
  restoreBusy.value = true
  restoreMsg.value = null
  try {
    const { data } = await startRestore({ key: restoreTarget.value.key,
                                          confirm_name: restoreConfirm.value })
    restoreTarget.value = null
    storedMsg.value = null
    activeRestore.value = data          // shows the progress panel immediately
    pollRestore(data.id)
  } catch (e) {
    restoreMsg.value = errText(e, 'Could not start the restore.')
  } finally {
    restoreBusy.value = false
  }
}

async function loadBackups() {
  if (!auth.isAdmin) return
  try {
    const { data } = await getBackupSettings()
    Object.assign(bk, data, { secret_key: '' })
    bkSavedEnabled.value = !!data.enabled
  } catch { /* not configured yet */ }
  await refreshBackupRuns()
  await loadStored()
  await loadRestoreRuns()
}

async function refreshBackupRuns() {
  try {
    bkRuns.value = (await listBackupRuns()).data
  } catch { bkRuns.value = [] }
  // Keep polling while a run is in flight so the step/percentage advances on screen.
  if (bkRunPage.value >= bkRunPages.value) bkRunPage.value = bkRunPages.value - 1
  const running = bkRuns.value.some(r => r.status === 'running')
  bkRunning.value = running
  clearTimeout(bkPoll)
  if (running) bkPoll = setTimeout(refreshBackupRuns, 4000)
}

async function saveBackups() {
  bkSaving.value = true
  bkMsg.value = null
  try {
    bkMissingBucket.value = ''      // the name may have just changed; re-test to re-offer
    const payload = { ...bk }
    delete payload.secret_set
    if (!payload.secret_key) delete payload.secret_key   // blank = keep stored
    const { data } = await updateBackupSettings(payload)
    Object.assign(bk, data, { secret_key: '' })
    bkSavedEnabled.value = !!data.enabled
    bkMsg.value = { ok: true, text: 'Saved.' }
  } catch (e) {
    bkMsg.value = { ok: false, text: e.response?.data?.detail || 'Could not save.' }
  } finally {
    bkSaving.value = false
  }
}

// The bucket-missing failure carries a structured detail; everything else is a plain string.
// Read both shapes rather than rendering "[object Object]" at the one moment the message matters.
function errText(e, fallback) {
  const d = e.response?.data?.detail
  return (typeof d === 'string' ? d : d?.message) || fallback
}

async function testBackups() {
  bkTesting.value = true
  bkMsg.value = null
  bkMissingBucket.value = ''
  try {
    const { data } = await testBackupDestination()
    bkMsg.value = { ok: true,
      text: `Destination is reachable and writable (region: ${data.region}).` }
  } catch (e) {
    const d = e.response?.data?.detail
    // Reachable, credentials good, bucket absent — the one failure fixable from right here.
    if (d?.code === 'bucket_missing') bkMissingBucket.value = d.bucket
    bkMsg.value = { ok: false, text: errText(e, 'Could not reach the destination.') }
  } finally {
    bkTesting.value = false
  }
}

async function createBucket() {
  bkCreating.value = true
  try {
    const { data } = await createBackupBucket()
    bkMissingBucket.value = ''
    bkMsg.value = { ok: true,
      text: `Created ${data.bucket} — reachable and writable (region: ${data.region}).` }
  } catch (e) {
    bkMsg.value = { ok: false, text: errText(e, 'Could not create the bucket.') }
  } finally {
    bkCreating.value = false
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
onUnmounted(() => {
  clearTimeout(bkPoll)
  // The restore poll re-arms itself and the reload countdown fires window.location.reload —
  // both must die with the component, or leaving Settings mid-restore reloads the page from
  // under whatever the user navigated to.
  clearTimeout(rsPoll)
  clearInterval(reloadTimer)
})

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
// WHICH version the Update button will move to. The CHOICE is a category — 'main' (development),
// 'release' (the newest published one), 'pick' (a specific tag) or 'branch' (unreleased work) —
// and `updateTarget` resolves it to the ref actually sent. Categories rather than one flat list
// because "the latest release" must keep meaning that as new ones are cut.
const updateChoice = ref('main')
const updatePickedTag = ref('')
const updatePickedBranch = ref('')
let updateChoiceMade = false        // once the admin picks, a re-check must not overrule them

const updateChoices = computed(() => {
  const d = updates.value.data || {}
  const opts = [{
    value: 'main', label: 'main (development)',
    hint: d.latest ? 'latest commit ' + d.latest : 'the newest code, not yet released',
  }]
  if (d.latest_release) {
    opts.push({
      value: 'release', label: 'Latest release — ' + d.latest_release.tag,
      hint: d.latest_release.sha === d.current_full ? 'what you are running' : 'the recommended version',
    })
  }
  if ((d.releases || []).length) {
    opts.push({ value: 'pick', label: 'A specific release', hint: 'pin to, or roll back to, any published version' })
  }
  if ((d.branches || []).length) {
    opts.push({ value: 'branch', label: 'Another branch (advanced)', hint: 'unreleased work in progress' })
  }
  return opts
})

// The ref actually POSTed as `target`.
const updateTarget = computed(() => {
  const d = updates.value.data || {}
  if (updateChoice.value === 'release') return d.latest_release?.tag || 'main'
  if (updateChoice.value === 'pick') return updatePickedTag.value || ''
  if (updateChoice.value === 'branch') return updatePickedBranch.value || ''
  return 'main'
})
const updateIsReinstall = computed(() => {
  const d = updates.value.data || {}
  const r = (d.releases || []).find(x => x.tag === updateTarget.value)
  if (r) return !!r.is_current
  const b = (d.branches || []).find(x => x.name === updateTarget.value)
  if (b) return b.sha === d.current_full
  return updateChoice.value === 'main' && d.up_to_date === true
})
const manualUpdateCommand = computed(() => {
  const d = updates.value.data || {}
  if (updateTarget.value === 'main' || !updateTarget.value) {
    return d.update_command || 'cd ~/geodeploy && sudo bash installer/self-update.sh'
  }
  return (d.update_command_template || 'cd ~/geodeploy && sudo bash installer/self-update.sh {ref}')
    .replace('{ref}', updateTarget.value)
})

async function checkUpdates(force = false) {
  updates.value.loading = true
  try {
    // Pressing Check bypasses the server's 10-minute GitHub cache; the automatic load on mount
    // does not (that cache is what keeps repeated page loads inside GitHub's rate limit).
    const { data } = await api.get('/admin/updates', { params: force ? { refresh: true } : {} })
    updates.value.data = data
    // Default to the channel this instance ALREADY follows, so a pinned instance is not offered the
    // development branch as its first option — the panel should not undo the operator's choice.
    if (!updateChoiceMade) {
      if (data.channel === 'release' && data.latest_release) updateChoice.value = 'release'
      else if (data.channel === 'branch') updateChoice.value = 'branch'
      else updateChoice.value = 'main'
    }
    if (!updatePickedTag.value) {
      updatePickedTag.value = data.current_tag || data.latest_release?.tag || data.releases?.[0]?.tag || ''
    }
    if (!updatePickedBranch.value) {
      // Default to the branch it is already on, so "update" on a branch means "pull its latest
      // commit" rather than "jump to some other branch".
      updatePickedBranch.value = (data.channel === 'branch' ? data.current_ref : '')
        || data.branches?.[0]?.name || ''
    }
  } catch {
    updates.value.data = { status: 'offline', current: '—' }
  } finally {
    updates.value.loading = false
  }
}
watch(updateChoice, () => { updateChoiceMade = true })
let updatePollTimer = null
let updatePollCount = 0
async function startUpdate() {
  // Ask the server what is in flight BEFORE the confirm, so the dialog can say what will actually be
  // interrupted. An update recreates the API and the worker; anything mid-run dies with them. The
  // check REPORTS rather than blocks — the admin may have a good reason, and being forced to choose
  // with no information is the problem, not the choice.
  let warning = ''
  try {
    const { data } = await api.get('/admin/update/preflight')
    if (data.count) {
      const NL = '\n'
      const lines = data.busy.slice(0, 6).map(b => '  • ' + b.what + (b.detail ? ' — ' + b.detail : ''))
      const more = data.count > 6 ? NL + '  …and ' + (data.count - 6) + ' more' : ''
      const tail = data.blocking
        ? 'Updating restarts the services, so ' + (data.blocking === 1 ? 'this' : 'these') +
          ' will be interrupted and may need re-running.'
        : 'All of it resumes automatically after the update.'
      warning = 'Work is in progress right now:' + NL + NL + lines.join(NL) + more +
        NL + NL + tail + NL + NL
    }
  } catch {
    // The preflight failing must not block an update — an admin locked out of updating because a
    // status check broke would be worse than the risk it warns about.
  }
  const target = updateTarget.value
  if (!target) return
  const onBranch = updateChoice.value === 'branch'
  const what = target === 'main' ? 'the latest development version (main)'
    : (onBranch ? `the branch ${target}` : `version ${target}`)
  // Moving BACKWARDS is legitimate — pinning to a release you trust, or undoing a bad update — but
  // it is not what "update" normally means, so say it out loud.
  const backwards = target !== 'main' && !onBranch
    && updates.value.data?.channel === 'main' && updates.value.data?.up_to_date === true
  if (!confirm(warning + `Update GeoDeploy to ${what}?`
    + (onBranch ? '\n\nThat branch is unreleased work in progress and may be broken. Back up first.' : '')
    + (backwards ? '\n\nThis instance is on the newest development code, so this installs OLDER code.' : '')
    + '\n\nServices restart briefly. If the new version is unhealthy it rolls back automatically.')) return
  updates.value.updating = true
  updatePollCount = 0
  updates.value.progress = { phase: 'running', message: 'Starting…' }
  try {
    await api.post('/admin/update', { target: updateTarget.value })
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
      setTimeout(() => checkUpdates(true), 2000)
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
