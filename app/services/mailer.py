"""Pluggable ticket mailer: SMTP for real use, console/files for development."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
from typing import Protocol

from app.config import Settings
from app.services.tickets import event_payment_deadline

logger = logging.getLogger("app.mail")


class MailerError(RuntimeError):
    """Raised when a mailer cannot hand the message to its transport."""


class Mailer(Protocol):
    def send_reservation(
        self,
        *,
        to: str,
        guest_name: str,
        ticket_id: int,
        total_price: int,
        amount_paid: int,
    ) -> None: ...

    def send_ticket(
        self,
        *,
        to: str,
        guest_name: str,
        ticket_id: int,
        qr_png: bytes,
        ticket_type: str = "full",
        amount_paid: int = 0,
        total_price: int = 0,
        balance_paid: bool = False,
    ) -> None: ...


def _qr_cid(ticket_id: int) -> str:
    return f"ticket-qr-{ticket_id}@imperium"


def build_reservation_message(
    settings: Settings,
    *,
    to: str,
    guest_name: str,
    total_price: int,
    amount_paid: int,
) -> EmailMessage:
    """Build a QR-free notice confirming a half-ticket reservation."""
    safe_name = escape(guest_name)
    safe_event = escape(settings.event_name)
    safe_date = escape(settings.event_date)
    safe_venue = escape(settings.event_venue)
    safe_currency = escape(settings.currency)
    balance = (total_price - amount_paid) / 100
    deadline = event_payment_deadline(settings).strftime("%d %b %Y, %H:%M %Z")
    subject = f"Seat reserved for {settings.event_name}"
    payment_details = (
        f"Deposit received: {settings.currency} {amount_paid / 100:,.2f}\n"
        f"Remaining balance: {settings.currency} {balance:,.2f}\n"
        f"Pay by: {deadline}\n"
    )
    text = (
        f"Hi {guest_name},\n\n"
        f"Your seat for {settings.event_name} is reserved.\n"
        "This confirmation is not an entry ticket. We will email your ticket and entry QR "
        "after staff record your full payment.\n\n"
        f"When: {settings.event_date}\n"
        f"Where: {settings.event_venue}\n\n"
        f"{payment_details}\n"
        "All payments are non-refundable.\n"
    )
    html = f"""\
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:24px 12px;background:#f3f7f5;color:#192522;
font-family:Arial,Helvetica,sans-serif;line-height:1.6">
  <main style="max-width:560px;margin:auto;padding:28px;background:#fff;
border:1px solid #dce6e1;border-radius:16px">
    <h1 style="margin-top:0">Seat reserved</h1>
    <p>Hi {safe_name}, your seat for <strong>{safe_event}</strong> is reserved.</p>
    <p><strong>This confirmation is not an entry ticket.</strong> We will email your ticket
    and entry QR after staff record your full payment.</p>
    <p><strong>When:</strong> {safe_date}<br><strong>Where:</strong> {safe_venue}</p>
    <p><strong>Deposit received:</strong> {safe_currency} {amount_paid / 100:,.2f}<br>
    <strong>Remaining balance:</strong> {safe_currency} {balance:,.2f}<br>
    <strong>Pay by:</strong> {escape(deadline)}</p>
    <p><strong>All payments are non-refundable.</strong></p>
  </main>
</body></html>
"""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.smtp_from_name, settings.smtp_from_email))
    message["To"] = to
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message


def build_ticket_message(
    settings: Settings,
    *,
    to: str,
    guest_name: str,
    ticket_id: int,
    qr_png: bytes,
    ticket_type: str = "full",
    amount_paid: int = 0,
    total_price: int = 0,
    balance_paid: bool = False,
) -> EmailMessage:
    """Build the guest email with the QR both inline and attached."""
    subject = f"Your ticket for {settings.event_name}"
    safe_name = escape(guest_name)
    safe_event = escape(settings.event_name)
    safe_date = escape(settings.event_date)
    safe_venue = escape(settings.event_venue)
    cid = _qr_cid(ticket_id)
    if ticket_type == "half" and balance_paid:
        payment_text = (
            "Your half ticket is fully paid. Show the QR at the door. "
            "It admits one guest and can only be used once."
        )
        payment_html = payment_text
    elif ticket_type == "half":
        deadline = event_payment_deadline(settings).strftime("%d %b %Y, %H:%M %Z")
        balance = (total_price - amount_paid) / 100
        payment_text = (
            "This half ticket reserves your place only and does not grant entry. "
            f"Pay the remaining {settings.currency} {balance:,.2f} by {deadline}. "
            "Entry is allowed after staff record the balance payment."
        )
        payment_html = (
            "This half ticket reserves your place only and does not grant entry. "
            f"Pay the remaining {escape(settings.currency)} {balance:,.2f} by "
            f"{escape(deadline)}. Entry is allowed after staff record the balance payment."
        )
    else:
        payment_text = (
            "Your full ticket is paid. Show the QR at the door. "
            "It admits one guest and can only be used once."
        )
        payment_html = payment_text

    text = (
        f"Hi {guest_name},\n\n"
        f"You are on the list for {settings.event_name}.\n\n"
        f"When: {settings.event_date}\n"
        f"Where: {settings.event_venue}\n\n"
        "Your QR ticket is attached to this email.\n"
        f"{payment_text}\n"
        "All payments are non-refundable.\n"
    )

    html = f"""\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
  </head>
  <body bgcolor="#f3f7f5" style="margin:0;padding:24px 12px;background:#f3f7f5;
                                  color:#192522;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           bgcolor="#f3f7f5">
      <tr>
        <td align="center" style="padding:8px 0;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                 style="width:100%;max-width:560px;background:#ffffff;border:1px solid #dce6e1;
                        border-radius:16px;overflow:hidden;">
            <tr>
              <td bgcolor="#172923" style="padding:28px 28px 26px;background:#172923;
                                            border-bottom:3px solid #d8a34c;">
                <p style="margin:0;color:#e8c982;font-size:12px;font-weight:bold;
                          letter-spacing:1.5px;line-height:1.4;text-transform:uppercase;">
                  Event ticket
                </p>
                <h1 style="margin:8px 0 0;color:#ffffff;font-size:26px;font-weight:700;
                           line-height:1.2;">{safe_event}</h1>
              </td>
            </tr>
            <tr>
              <td style="padding:26px 28px 8px;font-size:16px;line-height:1.6;">
                <p style="margin:0 0 12px;">Hi {safe_name},</p>
                <p style="margin:0;">You are on the list for {safe_event}.</p>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 28px 8px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                       border="0" bgcolor="#f3f7f5"
                       style="background:#f3f7f5;border:1px solid #dce6e1;border-radius:12px;">
                  <tr>
                    <td style="padding:14px 16px 6px;color:#52635e;font-size:12px;
                               font-weight:bold;letter-spacing:1px;text-transform:uppercase;">
                      When
                    </td>
                  </tr>
                  <tr>
                    <td style="padding:0 16px 12px;color:#192522;font-size:15px;line-height:1.5;">
                      {safe_date}
                    </td>
                  </tr>
                  <tr>
                    <td style="padding:0 16px 6px;color:#52635e;font-size:12px;
                               font-weight:bold;letter-spacing:1px;text-transform:uppercase;">
                      Where
                    </td>
                  </tr>
                  <tr>
                    <td style="padding:0 16px 14px;color:#192522;font-size:15px;line-height:1.5;">
                      {safe_venue}
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td align="center" style="padding:20px 20px 8px;">
                <p style="margin:0 0 12px;color:#52635e;font-size:12px;font-weight:bold;
                          letter-spacing:1px;text-transform:uppercase;">
                  Your entry QR
                </p>
                <img src="cid:{cid}" width="240" height="240" alt="Your entry QR code"
                     style="display:block;width:240px;max-width:100%;height:auto;
                            border:1px solid #dce6e1;border-radius:12px;background:#ffffff;">
              </td>
            </tr>
            <tr>
              <td style="padding:18px 28px 28px;color:#52635e;font-size:14px;line-height:1.6;">
                <p style="margin:0;">Your QR ticket is attached to this email. {payment_html}</p>
                <p style="margin:12px 0 0;"><strong>All payments are non-refundable.</strong></p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.smtp_from_name, settings.smtp_from_email))
    message["To"] = to
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    html_part = message.get_payload()[1]
    html_part.add_related(
        qr_png,
        maintype="image",
        subtype="png",
        cid=f"<{cid}>",
        filename="ticket-qr.png",
    )
    message.add_attachment(qr_png, maintype="image", subtype="png", filename="ticket-qr.png")
    return message


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send_reservation(
        self,
        *,
        to: str,
        guest_name: str,
        ticket_id: int,
        total_price: int,
        amount_paid: int,
    ) -> None:
        message = build_reservation_message(
            self._settings,
            to=to,
            guest_name=guest_name,
            total_price=total_price,
            amount_paid=amount_paid,
        )
        self._send(message)

    def send_ticket(
        self,
        *,
        to: str,
        guest_name: str,
        ticket_id: int,
        qr_png: bytes,
        ticket_type: str = "full",
        amount_paid: int = 0,
        total_price: int = 0,
        balance_paid: bool = False,
    ) -> None:
        message = build_ticket_message(
            self._settings,
            to=to,
            guest_name=guest_name,
            ticket_id=ticket_id,
            qr_png=qr_png,
            ticket_type=ticket_type,
            amount_paid=amount_paid,
            total_price=total_price,
            balance_paid=balance_paid,
        )
        self._send(message)

    def _send(self, message: EmailMessage) -> None:
        try:
            with smtplib.SMTP(
                self._settings.smtp_host, self._settings.smtp_port, timeout=20
            ) as smtp:
                if self._settings.smtp_use_starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if self._settings.smtp_user:
                    smtp.login(self._settings.smtp_user, self._settings.smtp_password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailerError("SMTP delivery failed") from exc


class ConsoleMailer:
    """Development mailer: writes the email to ``<db dir>/outbox`` instead of sending."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._outbox = settings.db_path.parent / "outbox"

    def send_reservation(
        self,
        *,
        to: str,
        guest_name: str,
        ticket_id: int,
        total_price: int,
        amount_paid: int,
    ) -> None:
        message = build_reservation_message(
            self._settings,
            to=to,
            guest_name=guest_name,
            total_price=total_price,
            amount_paid=amount_paid,
        )
        self._write(message, f"reservation-{ticket_id}")

    def send_ticket(
        self,
        *,
        to: str,
        guest_name: str,
        ticket_id: int,
        qr_png: bytes,
        ticket_type: str = "full",
        amount_paid: int = 0,
        total_price: int = 0,
        balance_paid: bool = False,
    ) -> None:
        message = build_ticket_message(
            self._settings,
            to=to,
            guest_name=guest_name,
            ticket_id=ticket_id,
            qr_png=qr_png,
            ticket_type=ticket_type,
            amount_paid=amount_paid,
            total_price=total_price,
            balance_paid=balance_paid,
        )
        self._write(message, f"ticket-{ticket_id}")

    def _write(self, message: EmailMessage, filename: str) -> None:
        self._outbox.mkdir(parents=True, exist_ok=True)
        path = self._outbox / f"{filename}.eml"
        path.write_bytes(message.as_bytes())
        logger.info("ticket email written to %s", path)


def get_mailer(settings: Settings) -> Mailer:
    if settings.mail_mode == "smtp":
        if not settings.smtp_host or not settings.smtp_from_email:
            raise RuntimeError("MAIL_MODE=smtp requires SMTP_HOST and SMTP_FROM_EMAIL.")
        return SmtpMailer(settings)
    return ConsoleMailer(settings)
