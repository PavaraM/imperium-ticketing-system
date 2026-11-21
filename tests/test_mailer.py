import pytest

from app.services.mailer import (
    ConsoleMailer,
    MailerError,
    SmtpMailer,
    build_reservation_message,
    build_ticket_message,
    get_mailer,
)
from app.services.qr import render_qr_png

QR_PNG = render_qr_png("test-token-value-for-mailer")


def test_reservation_email_confirms_seat_without_qr(settings):
    message = build_reservation_message(
        settings,
        to="guest@example.com",
        guest_name="Test Guest",
        total_price=150_000,
        amount_paid=75_000,
    )
    body = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()

    assert message["Subject"] == "Seat reserved for Test Party"
    assert "Your seat for Test Party is reserved." in body
    assert "not an entry ticket" in body
    assert "Remaining balance: LKR 750.00" in body
    assert "Pay by: 05 Dec 2026, 19:00 +0530" in body
    assert "All payments are non-refundable." in body
    assert "<strong>All payments are non-refundable.</strong>" in html
    assert len(message.get_payload()) == 2
    assert all(part.get_content_type() != "image/png" for part in message.walk())


def message_for(settings, **overrides):
    settings = settings.model_copy(update={"event_date": "2026-12-31", "event_venue": "Town Hall"})
    return build_ticket_message(
        settings,
        to="guest@example.com",
        guest_name="Test Guest",
        ticket_id=7,
        qr_png=QR_PNG,
    )


def test_message_carries_event_details(settings):
    message = message_for(settings)

    assert message["To"] == "guest@example.com"
    assert "Test Party" in message["Subject"]
    body = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "Test Guest" in body
    assert "2026-12-31" in body
    assert "Town Hall" in body
    assert "All payments are non-refundable." in body
    assert "<strong>All payments are non-refundable.</strong>" in html


def test_half_ticket_email_explains_reservation_and_balance_deadline(settings):
    message = build_ticket_message(
        settings,
        to="guest@example.com",
        guest_name="Test Guest",
        ticket_id=8,
        qr_png=QR_PNG,
        ticket_type="half",
        amount_paid=75_000,
        total_price=150_000,
    )
    body = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()

    assert "reserves your place only and does not grant entry" in body
    assert "LKR 750.00" in body
    assert "by 05 Dec 2026, 19:00 +0530" in body
    assert "All payments are non-refundable." in body
    assert "<strong>All payments are non-refundable.</strong>" in html


def test_html_message_is_responsive_and_escapes_dynamic_content(settings):
    customized = settings.model_copy(
        update={
            "event_name": "Test & Party",
            "event_date": "2026-12-31",
            "event_venue": "Town & Hall",
        }
    )
    message = build_ticket_message(
        customized,
        to="guest@example.com",
        guest_name="<Test Guest>",
        ticket_id=7,
        qr_png=QR_PNG,
    )
    html = message.get_payload()[0].get_payload()[1].get_payload()[0].get_content()

    assert 'lang="en"' in html
    assert "max-width:560px" in html
    assert "Test &amp; Party" in html
    assert "&lt;Test Guest&gt;" in html
    assert "Town &amp; Hall" in html
    assert 'src="cid:ticket-qr-7@imperium"' in html
    assert 'alt="Your entry QR code"' in html
    assert "Your QR ticket is attached to this email." in html


def test_qr_is_inline_and_attached(settings):
    message = message_for(settings)
    parts = message.get_payload()

    alternative = parts[0]
    assert alternative.get_content_type() == "multipart/alternative"

    html_part = alternative.get_payload()[1]
    assert html_part.get_content_type() == "multipart/related"
    inline = html_part.get_payload()[1]
    assert inline.get_content_type() == "image/png"
    assert inline["Content-ID"] == "<ticket-qr-7@imperium>"

    attachment = parts[1]
    assert attachment.get_content_disposition() == "attachment"
    assert attachment.get_filename() == "ticket-qr.png"
    assert attachment.get_payload(decode=True) == QR_PNG


def test_console_mailer_writes_an_eml_file(settings):
    mailer = ConsoleMailer(settings)

    mailer.send_ticket(to="guest@example.com", guest_name="Test Guest", ticket_id=3, qr_png=QR_PNG)

    path = settings.db_path.parent / "outbox" / "ticket-3.eml"
    assert path.exists()
    assert "Test Party" in path.read_text(encoding="utf-8", errors="replace")


def test_get_mailer_defaults_to_console(settings):
    assert isinstance(get_mailer(settings), ConsoleMailer)


def test_smtp_mailer_requires_configuration(settings):
    broken = settings.model_copy(update={"mail_mode": "smtp", "smtp_host": ""})

    with pytest.raises(RuntimeError, match="SMTP_HOST"):
        get_mailer(broken)


def test_smtp_transport_errors_become_mailer_errors(settings, monkeypatch):
    class BoomSMTP:
        def __init__(self, *args, **kwargs):  # noqa: ARG002
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self, **kwargs):  # noqa: ARG002
            raise OSError("connection refused")

    monkeypatch.setattr("app.services.mailer.smtplib.SMTP", BoomSMTP)
    smtp_settings = settings.model_copy(
        update={"smtp_host": "smtp.example.com", "smtp_from_email": "tickets@example.com"}
    )

    with pytest.raises(MailerError):
        SmtpMailer(smtp_settings).send_ticket(
            to="guest@example.com", guest_name="Test Guest", ticket_id=1, qr_png=QR_PNG
        )
