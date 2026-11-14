"""Admin dashboard, ticket revocation and CSV export."""

from fastapi.testclient import TestClient

from app.models import Ticket, TicketStatus
from app.services.users import create_ticketer
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, login
from tests.helpers import fresh, issue_ticket, issue_ticket_as


def _check_in(client: TestClient, ticket_id: int):
    return client.post("/api/scan/checkin", json={"ticket_id": ticket_id})


def _revoke(client: TestClient, ticket_id: int):
    return client.post(f"/tickets/{ticket_id}/revoke", follow_redirects=False)


def test_dashboard_totals_exclude_revoked(admin_client, session):
    admitted = issue_ticket(admin_client, session, name="Dashboard Guest", reference="DASH-0001")
    revoked = issue_ticket(
        admin_client,
        session,
        name="Revoked Guest",
        reference="DASH-0002",
        email="guest2@example.com",
    )
    assert _check_in(admin_client, admitted.id).status_code == 200
    assert _revoke(admin_client, revoked.id).status_code == 303

    page = admin_client.get("/")

    assert page.status_code == 200
    assert "Event dashboard" in page.text
    assert "Tickets sold" in page.text
    assert "1,500.00" in page.text
    assert "Latest admissions" in page.text
    assert "Dashboard Guest" in page.text
    assert "Revoked Guest" not in page.text


def test_dashboard_lists_sales_per_staff(admin_client, session):
    issue_ticket(admin_client, session, name="Sales Guest", reference="DASH-0003")

    page = admin_client.get("/")

    assert "Sales by staff" in page.text
    assert ADMIN_USERNAME in page.text
    assert "Collected" in page.text


def test_admin_navigation_marks_current_page(admin_client):
    admin_page = admin_client.get("/")

    assert admin_page.text.count('class="nav-icon"') == 4
    assert admin_page.text.count('aria-current="page"') == 1
    assert "Dashboard" in admin_page.text
    assert "Staff" in admin_page.text


def test_ticketer_navigation_is_limited_and_marks_current_page(ticketer_client):
    ticketer_page = ticketer_client.get("/tickets")

    assert ticketer_page.text.count('class="nav-icon"') == 3
    assert ticketer_page.text.count('aria-current="page"') == 1
    assert "Dashboard" in ticketer_page.text
    assert "Staff" not in ticketer_page.text


def test_ticketer_dashboard_uses_only_own_ticket_stats(ticketer_client, session):
    own_ticket = issue_ticket(
        ticketer_client,
        session,
        name="Ticketer Dashboard Guest",
        reference="DASH-TICKETER-001",
    )
    other_user = create_ticketer(session, "ticketer2", "ticketer2-pass-123")
    assert other_user.id is not None
    other_ticket = issue_ticket_as(
        session,
        other_user.id,
        name="Other Dashboard Guest",
        email="other-dashboard@example.com",
        reference="DASH-TICKETER-002",
    )
    assert _check_in(ticketer_client, own_ticket.id).status_code == 200
    assert _check_in(ticketer_client, other_ticket.id).status_code == 200

    ticketer_dashboard = ticketer_client.get("/")
    assert ticketer_dashboard.status_code == 200
    assert "Tickets sold" in ticketer_dashboard.text
    assert "1,500.00" in ticketer_dashboard.text
    assert "Ticketer Dashboard Guest" in ticketer_dashboard.text
    assert "Other Dashboard Guest" not in ticketer_dashboard.text
    assert "Ticketers" not in ticketer_dashboard.text
    assert "ticketer2" not in ticketer_dashboard.text

    ticketer_client.post("/logout", follow_redirects=False)
    assert login(ticketer_client, ADMIN_USERNAME, ADMIN_PASSWORD).status_code == 303
    admin_dashboard = ticketer_client.get("/")
    assert admin_dashboard.status_code == 200
    assert "3,000.00" in admin_dashboard.text
    assert "Ticketer Dashboard Guest" in admin_dashboard.text
    assert "Other Dashboard Guest" in admin_dashboard.text


def test_admin_revokes_ticket_and_scanner_refuses_it(admin_client, session):
    ticket = issue_ticket(admin_client, session, name="Revoke Me", reference="REV-0001")

    response = _revoke(admin_client, ticket.id)

    assert response.status_code == 303
    assert fresh(session, Ticket, ticket.id).status == TicketStatus.revoked
    assert (
        admin_client.post("/api/scan/lookup", json={"token": ticket.token}).json()["state"]
        == "revoked"
    )
    assert admin_client.post("/api/scan/checkin", json={"ticket_id": ticket.id}).status_code == 409


def test_ticketer_cannot_revoke(admin_client, ticketer_client, session):
    ticket = issue_ticket(ticketer_client, session, name="Keep Me", reference="REV-0002")

    response = _revoke(ticketer_client, ticket.id)

    assert response.status_code == 403
    assert fresh(session, Ticket, ticket.id).status == TicketStatus.valid


def test_cannot_revoke_an_admitted_ticket(admin_client, session):
    ticket = issue_ticket(admin_client, session, name="Admitted Guest", reference="REV-0003")
    assert _check_in(admin_client, ticket.id).status_code == 200

    response = _revoke(admin_client, ticket.id)

    assert response.status_code == 303
    refreshed = fresh(session, Ticket, ticket.id)
    assert refreshed.status == TicketStatus.checked_in
    assert refreshed.checked_in_at is not None
    follow = admin_client.get(f"/tickets/{ticket.id}")
    assert "already been admitted" in follow.text


def test_revoking_twice_is_a_no_op(admin_client, session):
    ticket = issue_ticket(admin_client, session, name="Twice Guest", reference="REV-0004")
    _revoke(admin_client, ticket.id)

    response = _revoke(admin_client, ticket.id)

    assert response.status_code == 303
    assert fresh(session, Ticket, ticket.id).status == TicketStatus.revoked
    assert "already revoked" in admin_client.get(f"/tickets/{ticket.id}").text


def test_revoke_unknown_ticket_is_404(admin_client):
    assert _revoke(admin_client, 9999).status_code == 404


def test_csv_export_contains_tickets_but_no_tokens(admin_client, session):
    kept = issue_ticket(admin_client, session, name="Csv Guest", reference="CSV-0001")
    revoked = issue_ticket(
        admin_client,
        session,
        name="Csv Revoked",
        reference="CSV-0002",
        email="guest3@example.com",
    )
    _revoke(admin_client, revoked.id)

    response = admin_client.get("/tickets/export.csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    body = response.text
    assert body.startswith("\ufeff")
    assert "Bank reference" in body
    assert "Csv Guest" in body
    assert "CSV-0001" in body
    assert "Csv Revoked" in body
    assert "revoked" in body
    assert kept.token not in body
    assert "Email" in body


def test_csv_export_is_admin_only(admin_client, ticketer_client, anon_client):
    assert ticketer_client.get("/tickets/export.csv").status_code == 403

    response = anon_client.get("/tickets/export.csv", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_failed_email_alert_links_to_filtered_list(admin_client, session, mailer):
    mailer.fail = True
    issue_ticket(admin_client, session, name="No Mail Guest", reference="MAIL-0001")
    mailer.fail = False
    issue_ticket(
        admin_client,
        session,
        name="Mailed Guest",
        reference="MAIL-0002",
        email="guest4@example.com",
    )

    dashboard = admin_client.get("/")
    filtered = admin_client.get("/tickets?email=failed")

    assert "could not be emailed" in dashboard.text
    assert "/tickets?email=failed" in dashboard.text
    assert "No Mail Guest" in filtered.text
    assert "Mailed Guest" not in filtered.text
    assert "with email failed" in filtered.text
