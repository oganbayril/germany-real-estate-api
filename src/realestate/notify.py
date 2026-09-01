"""Plain-text run-summary emails.

Used by the scheduled scrape and retrain jobs on the VPS. A no-op (logged, not
an error) when SMTP isn't configured, so local runs need nothing.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from realestate.config import Settings, get_settings

log = logging.getLogger(__name__)


def send_email(subject: str, body: str, *, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not settings.email_configured:
        log.info("email not configured; skipping notification: %s", subject)
        return False

    msg = EmailMessage()
    msg["From"] = settings.smtp_user
    msg["To"] = settings.email_to
    msg["Subject"] = f"[real-estate] {subject}"
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)  # type: ignore[arg-type]
            smtp.send_message(msg)
    except OSError as exc:  # network / auth / TLS
        log.error("failed to send notification %r: %s", subject, exc)
        return False
    log.info("sent notification: %s", subject)
    return True
