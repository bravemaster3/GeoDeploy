#!/usr/bin/env bash
# Rollback-capable GeoDeploy update (Coolify-style): pull → build → recreate → HEALTH-CHECK, and if
# the new version doesn't come up healthy, ROLL BACK to the previous commit and rebuild. Safe to run
# manually today:  cd ~/geodeploy && sudo bash installer/self-update.sh
#
#   sudo bash installer/self-update.sh v1.0            # pin to a released version
#   sudo bash installer/self-update.sh feat/something  # try a branch (unreleased work)
#   sudo bash installer/self-update.sh                 # the default: origin/main
#
# It is also the script the (opt-in) one-click updater runs in a detached container. It writes machine
# -readable progress to data/update-status.json so the admin UI can poll it. NOT `set -e`: we handle
# each failure so we can roll back instead of dying half-applied.
set -uo pipefail

# Run from a COPY of ourselves. `git reset --hard origin/main` below rewrites this very file, and
# bash reads a script INCREMENTALLY by byte offset — if the bytes change under it mid-run it resumes
# at the old offset inside new content and executes garbage. Any update that touches this script
# would be at risk (this one did). Copying first makes the running program immutable; the repo path
# rides along in the environment because $0 is then /tmp/… and dirname would resolve wrong.
if [ -z "${GD_UPDATER_REPO:-}" ]; then
  GD_UPDATER_REPO="$(cd "$(dirname "$0")/.." && pwd)" || exit 1
  export GD_UPDATER_REPO
  _copy="$(mktemp "${TMPDIR:-/tmp}/gd-self-update.XXXXXX")" 2>/dev/null || _copy=""
  if [ -n "$_copy" ] && cp "$0" "$_copy" 2>/dev/null; then
    exec bash "$_copy" "$@"      # never returns
  fi
  # Couldn't copy (read-only /tmp?) — carry on in place rather than refusing to update.
fi
cd "$GD_UPDATER_REPO" || exit 1   # repo root (this script lives in installer/)
STATUS_FILE="data/temp/update-status.json"   # under data/temp (mounted into the API → the UI can poll it)
HEALTH_URL="${GEODEPLOY_HEALTH_URL:-http://localhost/health}"
HEALTH_TRIES="${GEODEPLOY_HEALTH_TRIES:-40}"   # × 3s ≈ 2 min for the stack to come back healthy
# Recreate ONLY the code services. NGINX IS DELIBERATELY EXCLUDED: it's the single ingress, so
# recreating it takes the whole site down for a few seconds (Cloudflare 521). Instead we leave it
# running and RELOAD its config gracefully (nginx -s reload — zero-downtime, and if the new config is
# invalid it keeps the old one). postgres/minio/titiler are wizard-provisioned outside Compose (fixed
# names) and a blanket `up` would collide on them — and a code update doesn't touch any of these.
CORE_SERVICES="geodeploy-api geodeploy-ui celery"

mkdir -p data/temp
_now() { date -u +%FT%TZ; }
write_status() { # phase message
  printf '{"phase":"%s","message":"%s","at":"%s"}\n' "$1" "$(printf '%s' "$2" | sed 's/"/\\"/g')" "$(_now)" > "$STATUS_FILE"
  echo "[self-update] $1: $2"
}
_http_ok() { curl -fsS "$1" >/dev/null 2>&1 || wget -q -O- "$1" >/dev/null 2>&1; }
# Record the DEPLOYED commit. Two places, deliberately:
#   .env              — read by docker compose, so the API process gets GEODEPLOY_GIT_SHA in its env.
#                       ONLY takes effect when the container is (re)created — hence the call ordering
#                       below: record BEFORE `up -d`, never after.
#   data/temp/…       — a bind-mounted file the RUNNING API re-reads per request (admin.py prefers it
#                       over the env var). This is the belt: it makes the panel correct even if some
#                       future path recreates in the wrong order, with no extra restart.
# BUG THIS FIXES (2026-07-29): the success path seded .env AFTER recreating, so the new container was
# born with the OLD sha and the Updates panel kept showing the previous version until the next update.
record_sha() { # sha
  sed -i "s|^GEODEPLOY_GIT_SHA=.*|GEODEPLOY_GIT_SHA=${1}|" .env 2>/dev/null || true
  grep -q "^GEODEPLOY_GIT_SHA=" .env 2>/dev/null || echo "GEODEPLOY_GIT_SHA=${1}" >> .env
  printf '%s' "$1" > data/temp/deployed-sha 2>/dev/null || true
}
# The REF this instance FOLLOWS, beside the sha and read the same way (bind-mounted data/temp, the
# API re-reads it per request). Choosing a target made pinning possible; recording it makes it stick
# — without this the panel keeps measuring a v1.0 instance against `main` and calling it "behind",
# which is the state its operator deliberately chose. `origin/` is stripped: the remote-tracking
# prefix is a git detail, and `main` is what the operator picked.
REF_FILE="data/temp/deployed-ref"
record_ref() { # ref
  printf '%s' "${1#origin/}" > "$REF_FILE" 2>/dev/null || true
}
# Graceful nginx config reload (picks up nginx.conf changes with NO downtime; a bad config is rejected
# and the running config is kept). Never recreates the container.
reload_nginx() { docker compose exec -T nginx nginx -t >/dev/null 2>&1 && docker compose exec -T nginx nginx -s reload >/dev/null 2>&1 || true; }

# Bind-mount safety. The API WRITES portal bundles into data/portals; nginx READS + serves them. If the
# mounted directory's inode ever shifts (a reinstall that recreated the folder, a stray delete), a
# still-running nginx keeps the OLD mount and serves STALE/404 portals while the recreated API writes to
# the fresh one — a `reload` can't re-attach a mount, only a recreate can. This is the class of bug that
# made portals ghost/blank after updates. So: drop a sentinel through the API's mount and check nginx
# sees it; recreate nginx ONLY when they've actually diverged (normal updates skip it → still zero-downtime).
ensure_nginx_mount_synced() {
  local token="mchk-$(date +%s)-$$"
  docker compose exec -T geodeploy-api sh -c "echo $token > /data/portals/.mountcheck" >/dev/null 2>&1 || return 0
  if ! docker compose exec -T nginx sh -c "grep -q $token /var/www/portals/.mountcheck" >/dev/null 2>&1; then
    echo "[self-update] nginx's portals mount is stale (diverged) — recreating nginx to re-attach"
    docker compose up -d --force-recreate nginx >/dev/null 2>&1 || true
  fi
  docker compose exec -T geodeploy-api sh -c "rm -f /data/portals/.mountcheck" >/dev/null 2>&1 || true
}

# Apply an nginx.conf change. CRITICAL: nginx.conf is a SINGLE-FILE bind mount, and git rewrites it with a
# NEW inode on update — but the running container stays bound to the OLD inode, so `nginx -s reload` reads
# STALE config and the change silently never lands (this is why CORS/route fixes appeared "not deployed").
# So: compare the running container's config to the host file; if they differ, RECREATE nginx to re-mount
# the current file. If identical, a graceful zero-downtime reload is enough (or a no-op).
apply_nginx() {
  local host_sum cont_sum
  host_sum=$(sha1sum nginx/nginx.conf 2>/dev/null | cut -d' ' -f1)
  cont_sum=$(docker compose exec -T nginx sha1sum /etc/nginx/nginx.conf 2>/dev/null | tr -d '\r' | cut -d' ' -f1)
  if [ -n "$host_sum" ] && [ "$host_sum" != "$cont_sum" ]; then
    echo "[self-update] nginx.conf changed but the container is on a stale single-file mount — recreating nginx to apply it"
    docker compose up -d --force-recreate nginx >/dev/null 2>&1 || true
  else
    reload_nginx
  fi
}


# ── Did the new code ACTUALLY get deployed? ───────────────────────────────────────────────────────
# A health check proves the stack is UP, not that it is NEW. An old-but-healthy API answers /health
# perfectly, so an update whose build silently produced nothing reported success — and `record_sha`
# had already moved the marker, so the dashboard showed the new version running over the old code.
# Every signal said "updated" while nothing had changed.
#
# Observed 2026-08-06, not hypothetical: geodeploy/api:latest was 47 hours old after an update to a
# commit merged 2 hours earlier, both `geodeploy-api` and `celery` were running pre-fix code, and the
# panel read "Up to date". The consequence was a bug the operator had already been told was fixed.
#
# The check is deliberately NARROW, so it cannot cry wolf:
#   * it only complains when the build CONTEXT for that image changed between the two commits — a
#     docs-only or templates-only update legitimately produces no new image, and must stay silent;
#   * and it separately verifies the running CONTAINER is on the image we just built, which catches
#     "built fine, recreated nothing".
image_id() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null || echo none; }
container_image_id() { # compose service name
  local cid; cid=$(docker compose ps -q "$1" 2>/dev/null | head -1)
  [ -n "$cid" ] && docker inspect -f '{{.Image}}' "$cid" 2>/dev/null || echo none
}
# Did anything under <path> change between the old and new commit?
context_changed() { # path
  [ -n "$(git diff --name-only "$OLD_SHA" "$NEW_SHA" -- "$1" 2>/dev/null)" ]
}

# Returns a human-readable problem, or nothing at all when the deploy is sound.
verify_deployed() { # image_tag  context_path  before_image_id  service...
  local tag="$1" ctx="$2" before="$3" after svc; shift 3
  after=$(image_id "$tag")
  if context_changed "$ctx" && [ "$before" = "$after" ] && [ "$after" != none ]; then
    echo "$ctx/ changed between ${OLD_SHA:0:7} and ${NEW_SHA:0:7} but $tag was not rebuilt"
    return
  fi
  for svc in "$@"; do
    if [ "$(container_image_id "$svc")" != "$after" ]; then
      echo "$svc is not running the image that was just built ($tag)"
      return
    fi
  done
}

healthy() {
  local i
  for i in $(seq 1 "$HEALTH_TRIES"); do
    if _http_ok "$HEALTH_URL"; then return 0; fi
    sleep 3
  done
  return 1
}

rollback() { # old_sha reason
  write_status rollingback "$2 — rolling back to ${1:0:7}"
  git reset --hard "$1" >/dev/null 2>&1
  # Restore the deployed-commit marker BEFORE recreating, so the restored container is born with the
  # right sha (a post-recreate write only lands on the NEXT recreate — the bug fixed 2026-07-29).
  record_sha "$1"
  record_ref "$OLD_REF"    # the ref goes back with the code, or the panel claims a version we rolled off
  docker compose build && docker compose up -d --force-recreate $CORE_SERVICES && apply_nginx
  if healthy; then
    write_status rolledback "Rolled back to ${1:0:7} ($2). No changes applied."
  else
    write_status error "Rollback attempted but the stack is still unhealthy — check 'docker compose ps' and logs."
  fi
}

OLD_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
[ "$OLD_SHA" = unknown ] && { write_status error "Not a git checkout — can't self-update."; exit 1; }

# WHAT to update to. Default `origin/main` keeps every existing caller working; a tag name pins a
# released version, so an operator can hold back, or step down after a bad one. The API passes this
# as the first argument.
#
# Validated rather than trusted: this string reaches `git`, and the API is the only caller today but
# will not always be. Letters, digits, dot, dash, slash and underscore cover every branch and tag we
# create and exclude anything that could carry an option or a shell character.
TARGET="${1:-origin/main}"
case "$TARGET" in
  *[!A-Za-z0-9._/-]*|-*) write_status error "Refusing an unsafe update target: $TARGET"; exit 1 ;;
esac

OLD_REF="$(cat "$REF_FILE" 2>/dev/null || echo main)"

write_status running "Fetching ${TARGET}"
# An install.sh clone is `--depth 1 --branch main`: SHALLOW and SINGLE-BRANCH. Two consequences that
# only bite the targets this feature exists to reach, so neither shows up on a normal update:
#   * shallow — a tag resolves to a commit whose history is absent, and `rollback` has nothing to
#     reset back into;
#   * single-branch — the refspec is `+refs/heads/main:refs/remotes/origin/main`, so NO fetch will
#     ever produce `origin/<other-branch>` and every branch target fails as "No such version".
# Both are fixed once, idempotently, before fetching. Un-shallowing a repo this size is cheap.
if [ -f "$(git rev-parse --git-dir)/shallow" ]; then
  git fetch --unshallow --tags origin >/dev/null 2>&1 || git fetch --tags origin >/dev/null 2>&1
fi
git remote set-branches origin '*' >/dev/null 2>&1 || true
# --tags as well as the branch: a released version is a TAG, and without this a fresh clone cannot
# resolve one. --force because a tag can legitimately be re-pointed upstream; --prune-tags so a tag
# DELETED or moved upstream stops resolving to the stale local copy (which would deploy code we did
# not mean and report success). Fetch all branches, not just main, so a branch target can resolve.
if ! git fetch --tags --force --prune-tags origin >/dev/null 2>&1; then
  # Older gits have no --prune-tags; fall back rather than refusing to update.
  if ! git fetch --tags --force origin >/dev/null 2>&1; then
    write_status error "git fetch failed (no network?)."; exit 1
  fi
fi
git remote prune origin >/dev/null 2>&1 || true   # a branch deleted upstream must stop resolving

# Resolve BEFORE resetting, so a typo'd or missing ref fails while the checkout is still intact
# rather than half-applied.
#
# Try the REMOTE-TRACKING ref first. A branch this checkout has never been on exists ONLY as
# `refs/remotes/origin/<name>` — the bare name resolves to nothing — so `feat/whatever` failed as
# "No such version" even though the fetch above had just brought it down. `main` and tags hid the
# bug: the API rewrites `main` to `origin/main`, and a tag IS local after `--tags`.
#
# Remote before local, deliberately: if a stale local branch of the same name exists (left by a
# manual checkout), the remote is the one the operator means.
TARGET_COMMIT=""
for _cand in "refs/remotes/origin/${TARGET#origin/}" "refs/tags/$TARGET" "$TARGET"; do
  if TARGET_COMMIT="$(git rev-parse -q --verify "${_cand}^{commit}" 2>/dev/null)" && [ -n "$TARGET_COMMIT" ]; then
    break
  fi
  TARGET_COMMIT=""
done
if [ -z "$TARGET_COMMIT" ]; then
  write_status error "No such version: ${TARGET}. Expected a release tag (e.g. v1.0), a branch, 'main', or a commit."
  exit 1
fi
if ! git reset --hard "$TARGET_COMMIT" >/dev/null 2>&1; then write_status error "git update failed."; exit 1; fi
NEW_SHA="$(git rev-parse HEAD)"
if [ "$NEW_SHA" = "$OLD_SHA" ]; then
  # Keep the deployed-commit marker honest even on a no-op — a manual `git pull` moves HEAD but leaves
  # GEODEPLOY_GIT_SHA stale, so the panel wrongly shows "behind". Sync + reload the API to pick it up.
  # The REF can change without the sha changing (pinning to the tag that happens to BE the tip of
  # main), and that is a real state change: it decides what the panel offers next.
  if ! grep -q "^GEODEPLOY_GIT_SHA=${NEW_SHA}$" .env 2>/dev/null || [ "$OLD_REF" != "${TARGET#origin/}" ]; then
    record_sha "$NEW_SHA"
    record_ref "$TARGET"
    docker compose up -d --force-recreate geodeploy-api >/dev/null 2>&1 || true
  fi
  write_status success "Already up to date (${TARGET#origin/} — ${NEW_SHA:0:7})."; exit 0
fi

write_status running "Building the new version"
# Image ids BEFORE the build, so "did this actually produce anything" is answerable afterwards.
BEFORE_api=$(image_id geodeploy/api:latest)
BEFORE_ui=$(image_id geodeploy/ui:latest)
if ! docker compose build; then rollback "$OLD_SHA" "Build failed"; exit 1; fi

write_status running "Restarting services"
# Record the new sha BEFORE the recreate: .env is read by compose at container-create time, so a
# post-recreate write would leave the running API reporting the PREVIOUS version. A failure here
# falls into rollback(), which restores the old sha the same way.
record_sha "$NEW_SHA"
record_ref "$TARGET"
if ! docker compose up -d --force-recreate $CORE_SERVICES; then rollback "$OLD_SHA" "Restart failed"; exit 1; fi
apply_nginx               # recreate-or-reload nginx so an nginx.conf change ACTUALLY lands (stale single-file mount)
ensure_nginx_mount_synced # …and recreate nginx if its data/portals mount diverged (else portals ghost/404)

write_status running "Checking health"
if ! healthy; then rollback "$OLD_SHA" "Unhealthy after update"; exit 1; fi

# HEALTHY IS NOT THE SAME AS UPDATED — see the note above `image_id`. Verify the new code is really
# what is running before claiming so, and if it is not, put the version marker BACK. A wrong marker
# is worse than a failed update: it tells the operator (and the next debugging session) that a fix
# is deployed when it is not.
DEPLOY_PROBLEM=$(verify_deployed geodeploy/api:latest api "$BEFORE_api" geodeploy-api celery)
[ -z "$DEPLOY_PROBLEM" ] && DEPLOY_PROBLEM=$(verify_deployed geodeploy/ui:latest ui "$BEFORE_ui" geodeploy-ui)
if [ -n "$DEPLOY_PROBLEM" ]; then
  record_sha "$OLD_SHA"
  record_ref "$OLD_REF"
  write_status error "Update did not take effect: ${DEPLOY_PROBLEM}. The code on disk is ${NEW_SHA:0:7} but the running containers are not. Try: docker compose build --no-cache geodeploy-api geodeploy-ui && docker compose up -d --force-recreate geodeploy-api geodeploy-ui celery"
  exit 1
fi

# Re-assert the markers (cheap, idempotent): the pre-recreate write above is the one that matters,
# this just guarantees the file/env agree if anything in between rewrote .env.
record_sha "$NEW_SHA"
record_ref "$TARGET"
if [ "${TARGET#origin/}" = main ]; then
  write_status success "Updated ${OLD_SHA:0:7} → ${NEW_SHA:0:7} and healthy."
else
  write_status success "Updated ${OLD_SHA:0:7} → ${TARGET#origin/} (${NEW_SHA:0:7}) and healthy."
fi
