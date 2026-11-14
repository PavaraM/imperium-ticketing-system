import re

from sqlmodel import Session, select

from app.config import Settings
from app.models import Role, User
from app.security import burn_password_check, hash_password, verify_password

USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
MIN_PASSWORD_LENGTH = 8


class UserError(Exception):
    """Base class for user-management failures that are safe to show to an admin."""


class InvalidUserData(UserError):
    pass


class UsernameTaken(UserError):
    pass


def normalize_username(raw: str) -> str:
    username = raw.strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise InvalidUserData(
            "Username must be 3-32 characters: lowercase letters, digits, dot, dash or underscore."
        )
    return username


def validate_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise InvalidUserData(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > 200:
        raise InvalidUserData("Password is too long.")
    return password


def get_by_username(session: Session, username: str) -> User | None:
    normalized = username.strip().lower()
    return session.exec(select(User).where(User.username == normalized)).first()


def create_ticketer(session: Session, username: str, password: str) -> User:
    normalized = normalize_username(username)
    validate_password(password)
    if get_by_username(session, normalized) is not None:
        raise UsernameTaken("That username is already taken.")
    user = User(
        username=normalized,
        password_hash=hash_password(password),
        role=Role.ticketer,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def authenticate(session: Session, username: str, password: str) -> User | None:
    """Return an active user for valid credentials, otherwise ``None``."""
    user = get_by_username(session, username)
    if user is None:
        burn_password_check(password)
        return None
    if not verify_password(password, user.password_hash):
        return None
    if not user.is_active:
        return None
    return user


def set_active(session: Session, user: User, make_active: bool) -> User:
    if user.role is Role.admin:
        raise InvalidUserData("The admin account cannot be disabled.")
    user.is_active = make_active
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def get_admin(session: Session) -> User | None:
    return session.exec(select(User).where(User.role == Role.admin)).first()


def seed_admin(session: Session, settings: Settings) -> User:
    """Create the single admin on first start. Idempotent."""
    existing = get_admin(session)
    if existing is not None:
        return existing

    username = settings.admin_username.strip().lower()
    if get_by_username(session, username) is not None:
        raise RuntimeError("ADMIN_USERNAME is already used by an existing account.")

    admin = User(
        username=username,
        password_hash=hash_password(settings.admin_password),
        role=Role.admin,
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin
