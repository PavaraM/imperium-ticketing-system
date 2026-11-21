"""Half-ticket balances must be recorded before the scanner can admit a guest."""

from datetime import datetime

from app.models import Ticket, TicketType
from tests.conftest import TICKETER_PASSWORD, TICKETER_USERNAME, login
from tests.helpers import fresh, issue_ticket


def _set_event(app, value: str) -> None:
    app.state.settings = app.state.settings.model_copy(
        update={"event_datetime": datetime.fromisoformat(value)}
    )


def test_half_ticket_reserves_spot_but_cannot_enter(admin_client, session, app, mailer):
    _set_event(app, "2099-12-12T19:00:00+05:30")
    ticket = issue_ticket(
        admin_client, session, ticket_type="half", amount="2000", reference="HALF-0001"
    )

    assert ticket.ticket_type is TicketType.half
    assert ticket.total_price == 200_000
    assert ticket.amount_paid == 100_000

    lookup = admin_client.post("/api/scan/lookup", json={"token": ticket.token})
    assert lookup.json()["state"] == "payment_due"
    assert lookup.json()["balance_due"] == 100_000
    checkin = admin_client.post("/api/scan/checkin", json={"token": ticket.token})
    assert checkin.status_code == 409
    assert checkin.json()["state"] == "payment_due"


def test_admin_records_balance_then_scanner_admits(admin_client, session, app, mailer):
    _set_event(app, "2099-12-12T19:00:00+05:30")
    ticket = issue_ticket(
        admin_client, session, ticket_type="half", amount="2000", reference="HALF-0002"
    )

    response = admin_client.post(
        f"/tickets/{ticket.id}/balance",
        data={"bank_reference": "BALANCE-0002"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    updated = fresh(session, Ticket, ticket.id or 0)
    assert updated.balance_payment_reference == "BALANCE-0002"
    assert updated.balance_paid_at is not None
    assert updated.balance_paid_by is not None

    lookup = admin_client.post("/api/scan/lookup", json={"token": ticket.token})
    assert lookup.json()["state"] == "valid"
    assert lookup.json()["balance_due"] == 0
    assert admin_client.post("/api/scan/checkin", json={"token": ticket.token}).status_code == 200


def test_overdue_payment_needs_explicit_admin_confirmation(admin_client, session, app, mailer):
    _set_event(app, "2026-11-01T19:00:00+05:30")
    ticket = issue_ticket(
        admin_client, session, ticket_type="half", amount="2000", reference="HALF-0003"
    )
    assert (
        admin_client.post("/api/scan/lookup", json={"token": ticket.token}).json()["state"]
        == "payment_overdue"
    )

    unconfirmed = admin_client.post(
        f"/tickets/{ticket.id}/balance",
        data={"bank_reference": "BALANCE-0003"},
        follow_redirects=False,
    )
    assert unconfirmed.status_code == 303
    assert fresh(session, Ticket, ticket.id or 0).balance_paid_at is None

    confirmed = admin_client.post(
        f"/tickets/{ticket.id}/balance",
        data={"bank_reference": "BALANCE-0003", "confirm_late": "yes"},
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    assert fresh(session, Ticket, ticket.id or 0).balance_paid_at is not None


def test_balance_payment_is_admin_only(admin_client, anon_client, session, app, mailer):
    _set_event(app, "2099-12-12T19:00:00+05:30")
    ticket = issue_ticket(
        admin_client, session, ticket_type="half", amount="2000", reference="HALF-0004"
    )
    path = f"/tickets/{ticket.id}/balance"
    anonymous = anon_client.post(path, data={"bank_reference": "BALANCE-0004"})
    assert anonymous.status_code == 303
    assert anonymous.headers["location"].startswith("/login")

    admin_client.post("/logout", follow_redirects=False)
    assert login(admin_client, TICKETER_USERNAME, TICKETER_PASSWORD).status_code == 303
    assert (
        admin_client.post(
            path, data={"bank_reference": "BALANCE-0004"}, follow_redirects=False
        ).status_code
        == 403
    )


def test_half_ticket_price_must_split_evenly(admin_client, app):
    response = admin_client.post(
        "/tickets",
        data={
            "name": "Test Guest Odd Price",
            "email": "odd@example.com",
            "whatsapp_number": "0770000005",
            "ticket_type": "half",
            "amount": "1500.01",
            "bank_reference": "HALF-0005",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "whole cents" in response.text
