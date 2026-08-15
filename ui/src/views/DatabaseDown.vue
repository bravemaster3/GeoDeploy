<!--
  Shown when this instance IS installed but its database cannot be reached.

  Before this screen existed, that situation rendered the setup wizard — because the answers about
  what is configured live in the database we cannot read, so everything came back false and the app
  concluded "fresh server". To an operator whose instance had been running for weeks that reads as
  "my data is gone", and it offers re-installing as the remedy, which is the one action that could
  actually cause the loss it appears to describe.

  So: say what is wrong, say that the data is intact, and give the two commands that fix it.
-->
<template>
  <div class="min-h-screen flex items-center justify-center bg-background text-foreground p-4">
    <div class="w-full max-w-xl card p-6 space-y-5 shadow-2xl">
      <div>
        <h1 class="text-lg font-semibold">GeoDeploy cannot reach its database</h1>
        <p class="text-sm text-muted-foreground mt-2">
          This instance is installed and configured — <strong>your layers, portals and users are
          untouched.</strong> The database server itself is not answering, so nothing can be
          displayed until it is back.
        </p>
      </div>

      <div class="space-y-2">
        <p class="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Usually this is the database container being stopped
        </p>
        <pre class="text-xs bg-black/40 rounded p-3 overflow-x-auto"><code>docker start geodeploy-postgres</code></pre>
        <p class="text-xs text-muted-foreground">
          Then reload this page. If it stops again, the daemon log names what stopped it:
        </p>
        <pre class="text-xs bg-black/40 rounded p-3 overflow-x-auto"><code>docker logs --tail 40 geodeploy-postgres
journalctl -u docker --since "-1h" | grep -i postgres</code></pre>
      </div>

      <p class="text-xs text-muted-foreground">
        The database is provisioned outside Compose with a fixed container name, so
        <code>docker compose exec postgres</code> will report it as "not running" even when it is —
        address the container directly, as above.
      </p>

      <div class="flex items-center gap-3">
        <button class="btn-primary" :disabled="checking" @click="recheck">
          {{ checking ? 'Checking…' : 'Check again' }}
        </button>
        <span v-if="stillDown" class="text-xs text-red-400">Still unreachable.</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getSetupStatus } from '@/api'

const router = useRouter()
const checking = ref(false)
const stillDown = ref(false)
let timer = null

async function recheck() {
  checking.value = true
  stillDown.value = false
  try {
    const { data } = await getSetupStatus()
    if (!data.database_unreachable) {
      // Back up: send them where they were going, not to a dead-end success message.
      router.replace('/')
      return
    }
    stillDown.value = true
  } catch {
    stillDown.value = true
  } finally {
    checking.value = false
  }
}

onMounted(() => {
  // Poll quietly: an operator who fixes this in another window should not have to come back and
  // click anything.
  timer = setInterval(async () => {
    try {
      const { data } = await getSetupStatus()
      if (!data.database_unreachable) router.replace('/')
    } catch { /* keep waiting */ }
  }, 5000)
})

onUnmounted(() => { if (timer) clearInterval(timer) })
</script>
