"""Ticket CRUD and validation. Routes stay thin; all rules live here."""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, EmailStr, Field, ValidationError, field_validator
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, or_, select

from app.config import Settings
from app.models import EmailStatus, Ticket, TicketStatus, TicketType, User

PHONE_RE = re.compile(r"^\+?[0-9]{9,15}$")
MAX_AMOUNT = Decimal("10000000")
FORM_FIELDS = (
    "name",
    "email",
    "whatsapp_number",
    "batch",
    "ticket_type",
    "amount",
    "bank_reference",
)
FIELD_LABELS = {
    "name": "Name",
    "email": "Email",
    "whatsapp_number": "WhatsApp number",
    "batch": "Batch",
    "amount": "Ticket price",
    "ticket_type": "Ticket type",
    "bank_reference": "Bank reference",
}


class TicketError(Exception):
    """Base for expected ticket problems."""


class TicketValidationError(TicketError):
    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = messages


class DuplicateBankReference(TicketError):
    def __init__(self, reference: str) -> None:
        super().__init__(reference)
        self.reference = reference


class TicketInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    whatsapp_number: str
    batch: str = Field(default="", max_length=60)
    ticket_type: TicketType = TicketType.full
    amount: Decimal = Field(ge=Decimal(0), le=MAX_AMOUNT)
    bank_reference: str = Field(min_length=3, max_length=80)

    @field_validator("name", mode="before")
    @classmethod
    def _clean_name(cls, value: Any) -> Any:
        if isinstance(value, str):
            return " ".join(value.split())
        return value

    @field_validator("email", mode="before")
    @classmethod
    def _clean_email(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("whatsapp_number", mode="before")
    @classmethod
    def _clean_phone(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        digits = re.sub(r"[\s\-().]", "", value)
        if not PHONE_RE.match(digits):
            raise ValueError("Enter a WhatsApp number with 9-15 digits, e.g. 0771234567.")
        return digits

    @field_validator("batch", mode="before")
    @classmethod
    def _clean_batch(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("amount", mode="before")
    @classmethod
    def _clean_amount(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.replace(",", "").replace(" ", "").strip()
            if not value:
                raise ValueError("Enter the full ticket price, e.g. 1500 or 1500.50.")
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(
                "Enter the full ticket price as a number, e.g. 1500 or 1500.50."
            ) from exc
        if not amount.is_finite():
            raise ValueError("Enter a real amount.")
        if amount < 0 or amount > MAX_AMOUNT:
            return amount
        cents = amount.quantize(Decimal("0.01"))
        if amount != cents:
            raise ValueError("Enter an amount with no more than two decimal places.")
        return cents

    @field_validator("bank_reference", mode="before")
    @classmethod
    def _clean_reference(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("ticket_type", mode="before")
    @classmethod
    def _clean_ticket_type(cls, value: Any) -> Any:
        return value or TicketType.full


def parse_ticket_input(data: dict[str, str]) -> TicketInput:
    """Validate raw form values, raising one readable message per bad field."""
    try:
        return TicketInput(**{field: data.get(field, "") for field in FORM_FIELDS})
    except ValidationError as exc:
        messages = []
        for error in exc.errors():
            field = str(error["loc"][0])
            label = FIELD_LABELS.get(field, field)
            message = str(error["msg"]).removeprefix("Value error, ")
            messages.append(f"{label}: {message}")
        raise TicketValidationError(messages) from exc


def create_ticket(session: Session, data: TicketInput, *, issued_by: int) -> Ticket:
    """Persist a valid ticket. Duplicate references are rejected by the DB too."""
    reference = data.bank_reference
    exists = session.exec(
        select(Ticket.id).where(
            or_(Ticket.bank_reference == reference, Ticket.balance_payment_reference == reference)
        )
    ).first()
    if exists is not None:
        raise DuplicateBankReference(reference)

    total_price = int(data.amount * 100)
    if data.ticket_type is TicketType.half and total_price % 2:
        raise TicketValidationError(
            ["Ticket price: half ticket price must split into whole cents."]
        )
    amount_paid = total_price if data.ticket_type is TicketType.full else total_price // 2

    for _ in range(3):
        ticket = Ticket(
            token=secrets.token_urlsafe(32),
            name=data.name,
            email=str(data.email),
            whatsapp_number=data.whatsapp_number,
            batch=data.batch or None,
            amount_paid=amount_paid,
            total_price=total_price,
            ticket_type=data.ticket_type,
            bank_reference=reference,
            status=TicketStatus.valid,
            email_status=EmailStatus.pending,
            issued_by=issued_by,
        )
        session.add(ticket)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if "bank_reference" in str(exc.orig) or "balance_payment_reference" in str(exc.orig):
                raise DuplicateBankReference(reference) from exc
            continue
        session.refresh(ticket)
        return ticket

    raise TicketError("Could not allocate a unique ticket token. Try again.")


def get_ticket(session: Session, ticket_id: int) -> Ticket | None:
    return session.get(Ticket, ticket_id)


def find_by_token(session: Session, token: str) -> Ticket | None:
    return session.exec(select(Ticket).where(Ticket.token == token)).first()


def revoke_ticket(session: Session, ticket_id: int) -> str:
    """Atomically flip a valid ticket to revoked.

    Returns ``revoked``, ``already_revoked``, ``checked_in`` or ``missing``.
    A ticket that has already admitted someone is never un-admitted.
    """
    result = session.execute(
        update(Ticket)
        .where(Ticket.id == ticket_id, Ticket.status == TicketStatus.valid)
        .values(status=TicketStatus.revoked)
    )
    if result.rowcount == 1:
        session.commit()
        return "revoked"

    session.rollback()
    ticket = get_ticket(session, ticket_id)
    if ticket is None:
        return "missing"
    if ticket.status is TicketStatus.revoked:
        return "already_revoked"
    return "checked_in"


def event_payment_deadline(settings: Settings) -> datetime:
    """Return the event-local time exactly seven days before the event."""
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(settings.event_timezone)
    event_time = settings.event_datetime
    event_time = event_time.replace(tzinfo=zone) if event_time.tzinfo is None else event_time
    return event_time.astimezone(zone) - timedelta(days=7)


def record_balance_payment(
    session: Session,
    ticket: Ticket,
    actor: User,
    reference: str,
    *,
    settings: Settings,
    confirm_late: bool = False,
    now: datetime | None = None,
) -> str:
    """Record the remaining half-ticket payment; return a result code."""
    if ticket.ticket_type is not TicketType.half:
        return "not_half"
    if ticket.balance_paid_at is not None:
        return "already_paid"
    normalized = reference.strip().upper()
    if not 3 <= len(normalized) <= 80:
        return "invalid_reference"
    duplicate = session.exec(
        select(Ticket.id).where(
            or_(Ticket.bank_reference == normalized, Ticket.balance_payment_reference == normalized)
        )
    ).first()
    if duplicate is not None:
        return "duplicate_reference"

    current = now or datetime.now(UTC)
    deadline = event_payment_deadline(settings).astimezone(UTC)
    is_late = current > deadline
    if is_late and not confirm_late:
        return "late_confirmation_required"

    result = session.execute(
        update(Ticket)
        .where(
            Ticket.id == ticket.id,
            Ticket.ticket_type == TicketType.half,
            Ticket.balance_paid_at.is_(None),
        )
        .values(
            balance_payment_reference=normalized,
            balance_paid_at=current,
            balance_paid_by=actor.id,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return "already_paid"
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return "duplicate_reference"
    return "paid_late" if is_late else "paid"


def all_tickets(session: Session) -> list[Ticket]:
    return list(session.exec(select(Ticket).order_by(Ticket.id)).all())


def search_tickets(
    session: Session,
    query: str = "",
    *,
    email_status: EmailStatus | None = None,
    issued_by: int | None = None,
    limit: int = 500,
) -> list[Ticket]:
    statement = select(Ticket)
    if issued_by is not None:
        statement = statement.where(Ticket.issued_by == issued_by)
    term = query.strip()
    if term:
        pattern = f"%{term}%"
        statement = statement.where(
            or_(
                Ticket.name.ilike(pattern),
                Ticket.email.ilike(pattern),
                Ticket.whatsapp_number.ilike(pattern),
                Ticket.bank_reference.ilike(pattern),
            )
        )
    if email_status is not None:
        statement = statement.where(Ticket.email_status == email_status)
    statement = statement.order_by(Ticket.created_at.desc(), Ticket.id.desc()).limit(limit)
    return list(session.exec(statement).all())


def set_email_status(session: Session, ticket: Ticket, status: EmailStatus) -> None:
    ticket.email_status = status
    session.add(ticket)
    session.commit()
