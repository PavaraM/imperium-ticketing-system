"""Door scanning: read-only lookup, name search, and the single atomic check-in."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, unquote, urlparse

from sqlalchemy import func, or_, update
from sqlmodel import Session, select

from app.config import Settings
from app.models import Ticket, TicketStatus, TicketType, User, utcnow
from app.services.tickets import event_payment_deadline

# Generated tokens are urlsafe base64; validating the shape keeps junk input away
# from the database without leaking whether a real token exists.
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
MAX_SEARCH_RESULTS = 8


@dataclass(slots=True)
class ScanTicket:
    """The only guest data the door needs. Never carries a token."""

    ticket_id: int
    name: str
    batch: str | None
    status: TicketStatus | str
    checked_in_at: datetime | None
    checked_in_by: str | None
    ticket_type: TicketType
    amount_paid: int
    total_price: int
    balance_due: int


def normalize_scan_payload(raw: str) -> str:
    """Return the token from a bare token or a URL that carries one."""
    value = raw.strip().strip("\"'").strip()
    if not value:
        return ""
    if "://" in value:
        parsed = urlparse(value)
        segments = [unquote(part) for part in parsed.path.split("/") if part]
        if segments:
            value = segments[-1]
        else:
            query = parse_qs(parsed.query)
            value = (query.get("t") or query.get("token") or [""])[0]
    return value.strip()


def _snapshot(session: Session, ticket: Ticket, settings: Settings) -> ScanTicket:
    checker = session.get(User, ticket.checked_in_by) if ticket.checked_in_by else None
    state: TicketStatus | str = ticket.status
    if (
        ticket.status is TicketStatus.valid
        and ticket.ticket_type is TicketType.half
        and ticket.balance_paid_at is None
    ):
        deadline = event_payment_deadline(settings).astimezone(UTC)
        state = "payment_overdue" if utcnow() > deadline else "payment_due"
    return ScanTicket(
        ticket_id=ticket.id or 0,
        name=ticket.name,
        batch=ticket.batch or None,
        status=state,
        checked_in_at=ticket.checked_in_at,
        checked_in_by=checker.username if checker else None,
        ticket_type=ticket.ticket_type,
        amount_paid=ticket.amount_paid,
        total_price=ticket.total_price,
        balance_due=(
            ticket.total_price - ticket.amount_paid
            if ticket.ticket_type is TicketType.half and ticket.balance_paid_at is None
            else 0
        ),
    )


def _find(session: Session, *, token: str = "", ticket_id: int | None = None) -> Ticket | None:
    if ticket_id is not None:
        return session.get(Ticket, ticket_id)
    candidate = normalize_scan_payload(token)
    if not TOKEN_RE.match(candidate):
        return None
    return session.exec(select(Ticket).where(Ticket.token == candidate)).first()


def resolve(
    session: Session, settings: Settings, *, token: str = "", ticket_id: int | None = None
) -> ScanTicket | None:
    """Read-only lookup. Never changes ticket state."""
    ticket = _find(session, token=token, ticket_id=ticket_id)
    return _snapshot(session, ticket, settings) if ticket else None


def check_in(
    session: Session,
    actor: User,
    settings: Settings,
    *,
    token: str = "",
    ticket_id: int | None = None,
) -> tuple[ScanTicket | None, bool]:
    """Admit a ticket in one atomic statement.

    Returns ``(snapshot, admitted)``. ``admitted`` is False when the row was not
    ``valid`` at the moment the UPDATE ran, so two phones can never both win.
    """
    conditions = [
        Ticket.status == TicketStatus.valid,
        or_(
            Ticket.ticket_type == TicketType.full,
            (Ticket.ticket_type == TicketType.half) & Ticket.balance_paid_at.is_not(None),
        ),
    ]
    if ticket_id is not None:
        conditions.append(Ticket.id == ticket_id)
    else:
        candidate = normalize_scan_payload(token)
        if not TOKEN_RE.match(candidate):
            return None, False
        conditions.append(Ticket.token == candidate)

    result = session.execute(
        update(Ticket)
        .where(*conditions)
        .values(
            status=TicketStatus.checked_in,
            checked_in_at=utcnow(),
            checked_in_by=actor.id,
        )
    )
    admitted = result.rowcount == 1
    session.commit()

    snapshot = resolve(session, settings, token=token, ticket_id=ticket_id)
    return snapshot, admitted


def search_by_name(
    session: Session, settings: Settings, query: str, *, limit: int = MAX_SEARCH_RESULTS
):
    """Manual fallback when a QR will not scan. Returns snapshots, never tokens."""
    needle = query.strip().lower()
    if not needle:
        return []
    statement = (
        select(Ticket)
        .where(func.lower(Ticket.name).contains(needle, autoescape=True))
        .order_by(Ticket.name)
        .limit(limit)
    )
    return [_snapshot(session, ticket, settings) for ticket in session.exec(statement)]
