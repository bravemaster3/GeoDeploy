import os
import re
import shlex
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..config import get_settings
from ..database import get_db
from ..deps import require_admin, require_owner
from ..models import DeploymentRun, Portal, RasterLayer, SetupConfig, User, VectorLayer
from ..schemas import (DeploymentRunOut, EmailSettings, EmailSettingsOut, OidcSettings,
                       OidcSettingsOut, ServiceHealth, StorageStats)
from ..services import notifications
from .common import record_audit
from .users import request_origin

router = APIRouter(prefix="/admin", tags=["admin"])

# ── Software updates (read-only check) ────────────────────────────────────────
GEODEPLOY_REPO = "bravemaster3/geodeploy"
_UPDATE_CACHE: dict = {"at": 0.0, "data": None}
_UPDATE_TTL = 600  # seconds — GitHub's unauthenticated API allows 60 req/hr/IP, so cache the check


def _deployed_sha() -> str:
    """The commit this instance is actually running.

    Prefers `data/temp/deployed-sha`, written by `installer/self-update.sh::record_sha` — a
    bind-mounted file we re-read PER REQUEST. `GEODEPLOY_GIT_SHA` is only a fallback because it is
    baked into the process environment when the container is created: if anything records the sha
    after `docker compose up`, the running API reports the PREVIOUS version until the next recreate
    (exactly the "update worked but the version is wrong" bug, fixed 2026-07-29). The file has no
    such ordering trap, so the panel is right the moment the updater finishes."""
    try:
        with open(f"{get_settings().data_dir}/temp/deployed-sha") as fh:
            sha = fh.read().strip()
        if sha:
            return sha
    except OSError:
        pass
    return (get_settings().geodeploy_git_sha or "unknown").strip()


def _deployed_ref() -> str:
    """The ref this instance FOLLOWS — "main" or a release tag like "v1.0", or a branch.

    Choosing a target (PR #1) made the choice possible; this makes it STICK. A sha alone cannot
    answer "am I on a release or on development?", and that decides what the panel should say: an
    instance pinned to v1.0 is not "40 commits behind", it is where its operator put it.

    Written by `installer/{install,self-update}.sh` into the same bind-mounted `data/temp` channel as
    `deployed-sha`, so it is re-read per request and is correct the moment an update finishes.
    Defaults to "main", which is what every install tracked before this existed.
    """
    try:
        with open(f"{get_settings().data_dir}/temp/deployed-ref") as fh:
            ref = fh.read().strip()
        if ref:
            return ref.removeprefix("origin/")
    except OSError:
        pass
    return "main"


def _finalize_versions(result: dict, current: str) -> None:
    """Re-derive everything that depends on the DEPLOYED version, for the fresh AND cached path.

    The GitHub half of `result` is cached for 10 minutes; the deployed half must not be, or the panel
    keeps describing the version we were on before the update that just finished."""
    result["current_ref"] = ref = _deployed_ref()
    releases = result.get("releases") or []
    # Three channels, not two: a ref that is neither `main` nor a known tag is a BRANCH (or a bare
    # commit), and calling that "release" would have the panel announce "a new release is available"
    # to someone deliberately sitting on a feature branch.
    if ref == "main":
        result["channel"] = "main"
    elif any(r["tag"] == ref for r in releases):
        result["channel"] = "release"
    elif any(b["name"] == ref for b in result.get("branches") or []):
        result["channel"] = "branch"
    else:
        # A commit, or a ref GitHub did not list (we may simply be offline). Either way it is a
        # deliberate pin: report what it follows rather than judging it against a channel we cannot
        # confirm it is on.
        result["channel"] = "pinned"

    result["current_tag"] = next((r["tag"] for r in releases if r.get("sha") and r["sha"] == current),
                                 None)
    for r in releases:
        r["is_current"] = bool(r.get("sha")) and r["sha"] == current
    latest_rel = result.get("latest_release")
    if latest_rel:
        # Unknown current sha → offer it rather than hide it; the updater is a no-op if we are on it.
        result["release_update_available"] = (
            not current or current == "unknown" or latest_rel.get("sha") != current)
    else:
        result["release_update_available"] = False
    _finalize_update_available(result, current)


def _finalize_update_available(result: dict, current: str) -> None:
    """Decide whether to offer an update, INDEPENDENTLY of the compare call.

    `behind` needs GitHub's compare endpoint, which 404s on a diverged/rolled-back
    deploy and returns nothing when we're rate-limited — leaving `behind` null and,
    historically, hiding the Update button even when a newer commit clearly exists.
    So: if we learned a latest SHA and it differs from what's deployed, the update IS
    available. Only claim it's NOT available when we positively confirmed parity."""
    if result.get("up_to_date") is True:
        result["update_available"] = False
        return
    latest = result.get("latest_full")
    if latest and current and current != "unknown":
        result["update_available"] = (latest != current)


@router.get("/updates")
async def check_updates(refresh: bool = False, _: User = Depends(require_admin)):
    """Compare the DEPLOYED commit (see `_deployed_sha`) against the latest on GitHub `main`, so an
    admin can see whether an update is available — without SSH. Purely informational (no writes);
    the one-click updater is a separate, deliberate action."""
    import time

    import httpx

    now = time.time()
    # `refresh=true` = the admin PRESSED Check. The TTL exists to stop repeated PAGE LOADS burning
    # GitHub's 60 req/hr unauthenticated budget — not to make a deliberate check return a stale
    # answer. Without this, a commit pushed minutes ago stayed invisible for up to 10 minutes and
    # looked like the push had failed.
    if not refresh and _UPDATE_CACHE["data"] and now - _UPDATE_CACHE["at"] < _UPDATE_TTL:
        # The 10-min TTL exists for GITHUB's rate limit, not for our own version — re-read the
        # deployed sha on every call and re-derive from it, or the panel would keep showing the
        # pre-update version for up to TTL seconds after a successful update.
        cached = dict(_UPDATE_CACHE["data"])
        current = _deployed_sha()
        cached["current_full"] = current
        cached["current"] = current[:7] if current and current != "unknown" else "unknown"
        if current and current == cached.get("latest_full"):
            # We just caught up with the commit this cache entry called "latest" — the cached
            # behind/up_to_date came from BEFORE the update and would still advertise it.
            cached["up_to_date"], cached["behind"] = True, 0
        # Copy the release rows before `_finalize_versions` stamps `is_current` on them: they are
        # shared with the cache entry, and mutating them there would make a stale "you are running
        # this one" survive the next update.
        cached["releases"] = [dict(r) for r in (cached.get("releases") or [])]
        _finalize_versions(cached, current)
        return cached

    current = _deployed_sha()
    result: dict = {
        "current": current[:7] if current and current != "unknown" else "unknown",
        "current_full": current,
        "latest": None, "latest_full": None, "latest_message": None, "latest_date": None,
        "behind": None, "up_to_date": None, "update_available": None, "commits": [],
        "releases": [], "latest_release": None, "branches": [],
        "status": "ok",
        # The rollback-capable updater (builds, health-checks, and reverts if the new version is
        # unhealthy) — safer than a plain pull+build.
        "update_command": "cd ~/geodeploy && sudo bash installer/self-update.sh",
        # …and the same command aimed at a chosen version, so the UI's manual line matches what the
        # button would do. The script takes the ref as its first argument.
        "update_command_template": "cd ~/geodeploy && sudo bash installer/self-update.sh {ref}",
    }
    try:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "GeoDeploy"}
        async with httpx.AsyncClient(timeout=8, headers=headers) as client:
            # Tagged releases, so the panel can offer "hold on v1.0" as well as "track main", and
            # branches, so an unreleased feature branch can be tried on a real instance. Both are
            # best-effort INSIDE their loaders: a repo with no releases, or a rate-limited call,
            # simply means the choice is not offered — it must never stop the update CHECK from
            # answering.
            await _load_releases(client, result)
            await _load_branches(client, result)

            latest_r = await client.get(f"https://api.github.com/repos/{GEODEPLOY_REPO}/commits/main")
            latest_r.raise_for_status()
            latest = latest_r.json()
            latest_sha = latest["sha"]
            result["latest"] = latest_sha[:7]
            result["latest_full"] = latest_sha
            result["latest_message"] = latest["commit"]["message"].split("\n")[0]
            result["latest_date"] = latest["commit"]["author"]["date"]

            if current and current != "unknown":
                if current == latest_sha:
                    result["behind"] = 0
                    result["up_to_date"] = True
                else:
                    cmp_r = await client.get(
                        f"https://api.github.com/repos/{GEODEPLOY_REPO}/compare/{current}...{latest_sha}")
                    if cmp_r.status_code == 200:
                        d = cmp_r.json()
                        result["behind"] = d.get("ahead_by")          # commits latest is ahead of us
                        result["up_to_date"] = (d.get("ahead_by") == 0)
                        result["commits"] = [
                            {"sha": c["sha"][:7], "message": c["commit"]["message"].split("\n")[0]}
                            for c in d.get("commits", [])
                        ][-30:][::-1]  # newest first, capped
                    else:
                        result["up_to_date"] = False   # our commit isn't on GitHub's main (fork/dev)
    except Exception as exc:  # network/rate-limit/parse — report gracefully, don't cache the failure
        result["status"] = "offline"
        result["error"] = str(exc)
        _finalize_versions(result, current)
        return result

    _finalize_versions(result, current)
    result["checked_at"] = now          # so the UI can say how fresh the GitHub answer is
    _UPDATE_CACHE["at"] = now
    _UPDATE_CACHE["data"] = result
    return result


async def _load_releases(client, result: dict) -> None:
    """Fill `releases` + `latest_release` from GitHub.

    TWO endpoints, because neither alone is enough. `/releases` carries the human metadata (title,
    date, prerelease, notes URL) but NOT the commit a tag points at — and without a commit the panel
    cannot say which release is RUNNING, so a pinned instance had no way to know it was up to date.
    `/tags` carries the sha. They are joined on the tag name.

    A tag with no release row is still listed — it is installable, and a project that tags before it
    writes release notes should not look versionless. `is_release` says which is which.

    Failures are swallowed HERE (they used to be swallowed at the call site): an admin who cannot see
    the release list must still be able to see, and take, a normal update. The calls cost 2 of
    GitHub's 60 unauthenticated requests an hour, and the whole result is cached for `_UPDATE_TTL`.
    """
    try:
        tags_r = await client.get(
            f"https://api.github.com/repos/{GEODEPLOY_REPO}/tags", params={"per_page": 30})
        tags_r.raise_for_status()
        sha_by_tag = {t["name"]: t["commit"]["sha"] for t in tags_r.json() if t.get("commit")}
    except Exception:      # noqa: BLE001 — see docstring
        return

    meta: dict[str, dict] = {}
    try:
        rel_r = await client.get(
            f"https://api.github.com/repos/{GEODEPLOY_REPO}/releases", params={"per_page": 30})
        if rel_r.status_code == 200:
            for rel in rel_r.json():
                if rel.get("draft"):        # not published — invisible to `git fetch --tags` too
                    continue
                meta[rel["tag_name"]] = {
                    "name": rel.get("name") or rel["tag_name"],
                    "published_at": rel.get("published_at"),
                    "prerelease": bool(rel.get("prerelease")),
                    "url": rel.get("html_url"),
                }
    except Exception:      # noqa: BLE001 — tags alone are still usable
        pass

    releases = []
    for tag, sha in sha_by_tag.items():
        m = meta.get(tag, {})
        releases.append({
            "tag": tag, "sha": sha, "name": m.get("name") or tag,
            "published_at": m.get("published_at"), "prerelease": m.get("prerelease", False),
            "url": m.get("url"), "is_release": tag in meta,
        })
    # GitHub returns tags newest-first; keep that order rather than parsing versions ourselves —
    # "v1.10 vs v1.9" is exactly the comparison a naive sort gets wrong.
    result["releases"] = releases
    result["latest_release"] = next(
        (r for r in releases if r["is_release"] and not r["prerelease"]),
        next(iter(releases), None))


async def _load_branches(client, result: dict) -> None:
    """Fill `branches` — every branch on the repo except `main`, which has its own option.

    Deliberately a LIST rather than a text box: a typo in a free-text ref fails several minutes into
    an update, in a container, as "No such version". Offering the real names makes the wrong answer
    unreachable rather than merely discouraged.

    This is an advanced target and the UI says so. It exists because it is how a feature branch gets
    tested on a real instance — the alternative is SSH and a hand-run script, which is the dependency
    this panel exists to remove. Swallows failures like `_load_releases`.
    """
    try:
        r = await client.get(f"https://api.github.com/repos/{GEODEPLOY_REPO}/branches",
                             params={"per_page": 50})
        r.raise_for_status()
        result["branches"] = [
            {"name": b["name"], "sha": (b.get("commit") or {}).get("sha")}
            for b in r.json() if b.get("name") and b["name"] != "main"
        ]
    except Exception:      # noqa: BLE001 — see docstring
        return


def _repo_host_dir(client):
    """The repo's HOST path (needed to bind-mount it into the updater container). Derived by inspecting
    THIS (api) container's own bind mounts — the `.env` mount's Source is `<repo>/.env` on the host —
    so no extra env var is required."""
    import socket
    try:
        me = client.containers.get(socket.gethostname())
    except Exception:
        return None
    mounts = me.attrs.get("Mounts", [])
    for m in mounts:
        if m.get("Destination") == "/geodeploy/.env" and m.get("Source"):
            return os.path.dirname(m["Source"])
    for m in mounts:  # fallback via a data mount: <repo>/data/sqlite → <repo>
        if m.get("Destination") == "/data/sqlite" and m.get("Source"):
            return os.path.dirname(os.path.dirname(m["Source"]))
    return None


@router.get("/update/preflight")
async def update_preflight(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """What work is in flight right now, so an admin can decide whether this is a good moment.

    An update recreates the API and the worker. Anything mid-flight dies with them: an upload being
    converted, a layer being tiled, an export being clipped, a backup being written. Some of that
    self-heals (tiling is re-queued on worker start), some of it does not, and none of it should be
    a surprise. The endpoint REPORTS; it never blocks — the admin may have a good reason, and a
    forced choice with no information is what we are fixing, not the choice itself.
    """
    from ..models import BackupRun, RestoreRun, UploadJob

    busy: list[dict] = []

    rows = (await db.execute(select(UploadJob).where(
        UploadJob.status.in_(("queued", "processing"))))).scalars().all()
    for j in rows:
        busy.append({"kind": "upload", "what": f"{j.layer_type} layer being processed",
                     "detail": j.current_step or j.status, "recovers": False})

    tiling = (await db.execute(select(VectorLayer).where(
        VectorLayer.tile_status == "tiling"))).scalars().all()
    for l in tiling:
        # Tiling IS resumed automatically on worker start (celery_app._resume_interrupted_tiling),
        # so this one is informational rather than a reason to wait.
        busy.append({"kind": "tiling", "what": f"“{l.name}” is being tiled",
                     "detail": "resumes automatically after the update", "recovers": True})

    for model, kind, verb in ((BackupRun, "backup", "Backup"), (RestoreRun, "restore", "Restore")):
        try:
            runs = (await db.execute(select(model).where(model.status == "running"))).scalars().all()
        except Exception:
            runs = []
        for r in runs:
            busy.append({"kind": kind, "what": f"{verb} in progress",
                         "detail": getattr(r, "message", None) or "running", "recovers": False})

    return {
        "busy": busy,
        "count": len(busy),
        # Only work that will NOT pick itself back up is a real reason to wait.
        "blocking": sum(1 for b in busy if not b["recovers"]),
    }


_TARGET_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")


class UpdateRequest(BaseModel):
    """Which version to move to. `main` (the default) tracks the newest commit, as before; a tag name
    pins a released version, so an operator can hold back on a release, or step down after a bad
    one."""
    target: str = "main"


@router.post("/update", status_code=202)
async def start_update(body: UpdateRequest | None = None,
                       user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """One-click update. Launches a DETACHED helper container that runs `installer/self-update.sh`
    (pull → build → recreate the core services → health-check → **roll back** if unhealthy), then
    returns immediately; the UI polls `GET /admin/update/status`. The helper is `docker:cli` (bundles
    the compose plugin) + git; it runs OUR committed script on OUR repo (no user input), mounts the
    repo + docker socket, and joins the geodeploy network to hit the API health check. Admin + audited.
    A bad update self-heals via the script's rollback, so the worst case is 'no change applied'."""
    import docker
    settings = get_settings()

    # The target reaches `git` inside the helper. Validated HERE as well as in the script: this is
    # the boundary where untrusted input enters, and a shell command is assembled a few lines below.
    # `main` becomes `origin/main` so a stale local branch cannot win over the remote.
    target = (body.target if body else "main").strip() or "main"
    if not _TARGET_RE.match(target):
        raise HTTPException(400, "That version name contains characters that are not allowed.")
    ref = "origin/main" if target == "main" else target

    try:
        client = docker.from_env()
    except Exception as exc:
        raise HTTPException(500, f"Docker is not reachable: {exc}") from exc

    existing = _resolve_container(client, "updater")
    if existing is not None and existing.status in ("running", "created", "restarting"):
        raise HTTPException(409, "An update is already running.")
    if existing is not None:
        try:
            existing.remove(force=True)
        except Exception:
            pass

    repo = _repo_host_dir(client)
    if not repo:
        raise HTTPException(500, "Could not resolve the host repo path for the updater.")

    status_path = f"{settings.data_dir}/temp/update-status.json"
    try:
        os.makedirs(os.path.dirname(status_path), exist_ok=True)
        with open(status_path, "w") as fh:
            fh.write('{"phase":"running","message":"Starting update…","at":""}')
    except Exception:
        pass

    try:
        client.containers.run(
            image="docker:cli",
            # CRITICAL: mount the repo at its REAL host path and cd there, so `docker compose` inside this
            # helper resolves the compose file's RELATIVE bind mounts (./data/portals, ./data/sqlite) to the
            # SAME host paths the running stack uses. Mounting at a DIFFERENT path (/geodeploy) made compose
            # recreate the API against empty /geodeploy/data/* → blank portals + vanished layers after every
            # update, and could leave nginx serving a diverged mount. The mount path MUST equal the host path.
            command=["sh", "-c",
                     f'apk add --no-cache git bash >/dev/null 2>&1; cd "{repo}" && '
                     f'bash installer/self-update.sh {shlex.quote(ref)}'],
            volumes={
                repo: {"bind": repo, "mode": "rw"},
                "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
            },
            # Health-check THROUGH nginx (the public ingress), not the API directly — so a broken/down
            # nginx is caught and triggers a rollback, instead of a false "healthy".
            environment={"GEODEPLOY_HEALTH_URL": "http://nginx/health"},
            network="geodeploy",
            name="geodeploy-updater",
            detach=True,
            remove=True,
        )
    except Exception as exc:
        raise HTTPException(500, f"Failed to launch the updater: {exc}") from exc

    # History row. The API container is recreated by the update itself, so this is finalized later
    # by whoever reads /admin/update/status and sees a terminal phase — not by this request.
    try:
        run = DeploymentRun(status="running", trigger="manual", actor_name=user.name,
                            from_sha=_deployed_sha())
        db.add(run)
        await db.commit()
    except Exception:
        pass      # history must never block an update

    await record_audit(db, user, "admin.update.start", "system", None, {})
    return {"status": "started"}


@router.get("/credentials")
async def connection_details(user: User = Depends(require_owner), db: AsyncSession = Depends(get_db)):
    """The instance's OWN database and object-storage connection details, secrets included.

    OWNER-only and audited. This is the one endpoint that deliberately returns secrets, so the
    reasoning is worth stating: the credentials for the PostGIS and MinIO that GeoDeploy provisioned
    for you are generated during setup and then never shown anywhere. Wanting them is legitimate and
    common — pointing a backup destination at your own MinIO, connecting QGIS straight to PostGIS,
    running a query — and the only way to get them today is to read .env over SSH, which is exactly
    the terminal dependency the Environment tab exists to remove.

    It is not a new capability: an owner can already enable the terminal, or read the file. It makes
    an existing power convenient rather than granting a new one. Still owner-only, still audited, and
    the UI keeps the values masked until asked for.

    ## Where the values come from, and why the order changed (issue #2)

    Three copies of these credentials exist and they can DISAGREE:

      1. **`.env`** — the file Docker reads when it CREATES a container. Every service therefore runs
         on these values, and it is the file an operator greps over SSH.
      2. the **running process environment** — `.env` as of the last container create, plus whatever
         the setup wizard patched in-process.
      3. **`setup_config`** — a persisted copy in the database.

    This used to read (3) first, which made it the one source a RESTORE can replace with somebody
    else's: `pg_restore` overwrites `setup_config` with the snapshot's row, whose
    `storage_secret_key` is encrypted under the GEODEPLOY_SECRET_KEY of the instance that took the
    backup. `decrypt_secret` cannot distinguish a failed decrypt from legacy plaintext, so it hands
    back the raw Fernet blob — and the panel confidently displayed an access key and a secret that
    matched nothing, while `.env` (what the instance actually uses, and unchanged) was right all
    along. Reported as "wrong MinIO keys shown in Settings".

    So: read `.env` FIRST, fall back to the process environment, and use `setup_config` only for what
    neither has. A value that survived decryption as ciphertext is discarded rather than shown —
    displaying a Fernet blob as if it were a credential is worse than showing nothing.

    (`tasks/restore._restore_own_storage_settings` fixes the stored copy at restore time; this is the
    other half — the display must be right on an instance restored before that shipped.)
    """
    from ..models import SetupConfig
    from ..services import envfile

    cfg = (await db.execute(select(SetupConfig))).scalars().first()
    # `read_all` is documented as internal because it returns every secret in the file. This endpoint
    # is the one deliberate exception: owner-only, audited, and returning exactly these secrets IS
    # its purpose. Threadpooled — it is a file read on the event loop otherwise.
    env = await run_in_threadpool(envfile.read_all)
    await record_audit(db, user, "admin.credentials.view", "system", None, {})
    return merge_credentials(env, get_settings(), cfg)


def merge_credentials(env: dict, settings, cfg) -> dict:
    """The precedence rule itself, separated from the endpoint so it can be tested without a
    database, an app or a request — see `connection_details` for WHY the order is what it is."""
    from ..crypto import looks_encrypted

    def pick(env_key: str, attr: str):
        """.env → running settings → the persisted setup row, first non-empty wins."""
        val = (env.get(env_key) or "").strip()
        if val:
            return val
        val = getattr(settings, attr, None)
        if val:
            return val
        stored = getattr(cfg, attr, None) if cfg else None
        # Ciphertext here means the row came from a snapshot written under a different secret key.
        return None if looks_encrypted(stored) else stored

    def source(env_key: str, attr: str) -> str:
        if (env.get(env_key) or "").strip():
            return "env"
        return "environment" if getattr(settings, attr, None) else "database"

    return {
        "database": {
            "host": pick("POSTGIS_HOST", "postgis_host"),
            # int, as it always was: `.env` yields strings and this field had been a number.
            "port": _as_int(pick("POSTGIS_PORT", "postgis_port")),
            "database": pick("POSTGIS_DB", "postgis_db"),
            "user": pick("POSTGIS_USER", "postgis_user"),
            "password": pick("POSTGIS_PASSWORD", "postgis_password"),
            # Ready to paste into a client. The host is the one GeoDeploy itself uses, which on a
            # default install is the container name — reachable from the server, not the internet.
            "managed": (cfg.postgis_type if cfg else None) != "external",
            # Diagnostics only — deliberately NOT rendered. The panel answers "what are my
            # credentials"; which copy they came from is our plumbing, not the operator's question.
            "source": source("POSTGIS_PASSWORD", "postgis_password"),
        },
        "storage": {
            "endpoint": pick("STORAGE_ENDPOINT", "storage_endpoint"),
            "bucket": pick("STORAGE_BUCKET", "storage_bucket"),
            "access_key": pick("STORAGE_ACCESS_KEY", "storage_access_key"),
            "secret_key": pick("STORAGE_SECRET_KEY", "storage_secret_key"),
            "region": pick("STORAGE_REGION", "storage_region"),
            # Same precedence: a restored `setup_config` may claim someone else's storage TYPE too,
            # which would label a perfectly managed MinIO "external — you provided this".
            "managed": (pick("STORAGE_TYPE", "storage_type") or "local") in ("local", "minio"),
            "source": source("STORAGE_SECRET_KEY", "storage_secret_key"),
        },
    }


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


class EnvUpdate(BaseModel):
    values: dict[str, str]


@router.get("/env")
async def list_env(_: User = Depends(require_owner)):
    """The allow-listed environment settings and their current values.

    OWNER-only, not admin: these change how the instance runs, and one of them opens a root shell.
    The allow-list lives in services/envfile.py — anything not on it is neither returned here nor
    writable, so `.env` secrets never reach this surface.
    """
    from ..services import envfile
    return {"vars": envfile.list_editable(), "path": envfile.ENV_PATH}


@router.put("/env")
async def save_env(body: EnvUpdate, user: User = Depends(require_owner),
                   db: AsyncSession = Depends(get_db)):
    """Write the values. Returns the services that must be RECREATED for them to take effect —
    Docker reads .env when a container is created, not when it restarts, so saving alone changes
    nothing until /env/apply runs."""
    from ..services import envfile
    try:
        services = envfile.write(body.values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"Could not write the environment file: {exc}") from exc
    # Values are NOT audited — an allow-listed setting is not a secret, but logging values would make
    # the audit log a place secrets could land if the allow-list ever grew carelessly. Keys only.
    await record_audit(db, user, "admin.env.save", "system", None, {"keys": sorted(body.values)})
    return {"restart_required": services}


@router.post("/env/apply", status_code=202)
async def apply_env(body: EnvUpdate | None = None, user: User = Depends(require_owner),
                    db: AsyncSession = Depends(get_db)):
    """Recreate the given services so they pick up the new .env.

    `up -d --force-recreate`, NOT `restart`: a restart reuses the existing container, which already
    has its environment baked in from creation time. Runs in the same detached docker:cli helper the
    updater uses, mounted at the repo's REAL host path so compose resolves the relative bind mounts
    to the same host paths the running stack uses (mounting elsewhere is what once recreated the API
    against empty data directories).
    """
    import docker
    from ..services import envfile

    # Only the services the CHANGED keys actually need. Falls back to api+celery when the caller
    # names nothing, so "apply whatever is pending" still does the right thing.
    by_key = {v.key: v.services for v in envfile.EDITABLE}
    services: list[str] = []
    for key in (body.values if body and body.values else {}):
        for svc in by_key.get(key, ()):
            if svc not in services:
                services.append(svc)
    if not services:
        services = ["geodeploy-api", "celery"]

    try:
        client = docker.from_env()
    except Exception as exc:
        raise HTTPException(500, f"Docker is not reachable: {exc}") from exc
    repo = _repo_host_dir(client)
    if not repo:
        raise HTTPException(500, "Could not resolve the host repo path.")

    # Service names come from our own allow-list, never from the request body, so nothing a caller
    # sends can reach the shell.
    joined = " ".join(services)
    try:
        client.containers.run(
            image="docker:cli",
            command=["sh", "-c", f'cd "{repo}" && docker compose up -d --force-recreate {joined}'],
            volumes={
                repo: {"bind": repo, "mode": "rw"},
                "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
            },
            detach=True,
            remove=True,
            name="geodeploy-env-apply",
        )
    except Exception as exc:
        raise HTTPException(500, f"Failed to apply: {exc}") from exc

    await record_audit(db, user, "admin.env.apply", "system", None, {"services": services})
    return {"status": "applying", "services": services}


def _read_update_status() -> dict:
    """The updater's live progress file. Read from disk every time — the process that started the
    update does not survive it, so nothing can be cached in memory."""
    import json as _json
    path = f"{get_settings().data_dir}/temp/update-status.json"
    try:
        with open(path) as fh:
            return _json.load(fh)
    except FileNotFoundError:
        return {"phase": "idle", "message": "", "at": ""}
    except Exception as exc:
        return {"phase": "unknown", "message": str(exc), "at": ""}


@router.get("/update/status")
async def update_status(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Progress the updater writes to `data/temp/update-status.json`. `phase` ∈ running | success |
    rollingback | rolledback | error | idle. The API itself restarts mid-update, so the UI should
    tolerate this endpoint being briefly unreachable while services come back.

    Also closes the open deployment-history row once the phase is terminal (see
    `_reconcile_deployment`) — this poll is the first thing to run after the update finishes."""
    status = _read_update_status()
    await _reconcile_deployment(db, status)
    return status


_TERMINAL_PHASES = {"success": "success", "error": "error", "rolledback": "rolledback"}


async def _reconcile_deployment(db: AsyncSession, status: dict) -> None:
    """Close the open deployment row once the updater reaches a terminal phase.

    Deliberately driven by whoever READS the status rather than by the updater: the API process
    that started the update is gone by the time it finishes (the update recreates it), so nothing
    in-process survives to write the result.
    """
    phase = (status or {}).get("phase")
    final = _TERMINAL_PHASES.get(phase)
    if not final:
        return
    try:
        run = (await db.execute(select(DeploymentRun)
                                .where(DeploymentRun.status == "running")
                                .order_by(DeploymentRun.started_at.desc())
                                .limit(1))).scalar_one_or_none()
        if not run:
            return
        run.status = final
        run.message = (status.get("message") or "")[:500]
        run.to_sha = _deployed_sha()
        run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
    except Exception:
        pass


@router.get("/deployments", response_model=list[DeploymentRunOut])
async def list_deployments(limit: int = 20, _: User = Depends(require_admin),
                           db: AsyncSession = Depends(get_db)):
    """Update history. Before finalizing, reconcile any row left 'running' by a finished update —
    the process that started it did not survive to write the outcome."""
    await _reconcile_deployment(db, _read_update_status())
    rows = (await db.execute(select(DeploymentRun).order_by(DeploymentRun.started_at.desc())
                             .limit(max(1, min(limit, 100))))).scalars().all()
    return [DeploymentRunOut.model_validate(r) for r in rows]

# Services shown on the Settings page, in display order.
SERVICE_KEYS = ["postgres", "minio", "redis", "martin", "titiler", "nginx", "celery", "ui", "api"]
# The API container serves this very request — don't let the panel stop/restart itself.
NON_CONTROLLABLE = {"api"}
# DANGER-ZONE terminal: containers an admin may run commands IN. Deliberately EXCLUDES the containers
# that mount the Docker socket (`api`, `celery`) — a shell there is a host escape — so only leaf
# services are allowed. Gated further by geodeploy_enable_terminal (off by default) + admin + audit.
TERMINAL_ALLOWED = {"postgres", "redis", "martin", "titiler", "minio", "nginx", "ui"}


def _resolve_container(client, key: str):
    """Find a container for a service key whether it uses a fixed container_name
    (geodeploy-<key>) or Compose's auto name (geodeploy[-geodeploy]-<key>-N)."""
    try:
        return client.containers.get(f"geodeploy-{key}")
    except Exception:
        pass
    for c in client.containers.list(all=True):
        if "geodeploy" in c.name and key in c.name:
            return c
    return None


@router.get("/health", response_model=list[ServiceHealth])
async def service_health(_: User = Depends(require_admin)):
    import httpx
    import docker
    settings = get_settings()

    async def check_http(url: str):
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(url)
                return r.status_code < 400
        except Exception:
            return None

    http_ok = {
        "martin": await check_http(f"{settings.martin_url}/catalog"),
        "titiler": await check_http(f"{settings.titiler_url}/healthz"),
    }

    results = []
    try:
        client = docker.from_env()
        for key in SERVICE_KEYS:
            c = _resolve_container(client, key)
            if c is None:
                status = "stopped"
            else:
                status = c.status  # running | exited | paused | restarting | ...
                if status == "running" and http_ok.get(key) is not None:
                    status = "healthy" if http_ok[key] else "unhealthy"
            results.append(ServiceHealth(name=key, status=status, controllable=key not in NON_CONTROLLABLE))
    except Exception as e:
        results.append(ServiceHealth(name="docker", status="unhealthy", message=str(e)))

    return results


class ExecRequest(BaseModel):
    command: str


# DECLARED BEFORE the generic /services/{name}/{action} route below — and it must stay there.
# FastAPI matches in DECLARATION ORDER, so with the generic route first, POST .../{name}/exec was
# captured as action="exec" and answered "Action must be start, stop, or restart": the terminal
# could never run anything at all (reported 2026-07-30).
@router.post("/services/{name}/exec")
async def service_exec(name: str, body: ExecRequest, user: User = Depends(require_admin),
                       db: AsyncSession = Depends(get_db)):
    """DANGER ZONE — run a shell command INSIDE a container and return its output. Layered gates:
    (1) off unless `GEODEPLOY_ENABLE_TERMINAL` is set; (2) admin only; (3) only whitelisted LEAF
    containers (never api/celery — they hold the Docker socket); (4) 30s-bounded; (5) output-capped;
    (6) audited. It's a container-scoped command runner, not a host shell."""
    if not get_settings().geodeploy_enable_terminal:
        raise HTTPException(403, "Terminal is disabled. Turn it on under Settings → Infrastructure "
                                 "→ Environment (owner only), then apply so the API is recreated.")
    if name not in TERMINAL_ALLOWED:
        raise HTTPException(400, f"Terminal is not allowed for '{name}'.")
    command = (body.command or "").strip()
    if not command:
        raise HTTPException(400, "No command.")

    import docker
    try:
        client = docker.from_env()
        c = _resolve_container(client, name)
        if c is None:
            raise HTTPException(404, f"Container for '{name}' not found.")
        # `timeout 30` bounds a runaway command (present on our images); output is combined + capped.
        res = await run_in_threadpool(
            lambda: c.exec_run(["sh", "-c", f"timeout 30 {command}"], tty=False, demux=False))
        raw = res.output
        out = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
        exit_code = res.exit_code
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"exec failed: {exc}") from exc

    if len(out) > 100_000:
        out = out[:100_000] + "\n… (truncated)"
    await record_audit(db, user, "admin.terminal.exec", "service", None,
                       {"service": name, "command": command[:500], "exit_code": exit_code})
    return {"service": name, "exit_code": exit_code, "output": out}


@router.post("/services/{name}/{action}")
async def control_service(name: str, action: str, _: User = Depends(require_admin)):
    """Start / stop / restart a GeoDeploy container (Coolify-style controls)."""
    import docker
    if name not in SERVICE_KEYS or name in NON_CONTROLLABLE:
        raise HTTPException(400, f"Service '{name}' cannot be controlled.")
    if action not in ("start", "stop", "restart"):
        raise HTTPException(400, "Action must be start, stop, or restart.")
    try:
        client = docker.from_env()
        c = _resolve_container(client, name)
        if c is None:
            raise HTTPException(404, f"Container for '{name}' not found.")
        getattr(c, action)()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to {action} {name}: {exc}") from exc
    return {"status": "ok", "service": name, "action": action}


@router.get("/services/{name}/logs")
async def service_logs(name: str, tail: int = 200, timestamps: bool = True,
                       _: User = Depends(require_admin)):
    """Recent container logs for a service (READ-ONLY, admin-only). Combined stdout+stderr with
    timestamps; `tail` is capped so a huge log can't blow up the response. Admins can already read the
    host, so this is an information view, not a new privilege — and it's the safe substitute for a
    shell (no exec, no writes)."""
    import docker
    if name not in SERVICE_KEYS:
        raise HTTPException(400, f"Unknown service '{name}'.")
    tail = max(1, min(int(tail), 2000))
    try:
        client = docker.from_env()
        c = _resolve_container(client, name)
        if c is None:
            raise HTTPException(404, f"Container for '{name}' not found.")
        raw = c.logs(tail=tail, timestamps=timestamps, stdout=True, stderr=True)
        text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to read logs for {name}: {exc}") from exc
    return {"service": name, "tail": tail, "timestamps": timestamps, "logs": text}


@router.post("/reload-martin")
async def reload_martin(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from ..services.martin import regenerate_config
    result = await db.execute(
        select(VectorLayer).where(VectorLayer.status == "ready", VectorLayer.storage_backend == "postgis")
    )
    layers = [{"schema_name": l.schema_name, "table_name": l.table_name,
               "geometry_column": l.geometry_column, "id_column": l.id_column, "crs": l.crs}
              for l in result.scalars().all()]
    await regenerate_config(layers)
    return {"status": "ok", "tables": len(layers)}


async def _postgis_bytes(layers) -> int | None:
    """Sum pg_total_relation_size (data + indexes + TOAST) over the catalog's PostGIS tables.
    None when the DB can't be reached; a missing table just contributes nothing."""
    if not layers:
        return 0
    import asyncpg
    settings = get_settings()
    try:
        conn = await asyncpg.connect(settings.postgis_sync_dsn)
    except Exception:
        return None
    total = 0
    try:
        for l in layers:
            try:
                size = await conn.fetchval(
                    "SELECT pg_total_relation_size($1::regclass)",
                    f'"{l.schema_name}"."{l.table_name}"')
                total += size or 0
            except Exception:
                pass  # dropped/renamed table — the row is stale, not a reason to fail the panel
    finally:
        await conn.close()
    return total


def _s3_bytes(raster_layers, gpq_layers) -> tuple[int | None, int | None]:
    """(raster_bytes, geoparquet_bytes) from object storage — per-layer, so ATTACHED data
    (import-existing, keys outside rasters/ / vectors/) is counted too. Blocking (boto3);
    call via run_in_threadpool."""
    from ..services.minio import get_s3_client
    settings = get_settings()
    try:
        s3 = get_s3_client()
        bucket = settings.storage_bucket

        def key_size(key: str) -> int:
            try:
                return s3.head_object(Bucket=bucket, Key=key)["ContentLength"]
            except Exception:
                return 0

        def prefix_size(prefix: str) -> int:
            total = 0
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    total += obj["Size"]
            return total

        raster_total = sum(key_size(l.s3_key) for l in raster_layers if l.s3_key)

        gpq_total = 0
        for l in gpq_layers:
            key = (l.s3_key or "").rstrip("/")
            if key:
                # A prepped layer is a partitioned PREFIX (parts-<hex>/); before prep (or for a
                # raw large upload awaiting conversion) it's a single object with an extension.
                if "." in key.rsplit("/", 1)[-1]:
                    gpq_total += key_size(key)
                else:
                    gpq_total += prefix_size(key + "/")
            if l.pmtiles_key:
                gpq_total += key_size(l.pmtiles_key)
        return raster_total, gpq_total
    except Exception:
        return None, None


@router.get("/storage-stats", response_model=StorageStats)
async def storage_stats(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Instance-wide storage breakdown: PostGIS tables + S3 objects (rasters, GeoParquet incl.
    PMTiles) + published portal bundles. Measured per catalog layer — accurate for attached
    (import-existing) data too, and never counts orphans the catalog doesn't know about."""
    from starlette.concurrency import run_in_threadpool

    settings = get_settings()
    portals_dir = f"{settings.data_dir}/portals"
    bundle_bytes = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _dirs, files in os.walk(portals_dir)
        for f in files
    ) if os.path.exists(portals_dir) else 0

    vectors = (await db.execute(select(VectorLayer))).scalars().all()
    rasters = (await db.execute(select(RasterLayer))).scalars().all()
    portal_count = (await db.execute(select(func.count()).select_from(Portal))).scalar()

    postgis_layers = [l for l in vectors
                      if l.storage_backend == "postgis" and l.schema_name and l.table_name]
    gpq_layers = [l for l in vectors if l.storage_backend == "geoparquet"]

    pg_bytes = await _postgis_bytes(postgis_layers)
    raster_bytes, gpq_bytes = await run_in_threadpool(_s3_bytes, rasters, gpq_layers)

    used = sum(v for v in (pg_bytes, raster_bytes, gpq_bytes, bundle_bytes) if v)
    return StorageStats(
        used_bytes=used,
        total_bytes=None,
        vector_layers=len(vectors),
        raster_layers=len(rasters),
        portals=portal_count,
        postgis_bytes=pg_bytes,
        raster_bytes=raster_bytes,
        geoparquet_bytes=gpq_bytes,
        portal_bundle_bytes=bundle_bytes,
    )


# ── Outgoing email (generic SMTP, C-08a) ─────────────────────────────────────────────────────

async def _get_config(db: AsyncSession) -> SetupConfig:
    cfg = (await db.execute(select(SetupConfig).where(SetupConfig.id == 1))).scalar_one_or_none()
    if cfg is None:
        cfg = SetupConfig(id=1)
        db.add(cfg)
        await db.commit()
        await db.refresh(cfg)
    return cfg


def _email_out(cfg: SetupConfig) -> EmailSettingsOut:
    return EmailSettingsOut(
        smtp_host=cfg.smtp_host, smtp_port=cfg.smtp_port, smtp_security=cfg.smtp_security,
        smtp_username=cfg.smtp_username, email_from=cfg.email_from,
        has_password=bool(cfg.smtp_password),
        configured=bool((cfg.smtp_host or "").strip() and (cfg.email_from or "").strip()),
    )


@router.get("/email-settings", response_model=EmailSettingsOut)
async def get_email_settings(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return _email_out(await _get_config(db))


@router.put("/email-settings", response_model=EmailSettingsOut)
async def update_email_settings(body: EmailSettings,
                                _: User = Depends(require_admin),
                                db: AsyncSession = Depends(get_db)):
    """Partial update. The password is only overwritten when a non-empty value is sent (the UI
    leaves the field blank to keep the stored one); clearing smtp_host disables email entirely."""
    cfg = await _get_config(db)
    data = body.model_dump(exclude_unset=True)
    if data.get("smtp_password") == "":
        data.pop("smtp_password")  # blank = keep the stored secret
    for field, value in data.items():
        setattr(cfg, field, value)
    await db.commit()
    await db.refresh(cfg)
    return _email_out(cfg)


@router.post("/email-settings/test")
async def test_email(user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Send a test email to the calling admin. Raises the relay's actual error back to the UI —
    this is the one email path that is NOT best-effort, because the admin is debugging."""
    try:
        await notifications.send_test_email(db, user.email)
    except Exception as exc:  # noqa: BLE001 — surface whatever the relay said
        raise HTTPException(502, f"Test email failed: {exc}") from exc
    return {"status": "ok", "to": user.email}


# ── OIDC SSO settings (A-04) ─────────────────────────────────────────────────────────────────────

def _oidc_out(cfg: SetupConfig, request: Request) -> OidcSettingsOut:
    return OidcSettingsOut(
        oidc_enabled=bool(cfg.oidc_enabled),
        oidc_issuer=cfg.oidc_issuer,
        oidc_client_id=cfg.oidc_client_id,
        oidc_label=cfg.oidc_label,
        oidc_auto_provision=bool(cfg.oidc_auto_provision),
        oidc_allowed_domains=cfg.oidc_allowed_domains,
        oidc_default_role=cfg.oidc_default_role or "viewer",
        has_client_secret=bool(cfg.oidc_client_secret),  # never return the secret itself
        redirect_uri=request_origin(request) + "/api/auth/oidc/callback",
    )


@router.get("/oidc-settings", response_model=OidcSettingsOut)
async def get_oidc_settings(request: Request, _: User = Depends(require_admin),
                            db: AsyncSession = Depends(get_db)):
    return _oidc_out(await _get_config(db), request)


@router.put("/oidc-settings", response_model=OidcSettingsOut)
async def update_oidc_settings(body: OidcSettings, request: Request,
                               _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Partial update. The client secret is only overwritten when a non-empty value is sent (the UI
    leaves it blank to keep the stored one). The secret is encrypted at rest via EncryptedText."""
    cfg = await _get_config(db)
    data = body.model_dump(exclude_unset=True)
    if data.get("oidc_client_secret") == "":
        data.pop("oidc_client_secret")  # blank = keep the stored secret
    for field, value in data.items():
        setattr(cfg, field, value)
    await db.commit()
    await db.refresh(cfg)
    return _oidc_out(cfg, request)
