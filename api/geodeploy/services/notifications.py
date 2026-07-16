"""Notification delivery — currently a logging no-op.

Invites and password-reset links are delivered as COPYABLE LINKS in the UI (user
decision 2026-07-16: no email dependency — Resend/SMTP would add per-install setup
cost for little gain at this scale). If transactional email ever lands (roadmap
C-08, optional per-install), implement these hooks; every call site already exists.
"""
import logging

logger = logging.getLogger(__name__)


def send_invitation_email(email: str, accept_url: str, role: str) -> None:
    logger.info("Invitation created for %s (role=%s) — link delivery is manual (copy from UI)", email, role)


def send_password_reset_email(email: str, reset_url: str) -> None:
    logger.info("Password-reset link created for %s — link delivery is manual (copy from UI)", email)
