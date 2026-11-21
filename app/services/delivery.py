"""Background email delivery. Runs with its own DB session, never blocks creation."""

from __future__ import annotations

import logging

from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.models import EmailStatus, Ticket, TicketType
from app.services.mailer import Mailer
from app.services.qr import render_qr_png

logger = logging.getLogger("app.delivery")


def deliver_reservation_email(engine: Engine, mailer: Mailer, ticket_id: int) -> None:
    """Send the QR-free half-ticket reservation notice and record its outcome."""
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        if (
            ticket is None
            or ticket.ticket_type is not TicketType.half
            or ticket.balance_paid_at is not None
        ):
            return

        status = EmailStatus.failed
        try:
            mailer.send_reservation(
                to=ticket.email,
                guest_name=ticket.name,
                ticket_id=ticket_id,
                total_price=ticket.total_price,
                amount_paid=ticket.amount_paid,
            )
        except Exception:  # noqa: BLE001 - delivery failure must not undo a reservation
            logger.warning("reservation email failed for ticket %s", ticket_id)
        else:
            status = EmailStatus.sent

        ticket.reservation_email_status = status
        session.add(ticket)
        session.commit()


def deliver_ticket_email(engine: Engine, mailer: Mailer, ticket_id: int) -> None:
    """Send one ticket email and record the outcome. Failures are contained."""
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        if ticket is None or (
            ticket.ticket_type is TicketType.half and ticket.balance_paid_at is None
        ):
            return

        status = EmailStatus.failed
        try:
            qr_png = render_qr_png(ticket.token)
            mailer.send_ticket(
                to=ticket.email,
                guest_name=ticket.name,
                ticket_id=ticket_id,
                qr_png=qr_png,
                ticket_type=ticket.ticket_type.value,
                amount_paid=ticket.amount_paid,
                total_price=ticket.total_price,
                balance_paid=ticket.balance_paid_at is not None,
            )
        except Exception:  # noqa: BLE001 - any transport problem must not crash the app
            logger.warning("ticket email failed for ticket %s", ticket_id)
        else:
            status = EmailStatus.sent

        ticket.email_status = status
        session.add(ticket)
        session.commit()
