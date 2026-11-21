"""The additive ticket payment migration preserves legacy ticket rows."""

from sqlalchemy import create_engine, text

from app.migrations.ticket_payments import migrate_ticket_payments


def test_legacy_tickets_migrate_as_paid_full_tickets(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        connection.execute(
            text("CREATE TABLE tickets (id INTEGER PRIMARY KEY, amount_paid INTEGER NOT NULL)")
        )
        connection.execute(text("INSERT INTO tickets (id, amount_paid) VALUES (1, 150000)"))

    migrate_ticket_payments(engine)
    migrate_ticket_payments(engine)

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT amount_paid, total_price, ticket_type, reservation_email_status "
                "FROM tickets WHERE id = 1"
            )
        ).one()
    assert row == (150000, 150000, "full", None)
    engine.dispose()
