"""Transactional email over SMTP. Without SMTP settings, messages go to data/outbox.log (development)."""

from __future__ import annotations

import logging
import os
import smtplib
import time
from email.message import EmailMessage

from web.db import ROOT

logger = logging.getLogger(__name__)
OUTBOX = ROOT / "data" / "outbox.log"
FOOTER = ("\n\n--\nTrue Prob AI. Analysis and information only, not betting or investment advice. 21+.\n"
          "Gambling problem? Call or text 1-800-GAMBLER.")


def configured() -> bool:
    return bool(os.environ.get("SMTP_HOST"))


def send(to: str, subject: str, body: str) -> bool:
    """Send one plain-text email. Never raises: a mail outage must not break sign-up or billing."""
    sender = os.environ.get("MAIL_FROM") or "True Prob AI <no-reply@localhost>"
    if not configured():
        OUTBOX.parent.mkdir(parents=True, exist_ok=True)
        with OUTBOX.open("a", encoding="utf-8") as fh:
            fh.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} to={to} subject={subject}\n{body}{FOOTER}\n")
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg.set_content(body + FOOTER)
    try:
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT") or 587), timeout=15) as smtp:
            smtp.starttls()
            if os.environ.get("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD") or "")
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        logger.error("Email to %s failed: %s", to, type(exc).__name__)
        return False
