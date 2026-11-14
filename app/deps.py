"""Shared FastAPI dependencies, so route signatures stay free of default calls."""

from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from app.db import get_session
from app.models import Role, User
from app.security import require_role, require_user

SessionDep = Annotated[Session, Depends(get_session)]
UserDep = Annotated[User, Depends(require_user)]
AdminDep = Annotated[User, Depends(require_role(Role.admin))]
