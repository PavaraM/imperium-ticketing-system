from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request
from pwdlib import PasswordHash
from sqlmodel import Session
from starlette.responses import PlainTextResponse

from app.db import get_session
from app.models import Role, User

SESSION_USER_ID = "uid"

_password_hasher = PasswordHash.recommended()
# Verifying against this on unknown usernames keeps response timing flat.
_DUMMY_HASH = _password_hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password, password_hash)
    except Exception:  # noqa: BLE001 - a malformed hash must read as "wrong password"
        return False


def burn_password_check(password: str) -> None:
    _password_hasher.verify(password, _DUMMY_HASH)


def start_session(request: Request, user: User) -> None:
    request.session.clear()
    request.session[SESSION_USER_ID] = user.id


def end_session(request: Request) -> None:
    request.session.clear()


def get_current_user(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> User | None:
    """Resolve the signed cookie to an active user, or ``None``."""
    user_id = request.session.get(SESSION_USER_ID)
    if not isinstance(user_id, int):
        return None
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


CurrentUser = Annotated[User | None, Depends(get_current_user)]


def require_user(user: CurrentUser) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Login required")
    return user


RequiredUser = Annotated[User, Depends(require_user)]


def require_role(*roles: Role):  # noqa: ANN201
    allowed = frozenset(roles)

    def dependency(user: RequiredUser) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail="Not allowed")
        return user

    return dependency


def is_safe_next(target: str | None) -> bool:
    """Only allow same-site, absolute-path redirects."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc and "\\" not in target


async def origin_guard(request: Request, call_next):  # noqa: ANN001, ANN201
    """Reject cross-site state-changing requests as a CSRF belt-and-braces check."""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        host = request.headers.get("host")
        if origin and host and urlparse(origin).netloc != host:
            return PlainTextResponse("Cross-origin request blocked", status_code=403)
    return await call_next(request)
