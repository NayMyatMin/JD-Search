"""Gmail SMTP email sender.

Minimal: plaintext + HTML alternative, one recipient, no attachments.
Credentials come from the .env Secrets loader.

To test: `uv run jd email-test`.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from markdown_it import MarkdownIt

from .config import EmailConfig, Secrets

log = logging.getLogger(__name__)

_md = MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": True})


def render_html(markdown_body: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 720px; margin: 24px auto; padding: 0 16px; color: #222; }}
h1 {{ font-size: 22px; border-bottom: 1px solid #eee; padding-bottom: 6px; }}
h2 {{ font-size: 17px; margin-top: 28px; color: #444; }}
h3 {{ font-size: 15px; margin-top: 18px; }}
a {{ color: #0b5fff; text-decoration: none; }}
ul, li {{ margin: 4px 0; }}
code {{ background: #f4f4f4; padding: 1px 4px; border-radius: 3px; }}
</style></head><body>
{_md.render(markdown_body)}
</body></html>
"""


def send_email(
    subject: str,
    markdown_body: str,
    cfg: EmailConfig,
    secrets: Secrets,
) -> bool:
    if not cfg.enabled:
        log.info("email disabled in config; skipping send")
        return False
    if not (secrets.gmail_user and secrets.gmail_app_password and secrets.email_to):
        log.error("missing GMAIL_USER / GMAIL_APP_PASSWORD / EMAIL_TO in .env")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = secrets.gmail_user
    msg["To"] = secrets.email_to
    msg.set_content(markdown_body)
    msg.add_alternative(render_html(markdown_body), subtype="html")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(secrets.gmail_user, secrets.gmail_app_password.replace(" ", ""))
            smtp.send_message(msg)
        log.info("email sent to %s", secrets.email_to)
        return True
    except Exception as exc:
        log.error("email send failed: %s", exc)
        return False
