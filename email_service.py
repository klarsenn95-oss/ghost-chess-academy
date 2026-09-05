"""Outbound email — Resend's HTTP API. Used for password reset links.

Started as raw Gmail SMTP, but Render (like most PaaS free tiers) blocks
outbound SMTP ports entirely (confirmed live: smtplib raised
"[Errno 101] Network is unreachable" against smtp.gmail.com:465) — an
HTTP API on port 443 is the only thing that actually gets through.

Degrades to a clear server-side log (never a silent no-op) when
credentials aren't configured yet, matching the rest of the app's
"safe when unconfigured" pattern for optional integrations."""
from __future__ import annotations

import os

import requests

RESEND_API_URL = "https://api.resend.com/emails"


def smtp_configured() -> bool:
    """Kept under its original name — callers just need "is email sending
    ready", not which provider backs it."""
    return bool(os.environ.get("RESEND_API_KEY") and os.environ.get("GHOST_SMTP_EMAIL"))


def send_email(to_email: str, subject: str, body_text: str) -> bool:
    if not smtp_configured():
        print(f"[GHOST/Email] Resend non configuré — email non envoyé à {to_email} ({subject!r}).")
        return False
    api_key = os.environ["RESEND_API_KEY"]
    sender = os.environ["GHOST_SMTP_EMAIL"]
    try:
        res = requests.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"from": sender, "to": [to_email], "subject": subject, "text": body_text},
            timeout=10,
        )
        if res.status_code >= 400:
            print(f"[GHOST/Email] Envoi impossible à {to_email}: {res.status_code} {res.text}")
            return False
        return True
    except Exception as exc:
        print(f"[GHOST/Email] Envoi impossible à {to_email}: {exc}")
        return False
