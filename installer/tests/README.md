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
bash installer/tests/test-port80.sh    # ~6m,  NEEDS PORT 80 — see the warning below
```

!!! Each suite prints `N passed, M failed` and exits non-zero on failure.

**`test-port80.sh` stops any GeoDeploy already running on this machine** and restarts it from an
`EXIT` trap. Do not run it against something you care about. (It is written with the trap precisely
because an early version died on a quoting error and left the machine's instance down.)

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

### `test-port80.sh` — the cases that need port 80
| Area | Cases |
| --- | --- |
| Back-compat | a `.env` with none of the new keys still binds `0.0.0.0:80`; the installer records the truth and moves nothing |
| Dedicated | chosen interactively · binds `0.0.0.0:80` · reachable on the LAN address · tells you how to move it |
| Behind-proxy | binds `127.0.0.1:N` · answers on loopback · **refused from the LAN address** · leaves port 80 alone · prints the SSH tunnel · asks for and stores a domain |
| `set-port` | 80 → 8080 → 80, both directions, releasing the old port each time |

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
- **Two GeoDeploys on one machine.** Container and network names are still fixed — that is the
  namespacing slice. Preflight detects the collision, which *is* tested.
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
- All five suites pass on WSL2 + Docker 29.5.2 (2026-09-12), 210 assertions in total:
  `test-unit` 54 · `test-proxy` 31 · `test-install` 60 · `test-ports` 37 · `test-port80` 28.
- Not wired into CI, for the port-80 reason above. A subset (`test-unit.sh`, and `test-proxy.sh`
  minus the live half) could be, and probably should be.

## Last updated
2026-09-12
