"""Outbound email — Gmail SMTP with an app password. Used for password
reset links. Degrades to a clear server-side log (never a silent no-op)
when credentials aren't configured yet, matching the rest of the app's
"safe when unconfigured" pattern for optional integrations."""
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText


def smtp_configured() -> bool:
    return bool(os.environ.get("GHOST_SMTP_EMAIL") and os.environ.get("GHOST_SMTP_APP_PASSWORD"))


def send_email(to_email: str, subject: str, body_text: str) -> bool:
    if not smtp_configured():
        print(f"[GHOST/Email] SMTP non configuré — email non envoyé à {to_email} ({subject!r}).")
        return False
    sender = os.environ["GHOST_SMTP_EMAIL"]
    app_password = os.environ["GHOST_SMTP_APP_PASSWORD"]
    msg = MIMEText(body_text, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(sender, app_password)
            server.sendmail(sender, [to_email], msg.as_string())
        return True
    except Exception as exc:
        print(f"[GHOST/Email] Envoi impossible à {to_email}: {exc}")
        return False
