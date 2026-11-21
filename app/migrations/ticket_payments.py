"""Add ticket type and balance payment fields without discarding ticket data."""

from sqlalchemy import Engine, inspect, text


def migrate_ticket_payments(engine: Engine) -> None:
    """Add payment columns and mark all pre-existing tickets as paid full tickets."""
    columns = {column["name"] for column in inspect(engine).get_columns("tickets")}
    additions = {
        "total_price": "INTEGER NOT NULL DEFAULT 0",
        "ticket_type": "VARCHAR NOT NULL DEFAULT 'full'",
        "balance_payment_reference": "VARCHAR",
        "balance_paid_at": "DATETIME",
        "balance_paid_by": "INTEGER REFERENCES users(id)",
        "reservation_email_status": "VARCHAR",
    }
    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE tickets ADD COLUMN {name} {definition}"))
        connection.execute(
            text(
                "UPDATE tickets SET total_price = amount_paid "
                "WHERE total_price = 0 AND amount_paid > 0"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_tickets_balance_payment_reference ON tickets(balance_payment_reference)"
            )
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_tickets_ticket_type ON tickets(ticket_type)")
        )
