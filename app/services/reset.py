"""Reset the ticket database, with a backup and an in-use check."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, SQLModel, func, select

from app.config import Settings
from app.db import create_db_engine
from app.models import Role, Ticket, User
from app.services.users import seed_admin

SIDECARS = ("-wal", "-shm")


class ResetRefused(RuntimeError):
    """The reset cannot run safely right now."""


@dataclass(slots=True)
class ResetSummary:
    database: Path
    backup: Path | None
    tickets_deleted: int
    users_deleted: int
    admin: str


def _open_paths(path: Path) -> set[str]:
    candidates = [path, *(path.with_name(path.name + suffix) for suffix in SIDECARS)]
    return {str(candidate.resolve()) for candidate in candidates}


def database_in_use(path: Path, *, ignore_pid: int | None = None) -> list[int]:
    """Return PIDs holding the database file open (Linux /proc scan)."""
    wanted = _open_paths(path)
    holders: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return holders
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == ignore_pid:
            continue
        try:
            handles = list((entry / "fd").iterdir())
        except (PermissionError, FileNotFoundError):
            continue
        for handle in handles:
            try:
                target = os.readlink(handle)
            except OSError:
                continue
            if target.removesuffix(" (deleted)") in wanted:
                holders.append(pid)
                break
    return sorted(holders)


def _backup(path: Path) -> Path:
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backup_dir / f"{path.stem}-{stamp}.db"
    with sqlite3.connect(path) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    return destination


def reset_database_in_app(
    session: Session,
    settings: Settings,
    *,
    keep_users: bool,
    admin_username: str,
) -> ResetSummary:
    """Back up and reset records without replacing the live SQLite database file."""
    path = settings.db_path
    if not path.exists():
        raise ResetRefused("The database file is unavailable; no data was cleared.")

    try:
        backup_path = _backup(path)
    except (OSError, sqlite3.Error) as exc:
        raise ResetRefused("Could not create a backup; no data was cleared.") from exc

    try:
        tickets_deleted = session.exec(select(func.count()).select_from(Ticket)).one()
        ticketers_deleted = (
            0
            if keep_users
            else session.exec(
                select(func.count()).select_from(User).where(User.role == Role.ticketer)
            ).one()
        )
        session.exec(delete(Ticket))
        if not keep_users:
            session.exec(delete(User).where(User.role == Role.ticketer))
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        raise ResetRefused(
            f"Database reset failed; the backup is available at {backup_path}."
        ) from exc

    return ResetSummary(
        database=path,
        backup=backup_path,
        tickets_deleted=int(tickets_deleted),
        users_deleted=int(ticketers_deleted),
        admin=admin_username,
    )


def _count_rows(path: Path) -> tuple[int, int]:
    """Count tickets and users in an existing database, tolerating a blank file."""
    if not path.exists():
        return 0, 0
    with sqlite3.connect(path) as connection:
        tables = {
            name
            for (name,) in connection.execute("select name from sqlite_master where type='table'")
        }
        tickets = users = 0
        if "tickets" in tables:
            tickets = connection.execute("select count(*) from tickets").fetchone()[0]
        if "users" in tables:
            users = connection.execute("select count(*) from users").fetchone()[0]
    return int(tickets), int(users)


def reset_database(
    settings: Settings,
    *,
    keep_users: bool = False,
    backup: bool = True,
) -> ResetSummary:
    path = settings.db_path
    holders = database_in_use(path)
    if holders:
        pids = ", ".join(str(pid) for pid in holders)
        raise ResetRefused(f"the database is open by PID {pids}; stop the server first")

    backup_path = _backup(path) if path.exists() and backup else None
    tickets, users = _count_rows(path)

    if keep_users:
        engine = create_db_engine(settings)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            session.exec(delete(Ticket))
            session.commit()
            admin = seed_admin(session, settings)
        engine.dispose()
        return ResetSummary(
            database=path,
            backup=backup_path,
            tickets_deleted=tickets,
            users_deleted=0,
            admin=admin.username,
        )

    for suffix in ("", *SIDECARS):
        path.with_name(path.name + suffix).unlink(missing_ok=True)

    engine = create_db_engine(settings)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        admin = seed_admin(session, settings)
    engine.dispose()

    return ResetSummary(
        database=path,
        backup=backup_path,
        tickets_deleted=tickets,
        users_deleted=users,
        admin=admin.username,
    )
