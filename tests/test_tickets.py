import csv
import io

from sqlmodel import Session, select

from app.models import EmailStatus, Ticket, TicketStatus
from app.services.users import create_ticketer
from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME, login
from tests.helpers import issue_ticket_as

VALID_TICKET = {
    "name": "Test Guest One",
    "email": "guest1@example.com",
    "whatsapp_number": "0770000001",
    "batch": "2026 A/L Commerce",
    "amount": "1500",
    "bank_reference": "TEST-REF-001",
}


def create(client, **overrides):
    data = {**VALID_TICKET, **overrides}
    return client.post("/tickets", data=data, follow_redirects=False)


def stored(app):
    with Session(app.state.engine) as session:
        return list(session.exec(select(Ticket).order_by(Ticket.id)).all())


def test_create_ticket_saves_valid_ticket(admin_client, app, mailer):
    response = create(admin_client)

    assert response.status_code == 303
    tickets = stored(app)
    assert len(tickets) == 1
    ticket = tickets[0]
    assert response.headers["location"] == f"/tickets/{ticket.id}"
    assert ticket.status == TicketStatus.valid
    assert ticket.email_status == EmailStatus.sent
    assert len(ticket.token) >= 32
    assert ticket.amount_paid == 150_000
    assert ticket.batch == "2026 A/L Commerce"
    assert ticket.bank_reference == "TEST-REF-001"
    assert ticket.issued_by is not None

    assert len(mailer.sent) == 1
    assert mailer.sent[0].to == "guest1@example.com"
    assert mailer.sent[0].qr_png.startswith(b"\x89PNG")


def test_tokens_are_unique_and_qr_is_served(admin_client, app):
    create(admin_client)
    create(admin_client, bank_reference="TEST-REF-002", email="guest2@example.com")

    first, second = stored(app)
    assert first.token != second.token

    response = admin_client.get(f"/tickets/{first.id}/qr.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_duplicate_bank_reference_is_rejected(admin_client, app):
    assert create(admin_client).status_code == 303

    response = create(admin_client, email="other@example.com", name="Test Guest Two")

    assert response.status_code == 400
    assert "already attached" in response.text
    assert len(stored(app)) == 1


def test_bank_reference_duplicate_ignores_case(admin_client, app):
    create(admin_client)

    response = create(admin_client, bank_reference="test-ref-001")

    assert response.status_code == 400
    assert len(stored(app)) == 1


def test_invalid_email_is_rejected(admin_client, app):
    response = create(admin_client, email="not-an-email")

    assert response.status_code == 400
    assert "Email:" in response.text
    assert stored(app) == []


def test_invalid_phone_is_rejected(admin_client, app):
    response = create(admin_client, whatsapp_number="abc")

    assert response.status_code == 400
    assert "WhatsApp number:" in response.text
    assert stored(app) == []


def test_amount_accepts_commas_and_decimals(admin_client, app):
    response = create(admin_client, amount="1,500.50")

    assert response.status_code == 303
    assert stored(app)[0].amount_paid == 150_050


def test_amount_rejects_nonzero_precision_beyond_cents(admin_client, app):
    response = create(admin_client, amount="1.999")

    assert response.status_code == 400
    assert "Ticket price:" in response.text
    assert "no more than two decimal places" in response.text
    assert stored(app) == []


def test_amount_allows_extra_trailing_zeroes(admin_client, app):
    response = create(admin_client, amount="1.990")

    assert response.status_code == 303
    assert stored(app)[0].amount_paid == 199


def test_csv_export_neutralizes_formula_cells(admin_client, app):
    create(admin_client)
    ticket = stored(app)[0]
    with Session(app.state.engine) as session:
        ticket = session.get(Ticket, ticket.id)
        ticket.name = "\t=1+1"
        ticket.email = "\r+guest@example.com"
        ticket.whatsapp_number = "\n-CMD"
        ticket.batch = " \t@SUM(1,1)"
        ticket.bank_reference = "\v=FORMULA"
        session.add(ticket)
        session.commit()

    response = admin_client.get("/tickets/export.csv")
    rows = list(csv.reader(io.StringIO(response.text.removeprefix("\ufeff"))))
    exported = dict(zip(rows[0], rows[1], strict=True))

    assert exported["Name"] == "'\t=1+1"
    assert exported["Email"] == "'\r+guest@example.com"
    assert exported["WhatsApp"] == "'\n-CMD"
    assert exported["Batch"] == "' \t@SUM(1,1)"
    assert exported["Bank reference"] == "'\v=FORMULA"


def test_unknown_ticket_is_404(admin_client):
    assert admin_client.get("/tickets/9999").status_code == 404
    assert admin_client.get("/tickets/9999/qr.png").status_code == 404
    assert admin_client.post("/tickets/9999/resend", follow_redirects=False).status_code == 404


def test_ticket_pages_require_login(anon_client):
    for path in ("/tickets", "/tickets/new"):
        response = anon_client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login")


def test_anonymous_cannot_create_ticket(anon_client, app):
    response = create(anon_client)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert stored(app) == []


def test_ticketer_can_create_and_view_tickets(ticketer_client, app):
    response = create(ticketer_client)
    assert response.status_code == 303

    ticket = stored(app)[0]
    detail = ticketer_client.get(f"/tickets/{ticket.id}")
    assert detail.status_code == 200
    assert "Test Guest One" in detail.text
    assert "valid" in detail.text


def test_ticketer_can_only_access_own_tickets(ticketer_client, session, mailer):
    own_ticket = create(
        ticketer_client,
        name="Ticketer A Guest",
        email="a-guest@example.com",
        bank_reference="TICKETER-A-001",
    )
    assert own_ticket.status_code == 303
    own_id = int(own_ticket.headers["location"].rsplit("/", 1)[-1])

    other_user = create_ticketer(session, "ticketer2", "ticketer2-pass-123")
    assert other_user.id is not None
    other_ticket = issue_ticket_as(
        session,
        other_user.id,
        name="Ticketer B Guest",
        email="b-guest@example.com",
        reference="TICKETER-B-001",
    )

    listing = ticketer_client.get("/tickets")
    assert "Ticketer A Guest" in listing.text
    assert "Ticketer B Guest" not in listing.text

    own_search = ticketer_client.get("/tickets", params={"q": "Ticketer A Guest"})
    assert "Ticketer A Guest" in own_search.text
    other_search = ticketer_client.get("/tickets", params={"q": "Ticketer B Guest"})
    assert "Nothing matches" in other_search.text

    sent_count = len(mailer.sent)
    denied_responses = (
        ticketer_client.get(f"/tickets/{other_ticket.id}"),
        ticketer_client.get(f"/tickets/{other_ticket.id}/qr.png"),
        ticketer_client.post(f"/tickets/{other_ticket.id}/resend", follow_redirects=False),
    )
    missing_responses = (
        ticketer_client.get("/tickets/9999"),
        ticketer_client.get("/tickets/9999/qr.png"),
        ticketer_client.post("/tickets/9999/resend", follow_redirects=False),
    )
    for response, missing in zip(denied_responses, missing_responses, strict=True):
        assert response.status_code == 404
        assert "Ticket not found" in response.text
        assert response.text == missing.text
    assert len(mailer.sent) == sent_count

    own_detail = ticketer_client.get(f"/tickets/{own_id}")
    own_qr = ticketer_client.get(f"/tickets/{own_id}/qr.png")
    own_resend = ticketer_client.post(f"/tickets/{own_id}/resend", follow_redirects=False)
    assert own_detail.status_code == 200
    assert own_qr.status_code == 200
    assert own_qr.content.startswith(b"\x89PNG")
    assert own_resend.status_code == 303

    ticketer_client.post("/logout", follow_redirects=False)
    assert login(ticketer_client, ADMIN_USERNAME, ADMIN_PASSWORD).status_code == 303
    admin_listing = ticketer_client.get("/tickets")
    assert "Ticketer A Guest" in admin_listing.text
    assert "Ticketer B Guest" in admin_listing.text
    admin_search = ticketer_client.get("/tickets", params={"q": "Ticketer B Guest"})
    assert "Ticketer B Guest" in admin_search.text


def test_detail_and_list_hide_the_token(admin_client, app):
    create(admin_client)
    ticket = stored(app)[0]

    detail = admin_client.get(f"/tickets/{ticket.id}")
    listing = admin_client.get("/tickets")

    assert ticket.token not in detail.text
    assert ticket.token not in listing.text


def test_search_filters_by_name_and_reference(admin_client, app):
    create(admin_client, name="Test Guest One", bank_reference="REF-AAA")
    create(admin_client, name="Another Guest", bank_reference="REF-BBB", email="two@example.com")

    by_name = admin_client.get("/tickets?q=Another")
    assert "Another Guest" in by_name.text
    assert "Test Guest One" not in by_name.text

    by_reference = admin_client.get("/tickets?q=REF-AAA")
    assert "Test Guest One" in by_reference.text
    assert "Another Guest" not in by_reference.text

    nothing = admin_client.get("/tickets?q=nobody-here")
    assert "Nothing matches" in nothing.text


def test_qr_requires_login(anon_client, admin_client, app):
    create(admin_client)
    ticket = stored(app)[0]

    response = anon_client.get(f"/tickets/{ticket.id}/qr.png", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_email_failure_does_not_break_creation(admin_client, app, mailer):
    mailer.fail = True

    response = create(admin_client)

    assert response.status_code == 303
    tickets = stored(app)
    assert len(tickets) == 1
    assert tickets[0].email_status == EmailStatus.failed
    assert mailer.sent == []


def test_resend_uses_the_same_token(admin_client, app, mailer):
    create(admin_client)
    ticket = stored(app)[0]
    original_token = ticket.token
    first_qr = mailer.sent[0].qr_png

    response = admin_client.post(f"/tickets/{ticket.id}/resend", follow_redirects=False)

    assert response.status_code == 303
    assert len(mailer.sent) == 2
    assert mailer.sent[1].qr_png == first_qr
    assert stored(app)[0].token == original_token


def test_resend_recovers_after_a_failed_email(admin_client, app, mailer):
    mailer.fail = True
    create(admin_client)
    ticket = stored(app)[0]
    assert ticket.email_status == EmailStatus.failed

    mailer.fail = False
    admin_client.post(f"/tickets/{ticket.id}/resend", follow_redirects=False)

    assert stored(app)[0].email_status == EmailStatus.sent
