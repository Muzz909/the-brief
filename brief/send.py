"""Email delivery through Gmail SMTP (app password)."""
from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

from . import config


class GmailSender:
    def __init__(self, address: str | None = None, app_password: str | None = None):
        self.address = address or config.GMAIL_ADDRESS
        self.password = (app_password or config.GMAIL_APP_PASSWORD or "").replace(" ", "")
        if not self.address or not self.password:
            raise RuntimeError("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set")

    def send(self, to: str, subject: str, html: str, text: str) -> str:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = formataddr(("The Brief", self.address))
        msg["To"] = to
        msg["Message-ID"] = make_msgid(domain="thebrief.local")
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
            s.login(self.address, self.password)
            s.sendmail(self.address, [to], msg.as_string())
        return msg["Message-ID"]


class MemorySender:
    """Test double: keeps sent messages in memory."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, to: str, subject: str, html: str, text: str) -> str:
        self.sent.append({"to": to, "subject": subject, "html": html, "text": text})
        return f"<test-{len(self.sent)}>"
