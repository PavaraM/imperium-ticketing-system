"""Shared helpers for tests that drive the HTTP API."""

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models import Ticket


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
