"""Database reset: backup, wipe, admin re-seed, and the in-use guard."""

import os
import sqlite3

import pytest
from sqlmodel import Session, SQLModel, func, select

from app.db import create_db_engine
from app.models import Role, Ticket, User
from app.services.reset import ResetRefused, database_in_use, reset_database
from tests.conftest import ADMIN_USERNAME, TICKETER_USERNAME


def _issue(client, reference: str, name: str = "Reset Guest") -> int:
    response = client.post(
        "/tickets",
        data={
            "name": name,
            "email": "reset@example.com",
            "whatsapp_number": "0771234567",
            "batch": "Batch A",
            "amount": "1500",
            "bank_reference": reference,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return int(response.headers["location"].rsplit("/", 1)[-1])


def test_full_reset_wipes_everything_and_reseeds_admin(app, settings, admin_client):
    _issue(admin_client, "RESET-0001")
    app.state.engine.dispose()

    summary = reset_database(settings)

    assert summary.tickets_deleted == 1
    assert summary.users_deleted == 1
    assert summary.admin == ADMIN_USERNAME
    assert summary.backup is not None and summary.backup.exists()

    with sqlite3.connect(summary.backup) as backup:
        assert backup.execute("select count(*) from tickets").fetchone()[0] == 1

    engine = create_db_engine(settings)
    with Session(engine) as session:
        assert session.exec(select(func.count()).select_from(Ticket)).one() == 0
        users = session.exec(select(User)).all()
        assert [user.username for user in users] == [ADMIN_USERNAME]
        assert users[0].role is Role.admin
    engine.dispose()


def test_tickets_only_reset_keeps_staff_accounts(app, settings, admin_client):
    admin_client.post(
        "/admin/users",
        data={"username": TICKETER_USERNAME, "password": "ticketer-pass-1"},
        follow_redirects=False,
    )
    _issue(admin_client, "RESET-0002")
    app.state.engine.dispose()

    summary = reset_database(settings, keep_users=True)

    assert summary.tickets_deleted == 1
    assert summary.users_deleted == 0

    engine = create_db_engine(settings)
    with Session(engine) as session:
        assert session.exec(select(func.count()).select_from(Ticket)).one() == 0
        usernames = {user.username for user in session.exec(select(User)).all()}
    engine.dispose()

    assert usernames == {ADMIN_USERNAME, TICKETER_USERNAME}


def test_reset_without_backup_leaves_no_snapshot(app, settings, admin_client):
    _issue(admin_client, "RESET-0003")
    app.state.engine.dispose()

    summary = reset_database(settings, backup=False)

    assert summary.backup is None
    assert not (settings.db_path.parent / "backups").exists()


def test_database_in_use_detects_an_open_connection(settings):
    engine = create_db_engine(settings)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.exec(select(func.count()).select_from(Ticket)).one()

    holders = database_in_use(settings.db_path)

    engine.dispose()
    assert os.getpid() in holders


def test_reset_refuses_while_the_database_is_open(settings):
    engine = create_db_engine(settings)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.exec(select(func.count()).select_from(Ticket)).one()

    with pytest.raises(ResetRefused, match="stop the server"):
        reset_database(settings)

    engine.dispose()
    assert settings.db_path.exists()


def test_admin_panel_tickets_only_reset_backs_up_and_keeps_staff(app, admin_client, settings):
    admin_client.post(
        "/admin/users",
        data={"username": TICKETER_USERNAME, "password": "ticketer-pass-1"},
        follow_redirects=False,
    )
    _issue(admin_client, "PANEL-RESET-0001")

    response = admin_client.post(
        "/admin/reset",
        data={"scope": "tickets-only", "confirmation": "RESET"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(app.state.engine) as session:
        assert session.exec(select(func.count()).select_from(Ticket)).one() == 0
        usernames = {user.username for user in session.exec(select(User)).all()}
    assert usernames == {ADMIN_USERNAME, TICKETER_USERNAME}

    backups = list((settings.db_path.parent / "backups").glob("*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("select count(*) from tickets").fetchone()[0] == 1


def test_admin_panel_full_reset_keeps_admin_and_removes_ticketers(app, admin_client, settings):
    admin_client.post(
        "/admin/users",
        data={"username": TICKETER_USERNAME, "password": "ticketer-pass-1"},
        follow_redirects=False,
    )
    _issue(admin_client, "PANEL-RESET-0002")
    with Session(app.state.engine) as session:
        original_admin = session.exec(select(User).where(User.role == Role.admin)).one()
        original_password_hash = original_admin.password_hash

    response = admin_client.post(
        "/admin/reset",
        data={"scope": "full", "confirmation": "RESET"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert admin_client.get("/admin/users").status_code == 200
    with Session(app.state.engine) as session:
        assert session.exec(select(func.count()).select_from(Ticket)).one() == 0
        users = session.exec(select(User)).all()
    assert len(users) == 1
    assert users[0].role is Role.admin
    assert users[0].username == ADMIN_USERNAME
    assert users[0].password_hash == original_password_hash

    backups = list((settings.db_path.parent / "backups").glob("*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("select count(*) from tickets").fetchone()[0] == 1
        assert backup.execute("select count(*) from users").fetchone()[0] == 2


def test_admin_panel_reset_requires_typed_confirmation(app, admin_client, settings):
    _issue(admin_client, "PANEL-RESET-0003")

    response = admin_client.post(
        "/admin/reset",
        data={"scope": "full", "confirmation": "reset"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(app.state.engine) as session:
        assert session.exec(select(func.count()).select_from(Ticket)).one() == 1
    assert not (settings.db_path.parent / "backups").exists()
    page = admin_client.get("/admin/users")
    assert "Database reset cancelled" in page.text
    assert "Type RESET to confirm" in page.text


def test_ticketer_cannot_reset_database(ticketer_client, app, settings):
    _issue(ticketer_client, "PANEL-RESET-0004")

    response = ticketer_client.post(
        "/admin/reset",
        data={"scope": "full", "confirmation": "RESET"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    with Session(app.state.engine) as session:
        assert session.exec(select(func.count()).select_from(Ticket)).one() == 1
    assert not (settings.db_path.parent / "backups").exists()


def test_admin_panel_reset_stops_if_backup_fails(admin_client, app, settings, monkeypatch):
    from app.services import reset

    _issue(admin_client, "PANEL-RESET-0005")

    def fail_backup(_path):
        raise OSError("disk unavailable")

    monkeypatch.setattr(reset, "_backup", fail_backup)
    response = admin_client.post(
        "/admin/reset",
        data={"scope": "tickets-only", "confirmation": "RESET"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(app.state.engine) as session:
        assert session.exec(select(func.count()).select_from(Ticket)).one() == 1
    page = admin_client.get("/admin/users")
    assert "Could not create a backup" in page.text
