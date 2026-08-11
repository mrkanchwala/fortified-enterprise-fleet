"""Real outbound dispatch — Invoice/Payment-Followup's "send" actions
genuinely leave the system as an email, not just a Firestore status flag.
Sends to the operator's own inbox (a self-test pattern, not a real customer
recipient) via Gmail's SMTP relay — the right channel for this build: the
Fleet track's own audience and the invoicing/payment-reminder domain are
both email-native, unlike a chat-bot channel chosen for workspace
convenience.

Kept as a thin dependency-injected interface (same pattern as `db`) so every
existing test runs against `NullNotifier` with zero live sends, and
actions.py never imports smtplib directly.

Credential note: uses a dedicated Gmail App Password
(`FLEET_DEMO_GMAIL_APP_PASSWORD`), never the workspace's main Gmail OAuth
token (`credentials/google_token.json`) — that token is scoped across 5
different Google services for local-machine automation; reusing it inside
this hackathon's separate GCP Secret Manager would violate least-privilege
for no benefit. The App Password is single-purpose (SMTP send only) and
independently revocable.
"""

import logging
import os
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587


class EmailNotifier:
    def __init__(self, sender_address: str, app_password: str, recipient_address: str):
        self._sender = sender_address
        self._app_password = app_password
        self._recipient = recipient_address

    def send(self, subject: str, body: str) -> dict:
        message = MIMEText(body)
        message["Subject"] = subject
        message["From"] = self._sender
        message["To"] = self._recipient
        try:
            with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=10) as server:
                server.starttls()
                server.login(self._sender, self._app_password)
                server.sendmail(self._sender, [self._recipient], message.as_string())
            return {"dispatched": True, "ok": True}
        except Exception:
            logger.exception("email dispatch failed")
            return {"dispatched": False, "ok": False}


class NullNotifier:
    """Default when no email credentials are configured (local dev, tests, or
    before the App Password is provisioned). Records every call in memory so
    tests can assert dispatch actually happened, without any network call."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> dict:
        self.sent.append((subject, body))
        return {"dispatched": True, "ok": True, "test_mode": True}


def get_notifier():
    sender = os.environ.get("FLEET_DEMO_GMAIL_ADDRESS")
    app_password = os.environ.get("FLEET_DEMO_GMAIL_APP_PASSWORD")
    recipient = os.environ.get("FLEET_DEMO_RECIPIENT_EMAIL", sender)
    if sender and app_password and recipient:
        return EmailNotifier(sender, app_password, recipient)
    return NullNotifier()
