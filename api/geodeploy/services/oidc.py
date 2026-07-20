"""OIDC SSO (A-04) — generic OpenID Connect sign-in.

Config is admin-set in `SetupConfig`; the client secret is encrypted at rest (crypto.EncryptedText)
and never returned by the API. **Authlib** owns the protocol (discovery, PKCE, nonce, state, JWKS
id_token validation) — this module owns the CONFIG and the account-linking / provisioning POLICY,
which is the security-critical decision and is unit-tested via `resolve_user()`.
"""
import secrets

from authlib.integrations.starlette_client import OAuth
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import SetupConfig, User

_ROLES = ("viewer", "editor", "admin")
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


class OidcError(Exception):
    """A user-facing SSO refusal (shown on the login page). NOT a server error."""


async def get_oidc_config(db: AsyncSession) -> dict | None:
    """The active OIDC config, or None when SSO is not fully configured + enabled."""
    cfg = (await db.execute(select(SetupConfig).where(SetupConfig.id == 1))).scalar_one_or_none()
    if not cfg or not cfg.oidc_enabled:
        return None
    if not (cfg.oidc_issuer and cfg.oidc_client_id and cfg.oidc_client_secret):
        return None
    return {
        "issuer": cfg.oidc_issuer.strip(),
        "client_id": cfg.oidc_client_id.strip(),
        "client_secret": cfg.oidc_client_secret,  # EncryptedText decrypts on read
        "label": (cfg.oidc_label or "").strip() or "Single sign-on",
        "auto_provision": bool(cfg.oidc_auto_provision),
        "allowed_domains": [d.strip().lower()
                            for d in (cfg.oidc_allowed_domains or "").split(",") if d.strip()],
        "default_role": cfg.oidc_default_role if cfg.oidc_default_role in _ROLES else "viewer",
    }


def _metadata_url(issuer: str) -> str:
    issuer = issuer.rstrip("/")
    suffix = "/.well-known/openid-configuration"
    return issuer if issuer.endswith(suffix) else issuer + suffix


def build_oauth(cfg: dict) -> OAuth:
    """A fresh Authlib registry for the CURRENT config (config is dynamic/admin-set, so we register
    per-request rather than once at import). State/nonce live in the session, so a fresh object on the
    callback still validates the flow started at /login."""
    oauth = OAuth()
    oauth.register(
        name="oidc",
        server_metadata_url=_metadata_url(cfg["issuer"]),
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth


async def resolve_user(claims: dict, cfg: dict, db: AsyncSession) -> User:
    """Map validated OIDC claims → a GeoDeploy user (THE security-critical policy).

    Link by provider `sub` first (already-linked accounts), else by VERIFIED email. No account →
    create one ONLY when auto-provision is on AND the email domain is allow-listed (with the configured
    default role). Otherwise refuse. Raises `OidcError` (user-facing) on any refusal.
    """
    sub = claims.get("sub")

    # sub match = linked in a prior (verified) flow → trust it.
    if sub:
        linked = (await db.execute(select(User).where(User.oidc_sub == sub))).scalar_one_or_none()
        if linked:
            return linked

    # Every other path requires a provider-verified email.
    email = (claims.get("email") or "").lower().strip()
    if not email or claims.get("email_verified") is not True:
        raise OidcError("Your identity provider did not supply a verified email address.")

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user:
        if sub and not user.oidc_sub:
            user.oidc_sub = sub  # pin the subject on first SSO link
            await db.commit()
            await db.refresh(user)
        return user

    if not cfg["auto_provision"]:
        raise OidcError("No GeoDeploy account for this address — ask an admin to invite you.")
    domain = email.rsplit("@", 1)[-1]
    if cfg["allowed_domains"] and domain not in cfg["allowed_domains"]:
        raise OidcError(f"Single sign-on isn't allowed for {domain}.")

    role = cfg["default_role"]
    # A random bcrypt hash: password login can never match it (verify returns False cleanly, and a
    # non-bcrypt placeholder would make passlib RAISE in the login path). They can set one via reset.
    user = User(email=email, name=(claims.get("name") or email),
                hashed_password=_pwd.hash(secrets.token_urlsafe(32)),
                role=role, is_admin=role in ("admin", "owner"), oidc_sub=sub)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
