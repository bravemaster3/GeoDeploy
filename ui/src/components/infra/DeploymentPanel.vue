<template>
  <!-- Owner-only. Answers "where is this instance, who can reach it, and how do I give it a
       domain?" — the questions the shared-machine install mode leaves open, and which are
       otherwise only answerable over SSH. -->
  <section class="card overflow-hidden">
    <header class="flex items-center gap-3 px-5 py-3.5 border-b border-border/60">
      <span class="w-9 h-9 rounded-lg bg-sky-500/15 text-sky-400 flex items-center justify-center flex-shrink-0">
        <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10" /><path d="M2 12h20" />
          <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
        </svg>
      </span>
      <div class="flex-1 min-w-0">
        <h2 class="text-sm font-semibold text-foreground">Deployment</h2>
        <p class="text-xs text-muted-foreground/70">Where GeoDeploy is published, and who can reach it</p>
      </div>
      <button @click="load" :disabled="busy" class="btn-secondary text-xs px-3 py-1.5">
        {{ busy ? 'Checking…' : 'Refresh' }}
      </button>
    </header>

    <div v-if="error" class="px-5 py-4 text-xs text-red-400">{{ error }}</div>

    <div v-else-if="data" class="p-5 space-y-5">
      <!-- ── The verdict. One sentence, in the operator's terms, not a table of variables. ── -->
      <div class="rounded-lg border px-4 py-3" :class="verdictClass">
        <div class="flex items-start gap-3">
          <span class="text-base leading-5 flex-shrink-0" aria-hidden="true">{{ verdictIcon }}</span>
          <div class="min-w-0 flex-1">
            <p class="text-sm font-medium text-foreground">{{ data.verdict.title }}</p>
            <p class="text-xs text-muted-foreground/85 mt-1 leading-relaxed">{{ data.verdict.detail }}</p>
            <div v-if="data.verdict.fix" class="mt-2 flex items-center gap-2 flex-wrap">
              <code class="text-[11px] font-mono bg-background/70 border border-border rounded px-2 py-1 break-all">{{ data.verdict.fix }}</code>
              <button @click="copy(data.verdict.fix, 'fix')" class="text-[11px] text-muted-foreground/70 hover:text-foreground">
                {{ copied === 'fix' ? 'Copied' : 'Copy' }}
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- ── The three facts, side by side, because their disagreements are the diagnosis. ── -->
      <dl class="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-[auto_1fr]">
        <dt class="text-muted-foreground">Mode</dt>
        <dd class="font-mono text-foreground">
          {{ data.intent.mode }}
          <span class="font-sans text-muted-foreground/70"> — {{ modeExplainer }}</span>
        </dd>

        <dt class="text-muted-foreground">Published on</dt>
        <dd class="font-mono text-foreground">
          {{ data.intent.bind }}:{{ data.intent.port }}
          <span v-if="mismatch" class="font-sans text-red-400"> — but nginx is on {{ data.reality.bind }}:{{ data.reality.port }}</span>
          <span v-else-if="data.reality.listening === false" class="font-sans text-red-400"> — nothing is listening there</span>
        </dd>

        <dt class="text-muted-foreground">On this machine</dt>
        <dd class="font-mono text-foreground">{{ data.local_url }}</dd>

        <dt class="text-muted-foreground">You are reaching it as</dt>
        <dd class="font-mono text-foreground">
          {{ data.observed.origin }}
          <span class="font-sans text-muted-foreground/70">
            — {{ data.observed.behind_outer_proxy ? 'through a reverse proxy' : 'directly' }}
          </span>
        </dd>
      </dl>
      <p class="text-[11px] text-muted-foreground/70 leading-relaxed">
        The last line is what matters most: GeoDeploy builds every link it hands out — shared links,
        portal previews, the STAC and OGC catalogues — from the address the request arrived on.
      </p>

      <!-- ── Give it a domain ────────────────────────────────────────────────────────────── -->
      <div class="border-t border-border/60 pt-4">
        <button v-if="!showDomain" @click="openDomain" class="btn-secondary text-xs px-3 py-1.5">
          {{ data.verdict.level === 'ok' ? 'Change the domain' : 'Give it a domain' }}
        </button>

        <div v-else class="space-y-4">
          <div>
            <label for="gd-domain" class="block text-xs font-medium text-foreground mb-1.5">
              The domain you want GeoDeploy to answer on
            </label>
            <div class="flex gap-2 flex-wrap">
              <input id="gd-domain" v-model="domain" type="text" placeholder="maps.example.org"
                     spellcheck="false" autocapitalize="off" @keyup.enter="checkDns"
                     class="flex-1 min-w-[14rem] text-xs font-mono bg-background text-foreground border border-border rounded-lg px-2.5 py-1.5" />
              <button @click="checkDns" :disabled="!domain || dnsBusy" class="btn-secondary text-xs px-3 py-1.5">
                {{ dnsBusy ? 'Checking…' : 'Start' }}
              </button>
            </div>
            <p class="text-[11px] text-muted-foreground/70 mt-1.5">
              A subdomain of a domain you own. You do not need a new domain for this — if you have
              <code class="font-mono">example.org</code>, something like
              <code class="font-mono">maps.example.org</code> is free to create.
            </p>
          </div>

          <!-- ── STEP 1 · DNS. Deliberately first and separate: it is the only step performed
               somewhere else entirely, and the only one with a waiting period in it. ── -->
          <div v-if="domain && (dns || cfg)" class="border border-border/60 rounded-lg overflow-hidden">
            <div class="flex items-center gap-2 px-4 py-2.5 bg-muted/30 border-b border-border/60">
              <span class="text-[11px] font-mono text-muted-foreground">STEP 1</span>
              <span class="text-xs font-medium text-foreground flex-1">Point the domain at this server</span>
              <span v-if="dns" class="text-[11px]" :class="dnsChipClass">{{ dnsChipText }}</span>
            </div>
            <div class="p-4 space-y-3">
              <p class="text-xs text-muted-foreground/85 leading-relaxed">
                In whoever manages your domain — Cloudflare, Namecheap, GoDaddy, your registrar's
                control panel — add one <strong>A record</strong>:
              </p>
              <div class="overflow-x-auto">
                <table class="text-xs font-mono w-full">
                  <tbody>
                    <tr v-for="row in dnsRecordRows" :key="row.k" class="border-b border-border/40 last:border-0">
                      <td class="py-1.5 pr-4 text-muted-foreground whitespace-nowrap">{{ row.k }}</td>
                      <td class="py-1.5 text-foreground break-all">{{ row.v }}</td>
                      <td class="py-1.5 pl-2 w-8">
                        <button v-if="row.copy" @click="copy(row.v, row.k)"
                                class="text-[11px] text-muted-foreground/70 hover:text-foreground">
                          {{ copied === row.k ? '✓' : 'Copy' }}
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p v-if="data.server_ip && data.server_ip.differs"
                 class="text-[11px] text-amber-300/90 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2 leading-relaxed">
                This server sees its own address as
                <code class="font-mono">{{ data.server_ip.outbound }}</code>, but from the internet it
                is <code class="font-mono">{{ data.server_ip.public }}</code> — it is behind NAT or a
                load balancer. Use the public one in the A record.
              </p>
              <p v-if="!(data.server_ip && data.server_ip.public)"
                 class="text-[11px] text-amber-300/90 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2 leading-relaxed">
                GeoDeploy could not confirm this server's public address from the outside. The value
                above is what the server sees for itself, which is right on most VPSes and wrong
                behind NAT — check it against your provider's control panel before relying on it.
              </p>

              <div v-if="dns" class="text-xs rounded-lg px-3 py-2 border" :class="dnsBoxClass">
                <p class="text-foreground">{{ dns.detail }}</p>
                <p v-if="dns.fix" class="text-muted-foreground/85 mt-1.5 leading-relaxed">{{ dns.fix }}</p>
              </div>

              <div class="flex items-center gap-2 flex-wrap">
                <button @click="checkDns" :disabled="dnsBusy" class="btn-secondary text-xs px-3 py-1.5">
                  {{ dnsBusy ? 'Checking…' : 'Check DNS' }}
                </button>
                <span class="text-[11px] text-muted-foreground/70">
                  A new record is usually live within minutes. Checking again is free.
                </span>
              </div>
            </div>
          </div>

          <div v-if="cfgError" class="text-xs text-red-400">{{ cfgError }}</div>

          <!-- ── STEP 2 · the reverse proxy ── -->
          <div v-if="cfg" class="border border-border/60 rounded-lg overflow-hidden">
            <div class="flex items-center gap-2 px-4 py-2.5 bg-muted/30 border-b border-border/60">
              <span class="text-[11px] font-mono text-muted-foreground">STEP 2</span>
              <span class="text-xs font-medium text-foreground flex-1">Tell your web server about it</span>
            </div>
            <div class="p-4 space-y-3">
              <!-- Two routes, shown as a choice rather than a fallback. The automatic one is for
                   people who do not want to think about nginx; the manual one is for people who
                   would rather place the file themselves, and it is not a consolation prize — it
                   is the same text, and on a machine someone else depends on it is a perfectly
                   reasonable preference. Neither is ever hidden because the other is available. -->
              <div class="flex gap-1.5 flex-wrap">
                <button @click="mode = 'auto'"
                        class="text-[11px] px-2.5 py-1 rounded-md border"
                        :class="mode === 'auto' ? 'border-sky-500/60 bg-sky-500/15 text-sky-300'
                                                : 'border-border text-muted-foreground/80 hover:text-foreground'">
                  Let GeoDeploy configure it
                </button>
                <button @click="mode = 'manual'"
                        class="text-[11px] px-2.5 py-1 rounded-md border"
                        :class="mode === 'manual' ? 'border-sky-500/60 bg-sky-500/15 text-sky-300'
                                                  : 'border-border text-muted-foreground/80 hover:text-foreground'">
                  I’ll configure it myself
                </button>
              </div>

              <!-- ── AUTOMATIC ─────────────────────────────────────────────────────────────── -->
              <div v-if="mode === 'auto'" class="space-y-3">
                <p v-if="hostBusy" class="text-xs text-muted-foreground/85">
                  Looking at what this machine runs…
                </p>
                <p v-else-if="hostError" class="text-xs text-red-400">{{ hostError }}</p>

                <template v-else-if="hostPlan">
                  <p class="text-xs text-muted-foreground/85 leading-relaxed">
                    <span v-if="hostPlan.adapter">
                      In front of GeoDeploy:
                      <strong class="text-foreground capitalize">{{ hostPlan.adapter.kind }}</strong>
                      <span v-if="hostPlan.adapter.where === 'container'">
                        , running as the container
                        <code class="font-mono">{{ hostPlan.adapter.container }}</code></span>.
                      GeoDeploy would add one new file,
                      <code class="font-mono break-all">{{ hostPlan.adapter.target }}</code>, and
                      reload it. Nothing else on this machine is read, edited or restarted.
                    </span>
                    <span v-else>
                      GeoDeploy looked at what this machine runs and cannot configure it
                      automatically. The manual route works everywhere.
                    </span>
                  </p>

                  <!-- Each refusal, in full. A blocker is not an error — it is GeoDeploy declining
                       to touch something, and the operator needs the reason to decide what to do. -->
                  <div v-for="b in hostPlan.blockers" :key="b.code"
                       class="text-xs rounded-lg px-3 py-2 border border-amber-500/30 bg-amber-500/10">
                    <p class="text-foreground font-medium">{{ b.title }}</p>
                    <p class="text-muted-foreground/85 mt-1 leading-relaxed whitespace-pre-wrap">{{ b.detail }}</p>
                    <p v-if="b.fix" class="text-amber-300/90 mt-1.5 leading-relaxed">{{ b.fix }}</p>
                  </div>

                  <p v-if="hostPlan.applied"
                     class="text-[11px] text-muted-foreground/80 bg-muted/30 border border-border/60 rounded-lg px-3 py-2 leading-relaxed">
                    GeoDeploy has already written
                    <code class="font-mono break-all">{{ hostPlan.applied.target }}</code>
                    <span v-if="hostPlan.applied.domain"> for
                      <strong class="text-foreground">{{ hostPlan.applied.domain }}</strong></span>.
                    Applying again replaces it; <strong>Remove it</strong> takes it away and reloads.
                  </p>

                  <div v-for="w in hostPlan.warnings" :key="w"
                       class="text-[11px] text-muted-foreground/80 bg-muted/30 border border-border/60 rounded-lg px-3 py-2 leading-relaxed">
                    {{ w }}
                  </div>

                  <div class="flex items-center gap-2 flex-wrap">
                    <button v-if="hostPlan.can_apply" @click="applyProxy" :disabled="applyBusy"
                            class="btn-primary text-xs px-3 py-1.5">
                      {{ applyBusy ? 'Applying…' : applied ? 'Apply again' : 'Apply' }}
                    </button>
                    <button v-else @click="mode = 'manual'" class="btn-secondary text-xs px-3 py-1.5">
                      Show me the configuration instead
                    </button>
                    <button v-if="applied" @click="removeProxy" :disabled="removeBusy"
                            class="btn-secondary text-xs px-3 py-1.5">
                      {{ removeBusy ? 'Removing…' : 'Remove it' }}
                    </button>
                    <button @click="loadHostPlan" :disabled="hostBusy"
                            class="text-[11px] text-muted-foreground/70 hover:text-foreground">
                      Re-check
                    </button>
                  </div>

                  <ul v-if="applySteps.length" class="space-y-2">
                    <li v-for="s in applySteps" :key="s.name" class="flex items-start gap-2.5 text-xs">
                      <span class="flex-shrink-0 mt-0.5" :class="s.ok ? 'text-green-400' : 'text-red-400'"
                            aria-hidden="true">{{ s.ok ? '✓' : '✗' }}</span>
                      <div class="min-w-0">
                        <span class="text-foreground">{{ s.name }}</span>
                        <p class="text-muted-foreground/80 whitespace-pre-wrap leading-relaxed">{{ s.detail }}</p>
                        <p v-if="s.fix" class="text-[11px] text-amber-300/90 mt-1 leading-relaxed">{{ s.fix }}</p>
                      </div>
                    </li>
                  </ul>

                  <!-- HTTPS is its own decision, never a side effect of Apply: it reaches an
                       external CA, publishes the hostname to a public log, and spends a rate
                       limit. Caddy says so itself and needs no button. -->
                  <div v-if="applied && hostPlan.adapter && hostPlan.adapter.tls === 'certbot'"
                       class="border-t border-border/60 pt-3 space-y-2">
                    <p class="text-xs text-foreground font-medium">HTTPS</p>
                    <p class="text-[11px] text-muted-foreground/80 leading-relaxed">
                      The domain works over plain HTTP now. GeoDeploy can get a free Let’s Encrypt
                      certificate for it and switch this block to HTTPS — running certbot in a
                      throwaway container, so <strong>nothing is installed on this machine</strong>.
                      It renews itself daily from then on.
                    </p>
                    <div class="flex gap-2 flex-wrap">
                      <input v-model="certEmail" type="email" placeholder="you@example.org"
                             spellcheck="false" autocapitalize="off"
                             class="flex-1 min-w-[12rem] text-xs font-mono bg-background text-foreground border border-border rounded-lg px-2.5 py-1.5" />
                      <button @click="getCertificate" :disabled="!certEmail || certBusy"
                              class="btn-secondary text-xs px-3 py-1.5">
                        {{ certBusy ? 'Asking Let’s Encrypt…' : 'Get a certificate' }}
                      </button>
                    </div>
                    <p class="text-[11px] text-muted-foreground/70 leading-relaxed">
                      Let’s Encrypt needs a contact address for expiry notices, and you are agreeing
                      to their subscriber terms. The name becomes public in the Certificate
                      Transparency log — that is true of every HTTPS certificate. The challenge is a
                      file fetched over port 80, so
                      <strong v-if="dns && dns.state === 'proxied'">turn Cloudflare’s orange cloud
                        off while it runs</strong><span v-else>port 80 must be reachable from the
                        internet</span>.
                    </p>
                    <ul v-if="certSteps.length" class="space-y-2 pt-1">
                      <li v-for="s in certSteps" :key="s.name" class="flex items-start gap-2.5 text-xs">
                        <span class="flex-shrink-0 mt-0.5" :class="s.ok ? 'text-green-400' : 'text-red-400'"
                              aria-hidden="true">{{ s.ok ? '✓' : '✗' }}</span>
                        <div class="min-w-0">
                          <span class="text-foreground">{{ s.name }}</span>
                          <p class="text-muted-foreground/80 whitespace-pre-wrap leading-relaxed">{{ s.detail }}</p>
                          <p v-if="s.fix" class="text-[11px] text-amber-300/90 mt-1 leading-relaxed">{{ s.fix }}</p>
                        </div>
                      </li>
                    </ul>
                  </div>

                  <p v-if="dns && dns.state === 'proxied'"
                     class="text-[11px] text-sky-300/90 bg-sky-500/10 border border-sky-500/30 rounded-lg px-3 py-2 leading-relaxed">
                    This domain is behind Cloudflare’s proxy. Cloudflare terminates HTTPS for
                    visitors, so the site is encrypted the moment the proxy works — but set SSL/TLS
                    to <strong>Flexible</strong> until this server has its own certificate, or
                    Cloudflare will refuse to talk to a plain-HTTP origin. Switch to
                    <strong>Full (strict)</strong> once you have one. certbot’s HTTP challenge needs
                    the orange cloud turned off while it runs.
                  </p>
                </template>
              </div>

              <!-- ── MANUAL — unchanged, and the only route that works on every machine ─────── -->
              <div v-else class="space-y-3">
              <div class="flex gap-1.5 flex-wrap">
                <button v-for="f in data.flavors" :key="f" @click="flavor = f; loadConfig()"
                        class="text-[11px] px-2.5 py-1 rounded-md border capitalize"
                        :class="flavor === f ? 'border-sky-500/60 bg-sky-500/15 text-sky-300'
                                             : 'border-border text-muted-foreground/80 hover:text-foreground'">
                  {{ f }}
                </button>
              </div>

              <div v-if="flavor === 'caddy'" class="text-[11px] text-emerald-300/90 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-3 py-2">
                Caddy gets the certificate itself and needs none of the header or upload settings the
                others do. If you are choosing a reverse proxy for a machine you control, this is the
                shortest correct answer.
              </div>
              <div v-for="w in cfg.warnings" :key="w"
                   class="text-[11px] text-amber-300/90 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2">
                {{ w }}
              </div>

              <ol class="text-xs text-muted-foreground/85 space-y-1 list-decimal list-inside">
                <li v-for="s in cfg.steps" :key="s">{{ s }}</li>
              </ol>

              <div>
                <div class="flex items-center justify-between mb-1.5 gap-2">
                  <code class="text-[11px] text-muted-foreground font-mono break-all">{{ cfg.path }}</code>
                  <button @click="copy(cfg.config, 'cfg')" class="btn-secondary text-[11px] px-2.5 py-1 flex-shrink-0">
                    {{ copied === 'cfg' ? 'Copied' : 'Copy' }}
                  </button>
                </div>
                <pre class="text-[11px] font-mono bg-background border border-border rounded-lg p-3 overflow-x-auto max-h-80 overflow-y-auto whitespace-pre">{{ cfg.config }}</pre>
              </div>
              </div>
            </div>
          </div>

          <!-- ── STEP 3 · prove it. Turns "I pasted something" into "it works". ── -->
          <div v-if="cfg" class="border border-border/60 rounded-lg overflow-hidden">
            <div class="flex items-center gap-2 px-4 py-2.5 bg-muted/30 border-b border-border/60">
              <span class="text-[11px] font-mono text-muted-foreground">STEP 3</span>
              <span class="text-xs font-medium text-foreground flex-1">Check that it worked</span>
            </div>
            <div class="p-4">
              <button @click="verify" :disabled="verifyBusy" class="btn-primary text-xs px-3 py-1.5">
                {{ verifyBusy ? 'Checking…' : 'Verify' }}
              </button>
              <span class="text-[11px] text-muted-foreground/70 ml-2">
                Reaches the domain from this server and confirms it lands on this instance.
              </span>

              <ul v-if="checks.length" class="mt-3 space-y-2">
                <li v-for="c in checks" :key="c.name" class="flex items-start gap-2.5 text-xs">
                  <span class="flex-shrink-0 mt-0.5" :class="c.ok ? 'text-green-400' : 'text-red-400'" aria-hidden="true">
                    {{ c.ok ? '✓' : '✗' }}
                  </span>
                  <div class="min-w-0">
                    <span class="text-foreground">{{ c.name }}</span>
                    <span class="text-muted-foreground/80"> — {{ c.detail }}</span>
                    <p v-if="c.fix" class="text-[11px] text-amber-300/90 mt-1 leading-relaxed">{{ c.fix }}</p>
                  </div>
                </li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="px-5 py-4 text-xs text-muted-foreground/70">Loading…</div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  applyHostProxy, checkDeploymentDns, getDeployment, getHostProxyPlan, getProxyConfig,
  issueCertificate, removeHostProxy, verifyDeployment,
} from '@/api'

const data = ref(null)
const busy = ref(false)
const error = ref('')

const showDomain = ref(false)
const domain = ref('')
const flavor = ref('nginx')
const cfg = ref(null)
const cfgBusy = ref(false)
const cfgError = ref('')

const verifyBusy = ref(false)
const checks = ref([])
const copied = ref('')

const dns = ref(null)
const dnsBusy = ref(false)

// The automatic route. `mode` starts on 'auto' and is moved to 'manual' by loadHostPlan() when this
// machine turns out not to be one GeoDeploy can configure — so the operator lands on the route that
// will actually work for them, while both remain one click apart.
const mode = ref('auto')
const hostPlan = ref(null)
const hostBusy = ref(false)
const hostError = ref('')
const applyBusy = ref(false)
const applySteps = ref([])
const applied = ref(false)
const removeBusy = ref(false)
const certEmail = ref('')
const certBusy = ref(false)
const certSteps = ref([])

// The A record, laid out the way a DNS control panel asks for it, so it can be copied field by
// field rather than translated from prose. `Name` is the subdomain ALONE — every panel appends the
// zone itself, and typing the full name is the single commonest way this goes wrong (you end up
// with maps.example.org.example.org).
const dnsRecordRows = computed(() => {
  const ip = data.value?.server_ip?.public || data.value?.server_ip?.outbound || ''
  const parts = (domain.value || '').trim().toLowerCase().split('.')
  const name = parts.length > 2 ? parts.slice(0, -2).join('.') : '@'
  return [
    { k: 'Type', v: 'A' },
    { k: 'Name', v: name, copy: name !== '@' },
    { k: 'Value / points to', v: ip || 'unknown — see below', copy: !!ip },
    { k: 'TTL', v: 'Auto, or 300' },
    { k: 'Proxy (Cloudflare only)', v: 'DNS only (grey cloud) to start with' },
  ]
})

const DNS_STATES = {
  ok:         { chip: 'text-green-400',  box: 'border-green-500/30 bg-green-500/10',  label: '✓ pointing here' },
  proxied:    { chip: 'text-sky-400',    box: 'border-sky-500/30 bg-sky-500/10',      label: 'behind Cloudflare' },
  unresolved: { chip: 'text-amber-400',  box: 'border-amber-500/30 bg-amber-500/10',  label: 'not resolving yet' },
  elsewhere:  { chip: 'text-red-400',    box: 'border-red-500/30 bg-red-500/10',      label: 'points elsewhere' },
}
const dnsChipClass = computed(() => DNS_STATES[dns.value?.state]?.chip || 'text-muted-foreground')
const dnsChipText = computed(() => DNS_STATES[dns.value?.state]?.label || '')
const dnsBoxClass = computed(() => DNS_STATES[dns.value?.state]?.box || 'border-border bg-muted/30')

// Docker could not be read (no socket, nginx down), so `reality` is unknown — which is NOT the same
// as a mismatch, and must not be rendered as one.
const mismatch = computed(() => {
  const d = data.value
  if (!d?.reality?.port) return false
  return d.reality.port !== d.intent.port || d.reality.bind !== d.intent.bind
})

const modeExplainer = computed(() =>
  data.value?.intent.mode === 'dedicated'
    ? 'GeoDeploy is this machine’s web server'
    : 'a reverse proxy on this machine publishes it')

const verdictIcon = computed(() =>
  ({ ok: '✓', warning: '⚠', critical: '⛔' }[data.value?.verdict.level] || '•'))

const verdictClass = computed(() => ({
  ok: 'border-green-500/30 bg-green-500/10',
  warning: 'border-amber-500/30 bg-amber-500/10',
  critical: 'border-red-500/30 bg-red-500/10',
}[data.value?.verdict.level] || 'border-border bg-muted/30'))

async function load() {
  busy.value = true; error.value = ''
  try {
    const { data: d } = await getDeployment()
    data.value = d
    // The domain typed during install, so the field is already filled the first time someone opens
    // this — they gave the answer once and should not be asked for it again.
    if (!domain.value && d.domain_hint) domain.value = d.domain_hint
  } catch (e) {
    error.value = e?.response?.data?.detail || 'Could not read the deployment settings.'
  } finally {
    busy.value = false
  }
}

function openDomain() {
  showDomain.value = true
  if (domain.value) checkDns()
}

// Checking DNS also opens step 2, so the operator can paste the proxy config while the record
// propagates rather than waiting on one before starting the other. DNS being wrong is not a reason
// to withhold the configuration — they are independent, and both have to be done either way.
async function checkDns() {
  if (!domain.value) return
  dnsBusy.value = true; checks.value = []
  try {
    const { data: d } = await checkDeploymentDns(domain.value.trim())
    dns.value = d
  } catch (e) {
    dns.value = { state: 'elsewhere', detail: e?.response?.data?.detail || 'Could not check DNS.' }
  } finally {
    dnsBusy.value = false
  }
  await loadConfig()
  await loadHostPlan()
}

async function loadConfig() {
  if (!domain.value) return
  cfgBusy.value = true; cfgError.value = ''; checks.value = []
  try {
    const { data: d } = await getProxyConfig(domain.value.trim(), flavor.value)
    cfg.value = d
  } catch (e) {
    cfg.value = null
    cfgError.value = e?.response?.data?.detail || 'Could not build the configuration.'
  } finally {
    cfgBusy.value = false
  }
}

// Called alongside loadConfig(), never instead of it: the manual configuration is always fetched, so
// switching to the manual route is instant and works even if this call fails outright.
async function loadHostPlan() {
  if (!domain.value) return
  hostBusy.value = true; hostError.value = ''; applySteps.value = []
  try {
    const { data: d } = await getHostProxyPlan(domain.value.trim())
    hostPlan.value = d
    // From the SERVER's record, not this session: a configuration applied last week must still be
    // removable from the panel, or the only way to undo it is `rm` over SSH — which skips the
    // test-before-reload that makes removal safe.
    applied.value = !!d.applied
    // A machine we cannot configure should not leave the operator looking at a disabled button.
    if (!d.can_apply) mode.value = 'manual'
  } catch (e) {
    hostPlan.value = null
    hostError.value = e?.response?.data?.detail
      || 'Could not inspect this machine. The manual route below works everywhere.'
    mode.value = 'manual'
  } finally {
    hostBusy.value = false
  }
}

async function applyProxy() {
  applyBusy.value = true; applySteps.value = []; certSteps.value = []
  try {
    const { data: d } = await applyHostProxy(domain.value.trim())
    applySteps.value = d.steps || []
    // "Applied" means the file is in place, which is also true when a later step failed — the
    // Remove button has to be offered in exactly that case, since that is when it is needed.
    applied.value = (d.steps || []).some((s) => s.name === 'Wrote the configuration' && s.ok)
    if (d.ok) await load()
  } catch (e) {
    applySteps.value = [{ name: 'Apply', ok: false, detail: e?.response?.data?.detail || 'Apply failed.' }]
  } finally {
    applyBusy.value = false
  }
}

async function removeProxy() {
  removeBusy.value = true
  try {
    const { data: d } = await removeHostProxy()
    applySteps.value = d.steps || []
    if (d.ok) { applied.value = false; await load() }
  } catch (e) {
    applySteps.value = [{ name: 'Remove', ok: false, detail: e?.response?.data?.detail || 'Remove failed.' }]
  } finally {
    removeBusy.value = false
  }
}

async function getCertificate() {
  certBusy.value = true; certSteps.value = []
  try {
    const { data: d } = await issueCertificate(domain.value.trim(), certEmail.value.trim())
    certSteps.value = d.steps || []
    if (d.ok) await load()
  } catch (e) {
    certSteps.value = [{ name: 'Certificate', ok: false,
                         detail: e?.response?.data?.detail || 'The certificate request failed.' }]
  } finally {
    certBusy.value = false
  }
}

async function verify() {
  verifyBusy.value = true; checks.value = []
  try {
    const { data: d } = await verifyDeployment(domain.value.trim())
    checks.value = d.steps || []
    if (d.ok) await load()      // the verdict changes once the domain works
  } catch (e) {
    checks.value = [{ name: 'Check', ok: false, detail: e?.response?.data?.detail || 'The check could not run.' }]
  } finally {
    verifyBusy.value = false
  }
}

async function copy(text, key) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = key
    setTimeout(() => { if (copied.value === key) copied.value = '' }, 1500)
  } catch { /* clipboard blocked — the text is on screen and selectable */ }
}

onMounted(load)
</script>
