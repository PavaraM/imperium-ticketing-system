"""Ticket CRUD and validation. Routes stay thin; all rules live here."""

from __future__ import annotations

import re
import secrets
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, EmailStr, Field, ValidationError, field_validator
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, or_, select

from app.models import EmailStatus, Ticket, TicketStatus

PHONE_RE = re.compile(r"^\+?[0-9]{9,15}$")
MAX_AMOUNT = Decimal("10000000")
FORM_FIELDS = ("name", "email", "whatsapp_number", "batch", "amount", "bank_reference")
FIELD_LABELS = {
    "name": "Name",
    "email": "Email",
    "whatsapp_number": "WhatsApp number",
    "batch": "Batch",
    "amount": "Amount paid",
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
                raise ValueError("Enter the amount paid, e.g. 1500 or 1500.50.")
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("Enter the amount paid as a number, e.g. 1500 or 1500.50.") from exc
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
    exists = session.exec(select(Ticket.id).where(Ticket.bank_reference == reference)).first()
    if exists is not None:
        raise DuplicateBankReference(reference)

    for _ in range(3):
        ticket = Ticket(
            token=secrets.token_urlsafe(32),
            name=data.name,
            email=str(data.email),
            whatsapp_number=data.whatsapp_number,
            batch=data.batch or None,
            amount_paid=int(data.amount * 100),
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
            if "bank_reference" in str(exc.orig):
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
