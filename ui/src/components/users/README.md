# ui/src/components/users/

## Purpose
Components for the admin **Users** screen (`views/Users.vue`, route `/users`, admin/owner-only) —
workspace members, roles, and invitations (RBAC, roadmap A-01).

## Contents
- `UserRow.vue` — one member row: avatar initials, name/email, role badge
  (owner amber / admin violet / editor blue / viewer muted), creation counts, role `<select>`
  (hidden for the owner row + self), hover actions: password-reset link (KeyIcon),
  transfer-ownership (owner caller only, two-step inline confirm), delete (two-step confirm
  explaining reassign-to-owner). Errors inline from `err.response.data.detail`.
- `InviteRow.vue` — one pending invitation: email, role badge, expiry, "New link" (regenerate —
  the only way to re-obtain a link, tokens are stored hashed) and revoke.
- `InviteModal.vue` — clone of the AddSourceModal pattern: email + segmented role picker
  (viewer/editor/admin; owner is only reachable via transfer). On success it switches to a
  "link created" state showing the accept URL ONCE with a copy button.
- `CopyLink.vue` — shared read-only URL + copy-to-clipboard widget (clipboard API with
  execCommand fallback) used by the three above for invite/reset links.

## Dependencies / relationships
- `stores/users.js` (fetch/mutate wrappers over the `/api/users/*` endpoints in
  `api/geodeploy/routers/users.py`), `stores/auth.js` (`isOwner` gates transfer).
- Strings live under the `users.*` namespace in `ui/src/i18n/{en,fr}.json` (vue-i18n).
- The public accept/reset pages are `views/AcceptInvite.vue` / `views/ResetPassword.vue`
  (they consume `?token=` links minted here).

## Current status & known issues
- Invite links are copy-delivered by design (no email; notifications stub is the C-08 hook).
- Raw tokens appear exactly once per create/regenerate response — never listed.

## Last updated
2026-07-16 (A-01 phase 5)
