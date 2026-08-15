"""Operating an instance: health, services, updates, backups, the audit log, and users.

**These routes refuse API tokens.** `deps.require_role`/`require_admin` reject a token-authenticated
request outright, so a leaked `gdp_…` cannot restart your database, read your storage credentials,
or mint more tokens. That is a deliberate security property, not an oversight — so everything in
this module needs a *session*: `geodeploy login --password`, or `Client(url, jwt=…)`.

`Users` is the exception: `/users/*` is scope-gated (`users:admin`), so a token with that scope can
manage members.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .errors import PermissionError_, ValidationError

#: Services the health endpoint reports on. `api` is deliberately not controllable — it is the
#: process serving the request that would stop it.
SERVICES = ("postgres", "minio", "redis", "martin", "titiler", "nginx", "celery", "ui", "api")
ACTIONS = ("start", "stop", "restart")


def _session_only(exc: PermissionError_) -> PermissionError_:
    """Turn the API's bare 403 into the actionable version of the same fact."""
    if exc.status == 403 and "token" in (exc.detail or "").lower():
        return PermissionError_(
            exc.status,
            "{0} — administration is session-only by design, so a leaked API token cannot "
            "reconfigure the instance. Run `geodeploy login --password` first.".format(exc.detail),
            exc.url, exc.payload)
    return exc


class Admin(object):
    def __init__(self, client: Any):
        self._c = client

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        try:
            return self._c.get(path, params)
        except PermissionError_ as exc:
            raise _session_only(exc)

    def _post(self, path: str, json: Any = None) -> Any:
        try:
            return self._c.post(path, json)
        except PermissionError_ as exc:
            raise _session_only(exc)

    # ── health & services ───────────────────────────────────────────────────────────────────────

    def health(self) -> List[Dict[str, Any]]:
        return self._get("/admin/health")

    def service(self, name: str, action: str) -> Any:
        if action not in ACTIONS:
            raise ValidationError(400, "Action must be one of {0}.".format(", ".join(ACTIONS)))
        return self._post("/admin/services/{0}/{1}".format(name, action))

    def logs(self, name: str, tail: int = 200, timestamps: bool = True) -> Any:
        return self._get("/admin/services/{0}/logs".format(name),
                         {"tail": tail, "timestamps": timestamps})

    def reload_martin(self) -> Any:
        """Regenerate Martin's config from every ready PostGIS layer — the manual recovery hook
        for a tile server that has ended up with a stale or empty config."""
        return self._post("/admin/reload-martin")

    def storage_stats(self) -> Dict[str, Any]:
        """Per-store usage. A store that could not be measured is `null`, NOT 0 — the difference
        between "the database is unreachable" and "the database is empty"."""
        return self._get("/admin/storage-stats")

    # ── updates ─────────────────────────────────────────────────────────────────────────────────

    def updates(self, refresh: bool = False) -> Dict[str, Any]:
        """What this instance could move to: `main`, the latest release, every release, branches.

        `refresh=True` bypasses the 10-minute cache. That cache protects GitHub's unauthenticated
        rate limit against page loads; a deliberate check must not answer from it, or a commit
        pushed a minute ago looks like it never landed.
        """
        return self._get("/admin/updates", {"refresh": refresh} if refresh else None)

    def preflight(self) -> Dict[str, Any]:
        """Whether an update is safe to start right now (it refuses over work in progress)."""
        return self._get("/admin/update/preflight")

    def update(self, target: Optional[str] = None) -> Dict[str, Any]:
        """Start an update. The API container restarts as part of it, so the call that STARTS an
        update is not the one that reports its outcome — poll `update_status`."""
        return self._post("/admin/update", {"target": target} if target else None)

    def update_status(self) -> Dict[str, Any]:
        return self._get("/admin/update/status")

    def deployments(self, limit: int = 20) -> Any:
        return self._get("/admin/deployments", {"limit": limit})

    def credentials(self) -> Dict[str, Any]:
        """Connection details for the managed PostGIS/MinIO. Owner-only, and audited."""
        return self._get("/admin/credentials")

    # ── the public listing ──────────────────────────────────────────────────────────────────────

    def public_index(self) -> Dict[str, Any]:
        """Whether this instance publishes `GET /api/public` — its anonymous index."""
        return self._get("/admin/public-index")

    def set_public_index(self, enabled: bool) -> Dict[str, Any]:
        """List, or stop listing, what this instance publishes.

        Discoverability, not access: a published public portal stays reachable by its link either
        way. Audited, because "why did our datasets stop appearing" deserves an answer.
        """
        try:
            return self._c.put("/admin/public-index", {"enabled": bool(enabled)})
        except PermissionError_ as exc:
            raise _session_only(exc)

    # ── audit ───────────────────────────────────────────────────────────────────────────────────

    def audit(self, limit: int = 20, offset: int = 0, action: Optional[str] = None,
              resource_type: Optional[str] = None, resource_id: Optional[str] = None,
              actor_id: Optional[int] = None, query: Optional[str] = None,
              since: Optional[str] = None, until: Optional[str] = None) -> Dict[str, Any]:
        """A PAGE of the activity log. Every filter is applied server-side before the page is cut —
        never fetch the log and filter locally, that searches only the slice you downloaded."""
        return self._get("/audit", {"limit": limit, "offset": offset, "action": action,
                                    "resource_type": resource_type, "resource_id": resource_id,
                                    "actor_id": actor_id, "q": query, "since": since,
                                    "until": until})

    def audit_actions(self) -> List[str]:
        return self._get("/audit/actions")

    # ── backups ─────────────────────────────────────────────────────────────────────────────────

    def backup_settings(self) -> Dict[str, Any]:
        return self._get("/backups/settings")

    def backup_runs(self, limit: int = 20) -> Any:
        return self._get("/backups/runs", {"limit": limit})

    def backup_stored(self) -> Any:
        """The destination's own manifests — the only trustworthy inventory, since our run table
        lives in the state database that is itself part of what gets backed up."""
        return self._get("/backups/stored")

    def backup_run(self) -> Any:
        return self._post("/backups/run")

    def backup_test(self) -> Any:
        return self._post("/backups/settings/test")


class Users(object):
    """Members, roles and invitations — `users:admin` scope, so a token can do this."""

    def __init__(self, client: Any):
        self._c = client

    def list(self) -> List[Dict[str, Any]]:
        return self._c.get("/users")

    def invite(self, email: str, role: str = "viewer") -> Dict[str, Any]:
        """Create an invitation. The raw token is returned ONCE — regenerate is the only way to get
        a link again. It is emailed too when SMTP is configured, but the link always works."""
        if role not in ("viewer", "editor", "admin"):
            raise ValidationError(400, "Role must be viewer, editor or admin (owner is transferred).")
        return self._c.post("/users/invitations", {"email": email, "role": role})

    def invitations(self) -> List[Dict[str, Any]]:
        return self._c.get("/users/invitations")

    def revoke_invitation(self, invitation_id: int) -> Any:
        return self._c.delete("/users/invitations/{0}".format(int(invitation_id)))

    def regenerate_invitation(self, invitation_id: int) -> Any:
        return self._c.post("/users/invitations/{0}/regenerate".format(int(invitation_id)))

    def set_role(self, user_id: int, role: str) -> Dict[str, Any]:
        return self._c.put("/users/{0}/role".format(int(user_id)), {"role": role})

    def delete(self, user_id: int) -> Any:
        """Delete a member. Their layers, portals and sources are REASSIGNED to the owner —
        nothing of theirs is destroyed."""
        return self._c.delete("/users/{0}".format(int(user_id)))

    def reset_link(self, user_id: int) -> Dict[str, Any]:
        return self._c.post("/users/{0}/reset-password-link".format(int(user_id)))
