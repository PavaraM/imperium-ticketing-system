"""Shared helpers for tests that drive the HTTP API."""

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models import Ticket
from app.services.tickets import TicketInput, create_ticket


def fresh(session: Session, model, primary_key: int):
    """Drop any open read transaction so writes made by the API are visible."""
    session.rollback()
    return session.get(model, primary_key)


def issue_ticket(
    client: TestClient,
    session: Session,
    *,
    name: str = "Test Guest One",
    reference: str = "SCAN-0001",
    email: str = "guest1@example.com",
    batch: str = "Batch A",
    amount: str = "1500",
) -> Ticket:
    response = client.post(
        "/tickets",
        data={
            "name": name,
            "email": email,
            "whatsapp_number": "0771234567",
            "batch": batch,
            "amount": amount,
            "bank_reference": reference,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    ticket_id = int(response.headers["location"].rsplit("/", 1)[-1])
    ticket = fresh(session, Ticket, ticket_id)
    assert ticket is not None
    return ticket


def issue_ticket_as(
    session: Session,
    issued_by: int,
    *,
    name: str,
    reference: str,
    email: str,
) -> Ticket:
    """Create a fake ticket directly for an issuer other than the logged-in user."""
    data = TicketInput(
        name=name,
        email=email,
        whatsapp_number="0771234567",
        batch="Test Batch",
        amount=Decimal("1500.00"),
        bank_reference=reference,
    )
    return create_ticket(session, data, issued_by=issued_by)
