"""Renew the HTTPS certificate GeoDeploy obtained for the host's reverse proxy.

A Let's Encrypt certificate lasts 90 days, so the thing that makes automatic HTTPS *automatic* is not
the issuance — it is this. A certificate that quietly stops renewing is worse than never having had
one: the site works for two months and then fails for every visitor at once, with a browser warning
that looks like an attack, on a day nobody was touching the server.

So the design is deliberately dull:

* **Daily, not clever.** certbot decides whether renewal is due; it no-ops until the certificate is
  inside its renewal window. Calling it every day is the cheapest way to be certain, and it means a
  failure has roughly thirty chances to resolve itself before anything expires.
* **No-op when there is nothing to renew.** The tick costs one state-file read on an install that
  never asked for a certificate, which is almost all of them. It does not start a container, and it
  does not touch the proxy.
* **The reload is gated on the certificate file changing**, not on parsing certbot's output — see
  `hostproxy.renew_certificates`. Reloading somebody's web server once a day for no reason is the
  habit that module exists to avoid.

The task deliberately swallows nothing: a failure is logged with certbot's own words, because the
operator's recovery is to read them.
"""
import logging

from ..celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="geodeploy.tasks.certificates.renew_certificates")
def renew_certificates():
    """Beat tick: renew if due, reload the host proxy only if the certificate actually changed."""
    from ..services import hostproxy

    try:
        state = hostproxy.read_state()
    except Exception as exc:                      # a missing or corrupt state file is not an error
        logger.debug("certificate renewal: no state (%s)", exc)
        return {"ok": True, "renewed": False, "detail": "No state."}

    if not (state.get("certificate") or {}).get("domain"):
        return {"ok": True, "renewed": False, "detail": "No GeoDeploy-managed certificate."}

    try:
        result = hostproxy.renew_certificates()
    except Exception as exc:
        # Renewal failing is not an emergency on the day it happens — the certificate is still valid
        # for weeks — but it IS the thing that becomes an emergency if nobody ever looks. Log it at
        # error level so it reaches the places errors reach.
        logger.error("certificate renewal failed: %s", exc)
        return {"ok": False, "renewed": False, "detail": str(exc)}

    if result.get("renewed"):
        logger.info("certificate renewal: %s", result.get("detail"))
    elif not result.get("ok"):
        logger.error("certificate renewal: %s", result.get("detail"))
    return result
