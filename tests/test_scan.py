"""Scanner behaviour: read-only lookup, manual search, and the atomic check-in."""

import re

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models import Role, Ticket, TicketStatus, User
from app.services.scan import check_in
from app.services.users import create_ticketer
from tests.helpers import fresh, issue_ticket, issue_ticket_as

STATE_KEYS = {
    "state",
    "ticket_id",
    "name",
    "batch",
    "ticket_type",
    "amount_paid",
    "total_price",
    "balance_due",
    "balance_due_display",
    "checked_in_at",
    "checked_in_by",
}


def admin_id(session: Session) -> int:
    session.rollback()
    admin = session.exec(select(User).where(User.role == Role.admin)).one()
    return admin.id or 0


def test_scan_page_requires_login(anon_client: TestClient):
    response = anon_client.get("/scan", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_scan_page_renders_for_admin(admin_client: TestClient):
    response = admin_client.get("/scan")

    assert response.status_code == 200
    assert "Manual search" in response.text
    assert 'id="reader"' in response.text


def test_scan_page_renders_for_ticketer(ticketer_client: TestClient):
    response = ticketer_client.get("/scan")

    assert response.status_code == 200
    assert "Manual search" in response.text


def test_lookup_valid_returns_guest_without_personal_data(
    admin_client: TestClient, session: Session
):
    ticket = issue_ticket(admin_client, session)

    response = admin_client.post("/api/scan/lookup", json={"token": ticket.token})

    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "valid"
    assert data["name"] == "Test Guest One"
    assert data["batch"] == "Batch A"
    assert data["ticket_id"] == ticket.id
    assert set(data) == STATE_KEYS
    assert ticket.token not in response.text
    assert "guest1@example.com" not in response.text
    assert "0771234567" not in response.text


def test_lookup_accepts_a_url_wrapped_token(admin_client: TestClient, session: Session):
    ticket = issue_ticket(admin_client, session)

    response = admin_client.post(
        "/api/scan/lookup", json={"token": f"https://tickets.example.com/t/{ticket.token}"}
    )

    assert response.status_code == 200
    assert response.json()["state"] == "valid"


def test_lookup_reports_checked_in_time_and_user(admin_client: TestClient, session: Session):
    ticket = issue_ticket(admin_client, session)
    admitted = admin_client.post("/api/scan/checkin", json={"token": ticket.token})
    assert admitted.status_code == 200

    response = admin_client.post("/api/scan/lookup", json={"token": ticket.token})

    data = response.json()
    assert data["state"] == "checked_in"
    assert data["checked_in_by"] == "admin"
    assert re.fullmatch(r"\d{2}:\d{2}", data["checked_in_at"])


def test_lookup_revoked_ticket(admin_client: TestClient, session: Session):
    ticket = issue_ticket(admin_client, session)
    ticket.status = TicketStatus.revoked
    session.add(ticket)
    session.commit()

    response = admin_client.post("/api/scan/lookup", json={"token": ticket.token})

    assert response.status_code == 200
    assert response.json()["state"] == "revoked"
    assert response.json()["name"] == "Test Guest One"


def test_lookup_unknown_token(admin_client: TestClient):
    response = admin_client.post("/api/scan/lookup", json={"token": "x" * 43})

    assert response.status_code == 200
    assert response.json() == {"state": "unknown"}


def test_lookup_requires_login(anon_client: TestClient):
    response = anon_client.post("/api/scan/lookup", json={"token": "x" * 43})

    assert response.status_code == 401
    assert response.json()["detail"] == "Login required"


def test_ticketer_can_lookup(ticketer_client: TestClient, session: Session):
    ticket = issue_ticket(ticketer_client, session, reference="SCAN-0002")

    response = ticketer_client.post("/api/scan/lookup", json={"token": ticket.token})

    assert response.status_code == 200
    assert response.json()["state"] == "valid"


def test_checkin_admits_the_ticket(admin_client: TestClient, session: Session):
    ticket = issue_ticket(admin_client, session)

    response = admin_client.post("/api/scan/checkin", json={"token": ticket.token})

    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "checked_in"
    assert data["just_checked_in"] is True

    stored = fresh(session, Ticket, ticket.id or 0)
    assert stored.status == TicketStatus.checked_in
    assert stored.checked_in_by == admin_id(session)
    assert stored.checked_in_at is not None


def test_second_checkin_is_refused(admin_client: TestClient, session: Session):
    ticket = issue_ticket(admin_client, session)
    first = admin_client.post("/api/scan/checkin", json={"token": ticket.token})
    assert first.status_code == 200
    admitted_at = fresh(session, Ticket, ticket.id or 0).checked_in_at

    second = admin_client.post("/api/scan/checkin", json={"token": ticket.token})

    assert second.status_code == 409
    assert second.json()["state"] == "checked_in"
    assert second.json()["checked_in_by"] == "admin"
    assert "just_checked_in" not in second.json()
    assert fresh(session, Ticket, ticket.id or 0).checked_in_at == admitted_at


def test_stale_session_cannot_double_admit(admin_client: TestClient, session: Session):
    """A phone holding a stale read must still lose the race."""
    ticket = issue_ticket(admin_client, session)
    ticket_id = ticket.id or 0
    token = ticket.token

    with Session(admin_client.app.state.engine) as stale:
        stale.get(Ticket, ticket_id)  # loads status=valid into the identity map
        winner = fresh(session, User, admin_id(session))
        with Session(admin_client.app.state.engine) as other:
            _, won = check_in(
                other, other.get(User, winner.id), admin_client.app.state.settings, token=token
            )
        assert won is True

        snapshot, admitted = check_in(
            stale, stale.get(User, winner.id), admin_client.app.state.settings, token=token
        )

    assert admitted is False
    assert snapshot is not None
    assert snapshot.status == TicketStatus.checked_in


def test_checkin_rejects_revoked_ticket(admin_client: TestClient, session: Session):
    ticket = issue_ticket(admin_client, session)
    ticket.status = TicketStatus.revoked
    session.add(ticket)
    session.commit()

    response = admin_client.post("/api/scan/checkin", json={"token": ticket.token})

    assert response.status_code == 409
    assert response.json()["state"] == "revoked"

    stored = fresh(session, Ticket, ticket.id or 0)
    assert stored.status == TicketStatus.revoked
    assert stored.checked_in_at is None
    assert stored.checked_in_by is None


def test_checkin_unknown_token(admin_client: TestClient):
    response = admin_client.post("/api/scan/checkin", json={"token": "y" * 43})

    assert response.status_code == 404
    assert response.json() == {"state": "unknown"}


def test_checkin_by_ticket_id_for_manual_search(admin_client: TestClient, session: Session):
    ticket = issue_ticket(admin_client, session)

    response = admin_client.post("/api/scan/checkin", json={"ticket_id": ticket.id})

    assert response.status_code == 200
    assert response.json()["just_checked_in"] is True
    assert fresh(session, Ticket, ticket.id or 0).status == TicketStatus.checked_in


def test_checkin_requires_exactly_one_identifier(admin_client: TestClient):
    neither = admin_client.post("/api/scan/checkin", json={})
    both = admin_client.post("/api/scan/checkin", json={"token": "z" * 43, "ticket_id": 1})

    assert neither.status_code == 422
    assert both.status_code == 422


def test_checkin_records_the_scanning_ticketer(ticketer_client: TestClient, session: Session):
    ticket = issue_ticket(ticketer_client, session, reference="SCAN-0003")

    response = ticketer_client.post("/api/scan/checkin", json={"token": ticket.token})

    assert response.status_code == 200
    assert response.json()["checked_in_by"] == "ticketer1"


def test_checkin_requires_login(anon_client: TestClient):
    response = anon_client.post("/api/scan/checkin", json={"token": "z" * 43})

    assert response.status_code == 401


def test_search_finds_guests_by_partial_name(admin_client: TestClient, session: Session):
    issue_ticket(admin_client, session, name="Amara Perera", reference="SCAN-0004")
    issue_ticket(
        admin_client,
        session,
        name="Nimal Fernando",
        reference="SCAN-0005",
        email="guest2@example.com",
    )

    response = admin_client.get("/api/scan/search", params={"q": "amara"})

    assert response.status_code == 200
    items = response.json()
    assert [item["name"] for item in items] == ["Amara Perera"]
    assert items[0]["state"] == "valid"
    assert set(items[0]) == STATE_KEYS


def test_search_never_returns_tokens(admin_client: TestClient, session: Session):
    ticket = issue_ticket(admin_client, session, name="Amara Perera")

    response = admin_client.get("/api/scan/search", params={"q": "Amara"})

    assert ticket.token not in response.text
    assert "token" not in response.json()[0]


def test_ticketer_scanner_search_includes_other_ticketers_guests(
    ticketer_client: TestClient, session: Session
):
    other_user = create_ticketer(session, "ticketer2", "ticketer2-pass-123")
    assert other_user.id is not None
    ticket = issue_ticket_as(
        session,
        other_user.id,
        name="Other Ticketer Guest",
        email="other-guest@example.com",
        reference="SCAN-TICKETER-001",
    )

    response = ticketer_client.get("/api/scan/search", params={"q": "Other Ticketer Guest"})

    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == ["Other Ticketer Guest"]
    assert ticket.token not in response.text
    assert "token" not in response.json()[0]


def test_search_with_empty_query_returns_nothing(admin_client: TestClient, session: Session):
    issue_ticket(admin_client, session, name="Amara Perera")

    response = admin_client.get("/api/scan/search", params={"q": "   "})

    assert response.status_code == 200
    assert response.json() == []


def test_search_requires_login(anon_client: TestClient):
    response = anon_client.get("/api/scan/search", params={"q": "Amara"})

    assert response.status_code == 401
