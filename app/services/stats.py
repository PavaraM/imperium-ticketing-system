from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case
from sqlmodel import Session, func, select

from app.models import EmailStatus, Role, Ticket, TicketStatus, User


@dataclass(slots=True)
class Totals:
    sold: int
    admitted: int
    revenue: int
    ticketers: int
    failed_emails: int


@dataclass(slots=True)
class SalesRow:
    username: str
    role: Role
    issued: int
    admitted: int
    revenue: int


@dataclass(slots=True)
class Admission:
    ticket_id: int
    name: str
    batch: str | None
    checked_in_at: datetime
    by: str


def count_users(session: Session, role: Role | None = None) -> int:
    statement = select(func.count()).select_from(User)
    if role is not None:
        statement = statement.where(User.role == role)
    return session.exec(statement).one()


def count_tickets(session: Session, status: TicketStatus | None = None) -> int:
    statement = select(func.count()).select_from(Ticket)
    if status is not None:
        statement = statement.where(Ticket.status == status)
    return session.exec(statement).one()


def ticket_totals(session: Session) -> Totals:
    sold = count_tickets(session) - count_tickets(session, TicketStatus.revoked)
    revenue = session.exec(
        select(func.coalesce(func.sum(Ticket.amount_paid), 0)).where(
            Ticket.status != TicketStatus.revoked
        )
    ).one()
    failed = session.exec(
        select(func.count())
        .select_from(Ticket)
        .where(
            Ticket.email_status == EmailStatus.failed,
            Ticket.status != TicketStatus.revoked,
        )
    ).one()
    return Totals(
        sold=sold,
        admitted=count_tickets(session, TicketStatus.checked_in),
        revenue=int(revenue or 0),
        ticketers=count_users(session, Role.ticketer),
        failed_emails=failed,
    )


def sales_by_staff(session: Session) -> list[SalesRow]:
    """Issued tickets and revenue per staff account, revoked ones excluded."""
    admitted_count = func.coalesce(
        func.sum(case((Ticket.status == TicketStatus.checked_in, 1), else_=0)), 0
    )
    statement = (
        select(
            User.username,
            User.role,
            func.count(Ticket.id),
            admitted_count,
            func.coalesce(func.sum(Ticket.amount_paid), 0),
        )
        .join(
            Ticket,
            (Ticket.issued_by == User.id) & (Ticket.status != TicketStatus.revoked),
            isouter=True,
        )
        .group_by(User.id)
        .order_by(func.count(Ticket.id).desc(), User.username)
    )
    return [
        SalesRow(
            username=username,
            role=role,
            issued=int(issued),
            admitted=int(admitted),
            revenue=int(revenue or 0),
        )
        for username, role, issued, admitted, revenue in session.exec(statement).all()
    ]


def recent_admissions(session: Session, limit: int = 8) -> list[Admission]:
    statement = (
        select(Ticket, User.username)
        .join(User, Ticket.checked_in_by == User.id, isouter=True)
        .where(Ticket.status == TicketStatus.checked_in, Ticket.checked_in_at.is_not(None))
        .order_by(Ticket.checked_in_at.desc())
        .limit(limit)
    )
    return [
        Admission(
            ticket_id=ticket.id,
            name=ticket.name,
            batch=ticket.batch,
            checked_in_at=ticket.checked_in_at,
            by=username or "—",
        )
        for ticket, username in session.exec(statement).all()
    ]
