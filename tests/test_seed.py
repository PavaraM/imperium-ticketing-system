from sqlmodel import Session, select

from app.db import create_db_engine
from app.models import EmailStatus, Role, Ticket, TicketStatus, User
from app.services.seed import seed_demo_data
from app.services.users import authenticate, get_admin


def test_seed_creates_mixed_fake_guests(settings):
    summary = seed_demo_data(settings, count=50, ticketer_count=2)

    assert summary.created == 50
    assert summary.skipped == 0
    assert summary.ticketers == ["admin", "door-1", "door-2"]

    engine = create_db_engine(settings)
    try:
        with Session(engine) as session:
            tickets = session.exec(select(Ticket)).all()
            users = session.exec(select(User).where(User.role == Role.ticketer)).all()
            admin = get_admin(session)
            statuses = {ticket.status for ticket in tickets}
            references = {ticket.bank_reference for ticket in tickets}
            tokens = {ticket.token for ticket in tickets}
            emails = [ticket.email for ticket in tickets]
            amounts = [ticket.amount_paid for ticket in tickets]
            issuers = [ticket.issued_by for ticket in tickets]
            checked_in = [t for t in tickets if t.status is TicketStatus.checked_in]
            admissions = [(t.checked_in_at, t.checked_in_by) for t in checked_in]
            failed_emails = any(t.email_status is EmailStatus.failed for t in tickets)
    finally:
        engine.dispose()

    assert len(tickets) == 50
    assert len(users) == 2
    assert admin is not None
    assert statuses == {
        TicketStatus.valid,
        TicketStatus.checked_in,
        TicketStatus.revoked,
    }
    assert len(references) == 50
    assert len(tokens) == 50
    assert all(len(token) >= 32 for token in tokens)
    assert all(email.endswith("@example.com") for email in emails)
    assert all(amount > 0 for amount in amounts)
    assert all(issuers)
    assert admissions
    assert all(checked_at is not None and by for checked_at, by in admissions)
    assert failed_emails


def test_seed_is_idempotent(settings):
    first = seed_demo_data(settings, count=10, ticketer_count=1)
    second = seed_demo_data(settings, count=10, ticketer_count=1)

    assert first.created == 10
    assert second.created == 0
    assert second.skipped == 10

    engine = create_db_engine(settings)
    try:
        with Session(engine) as session:
            assert len(session.exec(select(Ticket)).all()) == 10
            assert len(session.exec(select(User).where(User.role == Role.ticketer)).all()) == 1
    finally:
        engine.dispose()


def test_seed_ticketers_can_log_in(settings):
    seed_demo_data(settings, count=1, ticketer_count=1, password="demo-pass-999")

    engine = create_db_engine(settings)
    try:
        with Session(engine) as session:
            user = authenticate(session, "door-1", "demo-pass-999")
            assert user is not None
            assert user.role is Role.ticketer
            assert user.is_active
    finally:
        engine.dispose()


def test_seed_respects_prefix_and_count(settings):
    summary = seed_demo_data(settings, count=3, ticketer_count=0, prefix="TEST")

    assert summary.created == 3

    engine = create_db_engine(settings)
    try:
        with Session(engine) as session:
            references = sorted(t.bank_reference for t in session.exec(select(Ticket)).all())
    finally:
        engine.dispose()

    assert references == ["TEST-0001", "TEST-0002", "TEST-0003"]
