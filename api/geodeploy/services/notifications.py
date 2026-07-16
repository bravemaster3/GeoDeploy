"""Outgoing email via generic SMTP (C-08a, 2026-07-16).

One send path that works with EVERY provider — Resend, Brevo, SendGrid, Gmail, an
institutional mail relay — because they all expose SMTP (chosen over the Resend HTTP
API to avoid vendor lock-in; the user decision of 2026-07-16). stdlib smtplib only,
no new dependencies; sync sends are wrapped in a threadpool by the async helpers.

Config lives in SQLite `setup_config` (smtp_host/port/security/username/password +
email_from), admin-editable in Settings → Email. UNCONFIGURED IS FINE: invite and
password-reset links are always shown as copyable links in the UI; email is an
additional delivery channel, so `send_*` failures must never fail the operation —
they log and return False (the response's `email_sent` flag tells the UI).
"""
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..models import SetupConfig

logger = logging.getLogger(__name__)

SMTP_TIMEOUT = 15  # seconds — a hung relay must not pin the request for long


async def get_email_config(db: AsyncSession) -> dict | None:
    """The SMTP settings, or None when email is not configured (no host or no from)."""
    cfg = (await db.execute(select(SetupConfig).where(SetupConfig.id == 1))).scalar_one_or_none()
    if not cfg or not (cfg.smtp_host or "").strip() or not (cfg.email_from or "").strip():
        return None
    return {
        "host": cfg.smtp_host.strip(),
        "port": int(cfg.smtp_port or 587),
        "security": (cfg.smtp_security or "starttls").lower(),
        "username": (cfg.smtp_username or "").strip() or None,
        "password": cfg.smtp_password or None,
        "from": cfg.email_from.strip(),
    }


def _send_smtp(cfg: dict, to: str, subject: str, text: str) -> None:
    """Blocking SMTP send (call via run_in_threadpool). Raises on failure."""
    msg = EmailMessage()
    msg["From"] = formataddr(("GeoDeploy", cfg["from"]))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)

    if cfg["security"] == "tls":  # implicit TLS (e.g. port 465)
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=SMTP_TIMEOUT)
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=SMTP_TIMEOUT)
    try:
        if cfg["security"] == "starttls":
            server.starttls()
        if cfg["username"]:
            server.login(cfg["username"], cfg["password"] or "")
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


async def _try_send(db: AsyncSession, to: str, subject: str, text: str) -> bool:
    """Best-effort send: False when unconfigured or the relay errors — never raises."""
    cfg = await get_email_config(db)
    if not cfg:
        return False
    try:
        await run_in_threadpool(_send_smtp, cfg, to, subject, text)
        return True
    except Exception as exc:  # noqa: BLE001 — deliberately broad: email is best-effort
        logger.warning("Email to %s failed (%s: %s) — the copyable link remains the fallback",
                       to, type(exc).__name__, exc)
        return False


async def send_invitation_email(db: AsyncSession, email: str, accept_url: str, role: str) -> bool:
    return await _try_send(
        db, email, "You've been invited to a GeoDeploy workspace",
        f"You have been invited to join a GeoDeploy workspace as {role}.\n\n"
        f"Create your account here (the link is single-use and expires in 7 days):\n\n"
        f"{accept_url}\n\n"
        f"If you weren't expecting this invitation, you can ignore this email.\n",
    )


async def send_password_reset_email(db: AsyncSession, email: str, reset_url: str) -> bool:
    return await _try_send(
        db, email, "Reset your GeoDeploy password",
        f"A password reset was requested for your GeoDeploy account.\n\n"
        f"Set a new password here (the link is single-use and expires in 24 hours):\n\n"
        f"{reset_url}\n\n"
        f"If you didn't request this, you can ignore this email — your password is unchanged.\n",
    )


async def send_test_email(db: AsyncSession, to: str) -> None:
    """Settings → 'Send test email'. RAISES on failure so the UI can show the relay's error."""
    cfg = await get_email_config(db)
    if not cfg:
        raise RuntimeError("Email is not configured (SMTP host and from-address are required).")
    await run_in_threadpool(
        _send_smtp, cfg, to, "GeoDeploy test email",
        "This is a test email from your GeoDeploy instance.\n\n"
        "If you are reading it, outgoing email is configured correctly.\n")
