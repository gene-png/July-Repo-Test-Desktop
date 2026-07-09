"""Tiny transactional-email sender (D-017 auth package).

Used by the email-verification flow. Deliberately minimal: a plain-text
message over SMTP against ``settings.smtp_host/port/from`` (MailHog in dev).

Delivery is best-effort by design - a registration must never fail because
the mail server is down. Every failure is logged and swallowed; the caller
treats send as fire-and-forget. Honours ``shield_email_delivery_enabled``:
when false (the v1 default), we log the intended send and return without
touching the network, so tests and offline dev don't block on SMTP.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.config import get_settings
from app.logging import get_logger

_log = get_logger(__name__)

_SMTP_TIMEOUT_SECONDS = 5.0


def send_email(*, to: str, subject: str, body: str) -> bool:
    """Send a plain-text email. Returns True on success, False on any failure.

    Never raises: SMTP problems are logged and reported via the return value so
    callers can proceed regardless (registration must not depend on mail).
    """
    settings = get_settings()
    if not settings.shield_email_delivery_enabled:
        _log.info("email_send_skipped", to=to, subject=subject, reason="delivery_disabled")
        return False

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(
            settings.smtp_host, settings.smtp_port, timeout=_SMTP_TIMEOUT_SECONDS
        ) as smtp:
            smtp.send_message(msg)
    except Exception as exc:  # noqa: BLE001 - best-effort delivery, never block the caller
        _log.warning("email_send_failed", to=to, subject=subject, error=str(exc))
        return False
    _log.info("email_sent", to=to, subject=subject)
    return True
