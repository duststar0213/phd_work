"""Send the temporary login code to a participant's inbox.

Configure one of:
  - SMTP (Gmail / Outlook / university mail) via SMTP_HOST + SMTP_USER + SMTP_PASSWORD
  - Resend via RESEND_API_KEY + RESEND_FROM
"""

import json
import logging
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

log = logging.getLogger("repertoire")

STUDY_NAME = "Repertoire"


class MailNotConfigured(Exception):
    """No SMTP or Resend settings in the environment."""


class MailSendFailed(Exception):
    """Provider rejected or could not deliver the message."""


def mail_ready() -> bool:
    if os.getenv("RESEND_API_KEY", "").strip() and os.getenv("RESEND_FROM", "").strip():
        return True
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    return bool(host and user and password)


def _message_body(code: str) -> str:
    return (
        f"Your temporary password for the {STUDY_NAME} study is:\n\n"
        f"    {code}\n\n"
        "It works for 10 minutes. If it expires, go back to the study page and request a new one.\n"
        "If you did not ask to join this study, you can ignore this email.\n"
    )


def _build_message(to_email: str, code: str) -> EmailMessage:
    from_addr = (
        os.getenv("SMTP_FROM", "").strip()
        or os.getenv("RESEND_FROM", "").strip()
        or os.getenv("SMTP_USER", "").strip()
    )
    message = EmailMessage()
    message["Subject"] = f"Your {STUDY_NAME} study password"
    message["From"] = from_addr
    message["To"] = to_email
    message.set_content(_message_body(code))
    return message


def _send_smtp(to_email: str, code: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587") or "587")
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    message = _build_message(to_email, code)
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as smtp:
            smtp.login(user, password)
            smtp.send_message(message)
        return
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(user, password)
        smtp.send_message(message)


def _send_resend(to_email: str, code: str) -> None:
    key = os.getenv("RESEND_API_KEY", "").strip()
    from_addr = os.getenv("RESEND_FROM", "").strip()
    payload = json.dumps(
        {
            "from": from_addr,
            "to": [to_email],
            "subject": f"Your {STUDY_NAME} study password",
            "text": _message_body(code),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if not (200 <= int(response.status) < 300):
                raise MailSendFailed()
    except urllib.error.HTTPError as exc:
        log.warning("resend_http_%s", exc.code)
        raise MailSendFailed() from exc
    except urllib.error.URLError as exc:
        log.warning("resend_unreachable")
        raise MailSendFailed() from exc


def send_login_code(to_email: str, code: str) -> None:
    """Email the 6-digit code. Never log the code."""
    if not mail_ready():
        raise MailNotConfigured()
    try:
        if os.getenv("RESEND_API_KEY", "").strip():
            _send_resend(to_email, code)
        else:
            _send_smtp(to_email, code)
    except MailSendFailed:
        raise
    except Exception as exc:
        log.warning("mail_send_failed: %s", type(exc).__name__)
        raise MailSendFailed() from exc
