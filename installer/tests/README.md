# installer/tests/

## Purpose
The test plan and the tests for **where GeoDeploy publishes itself** — port selection, the installer's
question, `set-port.sh`, and the reverse-proxy configuration the dashboard generates.

These are shell tests because the thing under test is shell, and because the failures worth catching
are *observable* ones: a port actually bound, a decoy still serving, a prompt that actually appeared
on a pty. Nothing here asserts on an internal variable that a real operator could not see.

The Python half of the feature (`services/deployment.py`, the three `/admin/deployment` endpoints,
`/api/public/whoami`) is covered by `api/tests/test_deployment.py` and
`api/tests/test_deployment_api.py`, and runs in CI.

## Running them

They need a real Docker daemon and some free ports, so they are **not** in CI — a shared runner
cannot be asked for port 80. Run them on a machine you do not mind disturbing:

```bash
bash installer/tests/run-all.sh              # everything except the port-80 suite
GD_PORT80=1 bash installer/tests/run-all.sh  # …including it — see the warning below

bash installer/tests/test-unit.sh      # ~1s,  no Docker at all
bash installer/tests/test-proxy.sh     # ~1m,  pulls the nginx and caddy images
bash installer/tests/test-install.sh   # ~12m, full install cycles
bash installer/tests/test-ports.sh     # ~8m
bash installer/tests/test-second-install.sh  # ~5s, read-only; needs another install to mean anything
bash installer/tests/test-port80.sh    # ~6m,  NEEDS PORT 80 — see the warning below

# On FAKE MACHINES — privileged containers with their own dockerd. They touch nothing on this
# machine, including an instance running here. Not in run-all.sh: they build images and take a while.
bash installer/tests/test-hostproxy.sh   # ~12m, Apply against real nginx/Apache/Caddy, 25 scenarios
bash installer/tests/test-upgrade.sh     # ~40m, real installers + updaters, old releases, branches
```

!!! Each suite prints `N passed, M failed` and exits non-zero on failure.

**`test-install.sh`, `test-ports.sh` and `test-port80.sh` disturb a GeoDeploy already on this
machine** and restore it from an `EXIT` trap. Do not run them against something you care about.

They have to: since preflight now refuses a SECOND installation (it would share the first one's
database through the `geodeploy` network's `postgres` alias), a suite that installs needs the
machine to have none. `pause_other_installs` removes only the CODE services —
`geodeploy-api geodeploy-ui celery nginx redis` — never a blanket `docker compose down`, because
postgres, minio, titiler and martin are wizard-provisioned outside Compose with fixed names and a
blanket command removes them and then cannot recreate them. Data lives in bind mounts and is
untouched. Both traps are written down because both were hit here.

`test-second-install.sh` is the exception: entirely read-only (preflight writes nothing), and it
NEEDS another installation present, so it neither pauses nor is paused.

## What is covered

### `test-unit.sh` — parsing and selection, no Docker
Every rule here decides where an instance ends up, and each one fails silently when wrong.

| Area | Cases |
| --- | --- |
| `gd_env_get` | plain · double-quoted · single-quoted · whitespace around `=` · `export` prefix · empty value · `#` inside a value · duplicate key (**last wins**, as Compose does) · missing key · **no prefix matching** (`PORT` must not match `LOOKALIKE_PORT`) · missing file |
| `gd_env_set` | replace · append · neighbours untouched · comments survive · **same inode** · no duplicate key · empty file · missing file |
| Defaults | a `.env` with none of the publish keys is `0.0.0.0:80` / `dedicated` — the back-compatibility contract |
| Candidates | environment → `.env` → built-in precedence · comma/space/semicolon · dedup preserving order · junk and out-of-range dropped · all-junk · long list |
| `gd_valid_port` | 1 · 80 · 65535 accepted; 0 · 65536 · −1 · `abc` · `80.5` · empty · whitespace rejected |
| `gd_port_in_use` | IPv4 · **IPv6-only** (the one a v4 check misses) · loopback-only · free |
| `gd_free_candidates` | filtered against live listeners · `max` respected · none free |

### `test-proxy.sh` — is the generated config real?
| Area | Cases |
| --- | --- |
| Generation | all four flavours render and name the domain; the upstream follows the configured port |
| **Validity** | the nginx block passes **`nginx -t`**; the Caddyfile passes **`caddy validate`**; the Traefik snippet parses as YAML |
| Directives | Host · X-Forwarded-Proto · X-Forwarded-For · `client_max_body_size 11G` · `proxy_request_buffering off` · timeouts · websocket upgrade · **`$scheme` not `$http_x_forwarded_proto`** (an edge must not let a client claim https) |
| Live, through a real proxy | hostname preserved · scheme forwarded · client address forwarded · a client cannot forge the scheme · a 2 MB upload succeeds |
| **Live, through a NAIVE config** | hostname becomes `127.0.0.1:8080` · no X-Forwarded-Proto · a 2 MB upload **413s** |

That last row is why the others matter: it demonstrates the cost of each directive rather than
asserting it is present.

### `test-install.sh` — choosing a port, and `test-ports.sh` — living with the choice
| Area | Cases |
| --- | --- |
| The menu | three options shown; "dedicated" explained (takes 80 · no port in the address · what fails later · nothing stopped now) |
| The free list | filtered to what is actually free · excludes busy ports · names the default |
| Option 3 | any port accepted, on the list or not · a **taken** port refused with its holder named and re-asked · a **privileged** port refused with a reason |
| `--port` | a busy port refused before anything starts, with alternatives · with a TTY, falls through to the chooser · a free port taken as given, no questions · `--dedicated` while 80 is busy refused, not forced |
| Candidates | read from `.env` · environment wins over `.env` |
| `set-port.sh` | a taken target refused with live alternatives and a copy-paste command |
| Non-interactive | piped install does not hang and does not consume its own script |
| Re-run | the port does **not** move, even with a different candidate list |
| The wedged bind | a plain `up -d` leaves it wedged; preflight catches it; `--force-recreate` recovers |
| Rollback | `set-port` to an unbindable address restores the old settings and comes back up |
| **A second GeoDeploy** | refused, naming the other install's directory (skipped when the machine has no other install to detect) |

### `test-port80.sh` — the cases that need port 80
| Area | Cases |
| --- | --- |
| Back-compat | a `.env` with none of the new keys still binds `0.0.0.0:80`; the installer records the truth and moves nothing |
| Dedicated | chosen interactively · binds `0.0.0.0:80` · reachable on the LAN address · tells you how to move it |
| Behind-proxy | binds `127.0.0.1:N` · answers on loopback · **refused from the LAN address** · leaves port 80 alone · prints the SSH tunnel · asks for and stores a domain |
| `set-port` | 80 → 8080 → 80, both directions, releasing the old port each time |

### `test-hostproxy.sh` — does Apply leave somebody else's web server alone?
Each scenario boots a throwaway Debian machine (`hostproxy/Dockerfile`) with its own dockerd and a
real web server already serving neighbour sites, then drives the branch's `services/hostproxy.py`
with `DOCKER_HOST` pointed at it — so the privileged helper's `/` is the fake machine's `/`. After
every step it photographs the config trees (sha256 of every file, target of every symlink), each
neighbour's response, the master PID, and a 6 MB download held in flight across the reload.

| Scenario | What the machine has | Expected, and asserted |
| --- | --- | --- |
| N1 | Debian nginx: default site, a sites-enabled vhost, a conf.d vhost, an HTTPS catch-all | plan writes nothing; Apply adds exactly `conf.d/geodeploy.conf`; neighbours byte-identical; same PID; the in-flight download completes; Host/X-Forwarded-* right, a client cannot forge https; a 3 MB upload passes; the HTTPS gap is its own failing step; Apply twice is idempotent; changing the domain moves it; Remove restores the tree byte-for-byte; Remove twice does nothing |
| N2 | a config already broken on disk, never reloaded | refused (R2) quoting nginx; nothing written or reloaded, every site still up |
| N3 | the name already served — second token of a one-line block, and under a wildcard | refused (R5); nothing written |
| N4 | a `geodeploy.conf` GeoDeploy did not write | refused (R1); the file byte-identical, also after Remove |
| N5 | hand-built nginx.conf including neither drop-in dir | refused (R6); nothing written |
| N6 | sites-enabled only | sites-available + symlink; Remove takes both |
| N7 | a small `server_names_hash_bucket_size` and a long name | the new file fails `nginx -t` → removed (fresh) or the previous one restored (re-apply); nothing reloaded |
| N8 | the config broken by somebody AFTER Apply, then Remove | our file removed, test fails, NOT reloaded, sites still up, their file left alone |
| N9 | nginx installed but not running | refused (`not-serving`); nothing written, nothing started |
| N10 | IPv6 disabled on the machine | applies; neighbours unaffected |
| A1 | stock Debian Apache (mod_proxy off) | refused up front with the `a2enmod` line; nothing written |
| A2 | Apache with the proxy modules | the N1 lifecycle; a graceful reload keeps the parent PID |
| A3 | the name as a second `ServerAlias` | refused (R5) |
| C1 | stock Caddyfile, no import | refused with the one import line to add; Caddyfile untouched |
| C2 | Caddyfile importing conf.d | applies; HTTPS through Caddy reaches GeoDeploy; Remove byte-identical |
| C3 | the name as `http://a, http://b {` | refused (R5, via `caddy adapt`) |
| M1 / M2 | Apache on 80 with an idle nginx / nginx on 80 with an idle Apache | the one holding the port is chosen; the idle one untouched |
| B1 | no web server | refused; nothing created |
| D1–D4 | nginx or Caddy **in a container** — plain, with a foreign `geodeploy.conf`, already serving the name | refused (`container-proxy`) with the network steps; nothing written into the mount |
| P1 | GeoDeploy still on **port 80** (dedicated, as after an update from an older release) beside a host nginx | refused (`port-80-held`, fix `set-port.sh 8080`), nothing touched; after the move Apply works and every neighbour answers as before |
| T1 | Traefik in a container | named refusal |

### `test-upgrade.sh` — existing instances, old releases, other branches
Fake machines again (stage `machine`), the REAL installers and updaters, and a bare copy of this
repository standing in for GitHub through git's `insteadOf`. Installs preload images; every update
builds for real inside the machine.

| Scenario | Expected, and asserted |
| --- | --- |
| F1–F4 | an old release (default `v1.6.4`) with this installer: refused without consent (piped, or `n` at the prompt), refused `--behind-proxy`, refused while somebody holds port 80 — the clone removed, nothing listening |
| F5 | the old release with `--dedicated`: handed to its own installer, the checkout IS that tag, serving on 80 over IPv4 and IPv6 |
| F6 / F7 | this branch: piped with no flags → behind-proxy on loopback, never 80; `--dedicated` → a bare `80:80`, IPv6 included, nothing on 443 |
| U1–U3 | install `main` with main's installer, "Update now" to this branch with **main's** self-update.sh (the copy on disk): success, nginx NOT recreated, `.env` untouched, IPv6 kept; nginx recreated later → bare `:80`, 443 released, IPv6 kept |
| U4 / U5 | downgrade to the old release with this updater (allowed on port 80), and forward again with the old release's updater |
| U6–U8 | `set-port.sh 8080`; a `.env` without PUBLISH migrated by the API at startup and by the updater, so a recreate stays on 127.0.0.1:8080 |
| U9 / U10 | behind a proxy: downgrade to the old release refused with the reason, checkout unmoved; installer re-run onto it refused before the reset |
| M1 / M2 | the manual `update.sh` from main's copy after `main` is fast-forwarded to this branch (as the merge does): finishes, nginx recreated on a bare `:80`, IPv6 kept |

## What is deliberately NOT covered, and why

- **A real domain, real DNS, a real certificate.** Verify and the DNS check are tested against
  monkeypatched resolvers (`api/tests/test_deployment_api.py`); end-to-end needs a domain nobody owns
  in CI. The parts that can be faked — Host passthrough, the scheme, the body limit — are covered
  live above.
- **The pre-launch re-check** (the port going from free to taken *between* choosing and
  `docker compose up`). Reproducing it means winning a race deterministically; the guard is three
  lines and shares `gd_port_in_use` with everything above, which is tested hard.
- **Rootless Docker, SELinux, non-Debian hosts.** Out of scope for this slice, and stated as such in
  the issue.
- **Two GeoDeploys actually RUNNING side by side.** They cannot, by design for now — five fixed
  container names and one shared network whose `postgres` alias belongs to whoever registered it
  first. What is tested is that preflight *refuses* the second install; making it work is the
  namespacing slice.
- **`--strict` docs and the UI build** are separate: `mkdocs build --strict` and `npm run build`.

## Dependencies / relationships
- `test-unit.sh` sources `../lib-deploy.sh` directly and needs only bash, python3 and `ss`.
- The others build a throwaway "GeoDeploy" under `$HOME/gdh` from the real `installer/*.sh` against a
  cut-down `docker-compose.yml` that carries the **same** `ports:` line as the real one. The scripts
  under test are used unmodified.
- WSL notes, for anyone re-running this on the machine it was written on: `/tmp` does not survive
  between `wsl.exe` invocations and background jobs die with the distro (keep everything under
  `$HOME`, run synchronously); the repo checkout has CRLF while git stores LF, so strip `\r` when
  copying scripts in; `sudo` wants a password, so the harness puts a passthrough shim on `PATH`.
- When faking a terminal, the redirect goes on `script`, **not** on the inner command:
  `script -qec "cmd" /dev/null < keys` works, `script -qec "cmd < keys" /dev/null` hangs forever
  because the prompt reads `/dev/tty` and nothing writes there.

## Current status & known issues
- 2026-11-19: `test-hostproxy.sh` 25 scenarios / 172 assertions green (new **P1**: GeoDeploy still on
  port 80 beside a host nginx is refused with nothing touched, then applies once moved to 8080).
- 2026-11-18: `test-hostproxy.sh` 24 scenarios / 159 assertions green on WSL2 + Docker 29.5.2,
  after fixing the defects it found; `test-upgrade.sh` results are recorded in the PR description.
- 2026-11-18, every suite re-run INSIDE fake machines (so port 80 and a live instance are never
  touched): unit 64/64, proxy 31/31, install 61/61, port80 32/32 (with new IPv6 assertions),
  ports 34/37. The three `ports` failures are P8, the wedged bind: on Docker 29.10 inside dind a plain
  `up -d` does not reproduce the wedge measured on WSL's Docker 29.5.2 in September, so there is no
  dead port for preflight to catch. The pre-change installer fails the same three in the same
  machine — environment, not a regression — but it means P8 is currently UNVERIFIED, not passing.
- `test-proxy.sh`'s "the edge sets the scheme" check grepped comments, and had been red since the
  2026-10-09 commented-out opt-in; it now reads active lines only. Nobody had re-run the suite.
- All five suites pass on WSL2 + Docker 29.5.2 (2026-09-12), 210 assertions in total:
  `test-unit` 54 · `test-proxy` 31 · `test-install` 61 · `test-ports` 37 · `test-second-install` 11
  · `test-port80` 28.
- Not wired into CI, for the port-80 reason above. A subset (`test-unit.sh`, and `test-proxy.sh`
  minus the live half) could be, and probably should be.

## Last updated
2026-11-18 (test-hostproxy.sh and test-upgrade.sh: fake shared machines)
2026-09-12
