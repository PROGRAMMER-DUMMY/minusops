"""
Executive email dispatcher: HTML body plus .xlsx attachments over SMTP.

One transport, not two. The plan offered "SES SendRawEmail or smtplib"; SES exposes an SMTP
endpoint (email-smtp.<region>.amazonaws.com:587), so smtplib reaches SES and Exchange Online
with the same code and no signing logic to get wrong. Point SMTP_HOST at whichever one you own.

Attachments are read from disk and typed with `mimetypes` — an .xlsx sent as
application/octet-stream lands in Outlook as an unopenable blob, so the spreadsheet MIME type
is set explicitly when the guess comes back empty.

Credentials come from SMTP_USERNAME / SMTP_PASSWORD (or a Secrets Manager ARN), never from a
parameter.

Depends on: core/integrations/base_hook.py
Shells out to: the SMTP host in SMTP_HOST (AWS SES SMTP or Exchange Online), STARTTLS on
    SMTP_PORT; reads the attachment files named by the caller
Used by: tests/test_integrations.py
"""
import os
import sys
import smtplib
import mimetypes
from email.message import EmailMessage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base_hook  # noqa: E402

HOST_ENV = "SMTP_HOST"
PORT_ENV = "SMTP_PORT"
USER_ENV = "SMTP_USERNAME"
PASSWORD_ENV = "SMTP_PASSWORD"
FROM_ENV = "SMTP_FROM"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def build_message(from_address, to_addresses, subject, body_html, attachments=()):
    """
    Build the MIME message: a plain-text alternative, the HTML body, and one part per
    attachment path.

    A missing or unreadable attachment path raises OSError here — deliberately, because this
    is called inside the gated sender and a "sent" executive report with a silently dropped
    workbook is worse than a failed send that says why.
    """
    msg = EmailMessage()
    msg["From"] = from_address
    msg["To"] = ", ".join(to_addresses) if isinstance(to_addresses, (list, tuple)) else to_addresses
    msg["Subject"] = subject
    msg.set_content("This message contains an HTML report. View it in an HTML-capable client.")
    msg.add_alternative(body_html, subtype="html")

    for path in attachments or ():
        with open(path, "rb") as f:
            data = f.read()
        guessed, _enc = mimetypes.guess_type(path)
        if not guessed:
            guessed = _XLSX if path.lower().endswith(".xlsx") else "application/octet-stream"
        maintype, _, subtype = guessed.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype,
                           filename=os.path.basename(path))
    return msg


def send_executive_email(to_addresses, subject, body_html, attachments=(),
                         approval_mode="gatekeeper", action="send-executive-email",
                         details=None, secret_arn=None, timeout=30):
    """
    Send the executive summary email. Returns a result dict; `sent` is False when approval
    was denied or when SMTP_HOST / SMTP_FROM are unconfigured.
    """
    recipients = list(to_addresses) if isinstance(to_addresses, (list, tuple)) else [to_addresses]

    def _send():
        host = (os.environ.get(HOST_ENV) or "").strip()
        sender = (os.environ.get(FROM_ENV) or "").strip()
        if not host or not sender:
            return base_hook.not_configured(f"{HOST_ENV}/{FROM_ENV}")
        port = int(os.environ.get(PORT_ENV) or 587)
        username = (os.environ.get(USER_ENV) or "").strip()
        password = base_hook.resolve_secret(PASSWORD_ENV, secret_arn)
        try:
            msg = build_message(sender, recipients, subject, body_html, attachments)
        except OSError as e:
            return {"ok": False, "status": 400, "error": f"attachment unreadable: {e}"}
        try:
            with smtplib.SMTP(host, port, timeout=timeout) as smtp:
                smtp.starttls()
                if username and password:
                    smtp.login(username, password)
                refused = smtp.send_message(msg)
            return {"ok": True, "status": 250, "recipients": recipients,
                    "refused": list(refused or {})}
        except smtplib.SMTPException as e:
            return {"ok": False, "status": 502, "error": str(e)}
        except (TimeoutError, OSError) as e:
            return {"ok": False, "status": 504, "error": str(e)}
        except Exception as e:
            return {"ok": False, "status": 500, "error": str(e)}

    return base_hook.gated(action, details or f"{subject} -> {', '.join(recipients)}",
                           approval_mode, _send)
