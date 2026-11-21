from datetime import UTC, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class Role(StrEnum):
    admin = "admin"
    ticketer = "ticketer"


class TicketStatus(StrEnum):
    valid = "valid"
    checked_in = "checked_in"
    revoked = "revoked"


class TicketType(StrEnum):
    full = "full"
    half = "half"


class EmailStatus(StrEnum):
    pending = "pending"
    sent = "sent"
    failed = "failed"


class User(SQLModel, table=True):
    __tablename__ = "users"
    __table_args__ = (
        # A partial unique index makes "exactly one admin" a database guarantee.
        sa.Index(
            "uq_users_single_admin",
            "role",
            unique=True,
            sqlite_where=sa.text("role = 'admin'"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True, max_length=32)
    password_hash: str
    role: Role = Field(default=Role.ticketer, index=True)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)


class Ticket(SQLModel, table=True):
    __tablename__ = "tickets"
    __table_args__ = (
        sa.CheckConstraint("amount_paid >= 0", name="ck_tickets_amount_paid"),
        sa.CheckConstraint("total_price >= 0", name="ck_tickets_total_price"),
    )

    id: int | None = Field(default=None, primary_key=True)
    token: str = Field(index=True, unique=True, max_length=64)
    name: str = Field(max_length=120)
    email: str = Field(max_length=254)
    whatsapp_number: str = Field(max_length=32)
    batch: str | None = Field(default=None, max_length=60)
    # Money is stored in minor units (cents) to keep arithmetic exact.
    amount_paid: int = Field(default=0, ge=0)
    total_price: int = Field(default=0, ge=0)
    ticket_type: TicketType = Field(default=TicketType.full, index=True)
    bank_reference: str = Field(index=True, unique=True, max_length=80)
    balance_payment_reference: str | None = Field(default=None, max_length=80)
    balance_paid_at: datetime | None = Field(default=None)
    balance_paid_by: int | None = Field(default=None, foreign_key="users.id")
    status: TicketStatus = Field(default=TicketStatus.valid, index=True)
    issued_by: int = Field(foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    checked_in_at: datetime | None = Field(default=None)
    checked_in_by: int | None = Field(default=None, foreign_key="users.id")
    email_status: EmailStatus = Field(default=EmailStatus.pending)
