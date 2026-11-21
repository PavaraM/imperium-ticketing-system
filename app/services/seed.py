"""Demo data for development. Fake names only — never point this at a real event."""

from __future__ import annotations

import random
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, SQLModel, select

from app.config import Settings
from app.db import create_db_engine
from app.migrations.ticket_payments import migrate_ticket_payments
from app.models import EmailStatus, Ticket, TicketStatus, User
from app.services.users import create_ticketer, get_by_username, seed_admin

DEMO_PASSWORD = "demo-pass-123"
FIRST_NAMES = (
    "Amara",
    "Nimal",
    "Kavindi",
    "Sahan",
    "Dilani",
    "Ruwan",
    "Ishara",
    "Tharindu",
    "Nadeesha",
    "Chamara",
    "Hiruni",
    "Pasindu",
    "Sachini",
    "Lahiru",
    "Malsha",
    "Dinesh",
    "Ayesha",
    "Kalana",
    "Thilini",
    "Bimal",
)
LAST_NAMES = (
    "Perera",
    "Fernando",
    "Jayasinghe",
    "Wickramasinghe",
    "Silva",
    "Bandara",
    "Rathnayake",
    "Gunawardena",
    "Dissanayake",
    "Herath",
)
BATCHES = ("Batch A", "Batch B", "Batch C")
AMOUNTS = (1500, 2000, 2500)


@dataclass(slots=True)
class SeedSummary:
    created: int
    skipped: int
    ticketers: list[str] = field(default_factory=list)


def seed_demo_data(
    settings: Settings,
    *,
    count: int = 50,
    ticketer_count: int = 2,
    password: str = DEMO_PASSWORD,
    prefix: str = "DEMO",
) -> SeedSummary:
    """Insert fake guests, mixed statuses, idempotent on bank reference."""
    if count < 1:
        raise ValueError("count must be at least 1")

    engine = create_db_engine(settings)
    SQLModel.metadata.create_all(engine)
    migrate_ticket_payments(engine)
    created = 0
    skipped = 0
    staff_names: list[str] = []

    try:
        with Session(engine) as session:
            admin = seed_admin(session, settings)
            staff: list[User] = [admin]
            staff_names.append(admin.username)
            for index in range(1, ticketer_count + 1):
                username = f"door-{index}"
                user = get_by_username(session, username)
                if user is None:
                    user = create_ticketer(session, username, password)
                staff.append(user)
                staff_names.append(user.username)

            existing = set(session.exec(select(Ticket.bank_reference)).all())
            rng = random.Random(20261219)
            now = datetime.now(UTC)
            checked_in_from = count - 7
            revoked_from = count - 1

            for index in range(1, count + 1):
                reference = f"{prefix}-{index:04d}"
                if reference in existing:
                    skipped += 1
                    continue

                if index >= revoked_from:
                    status = TicketStatus.revoked
                elif index >= checked_in_from:
                    status = TicketStatus.checked_in
                else:
                    status = TicketStatus.valid

                created_at = now - timedelta(hours=count - index, minutes=rng.randrange(0, 60))
                checked_in_at = None
                checked_in_by = None
                if status is TicketStatus.checked_in:
                    checked_in_at = now - timedelta(minutes=(count - index) * 3)
                    checked_in_by = staff[index % len(staff)].id

                amount = AMOUNTS[index % len(AMOUNTS)] * 100
                session.add(
                    Ticket(
                        token=secrets.token_urlsafe(32),
                        name=(
                            f"{FIRST_NAMES[(index - 1) % len(FIRST_NAMES)]} "
                            f"{LAST_NAMES[(index * 7) % len(LAST_NAMES)]}"
                        ),
                        email=f"guest{index:02d}@example.com",
                        whatsapp_number=f"077{1000000 + index:07d}",
                        batch=None if index % 9 == 0 else BATCHES[index % len(BATCHES)],
                        amount_paid=amount,
                        total_price=amount,
                        bank_reference=reference,
                        status=status,
                        issued_by=staff[index % len(staff)].id or admin.id or 0,
                        created_at=created_at,
                        checked_in_at=checked_in_at,
                        checked_in_by=checked_in_by,
                        email_status=(EmailStatus.failed if index == 7 else EmailStatus.sent),
                    )
                )
                created += 1
                if created % 25 == 0:
                    session.commit()

            session.commit()
    finally:
        engine.dispose()

    return SeedSummary(
        created=created,
        skipped=skipped,
        ticketers=staff_names,
    )
