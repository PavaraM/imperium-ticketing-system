from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app.deps import AdminDep, SessionDep
from app.models import User
from app.services.reset import ResetRefused, reset_database_in_app
from app.services.users import (
    InvalidUserData,
    UsernameTaken,
    create_ticketer,
    normalize_username,
    set_active,
    validate_password,
)
from app.templating import add_flash, render

router = APIRouter(prefix="/admin")


@router.get("/users", response_class=HTMLResponse)
def list_users(request: Request, user: AdminDep, session: SessionDep):
    users = session.exec(select(User).order_by(User.created_at)).all()
    return render(request, "users.html", user=user, users=users)


@router.post("/users", response_class=HTMLResponse)
def create_user(
    request: Request,
    user: AdminDep,
    session: SessionDep,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
):
    try:
        normalize_username(username)
        validate_password(password)
        created = create_ticketer(session, username, password)
    except (UsernameTaken, InvalidUserData) as exc:
        return _form_error(request, user, session, str(exc), username)

    add_flash(request, f"Ticketer “{created.username}” created.")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/toggle")
def toggle_user(
    user_id: int,
    request: Request,
    user: AdminDep,
    session: SessionDep,
):
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        set_active(session, target, not target.is_active)
    except InvalidUserData as exc:
        add_flash(request, str(exc))
        return RedirectResponse("/admin/users", status_code=303)

    state = "re-enabled" if target.is_active else "disabled"
    add_flash(request, f"Ticketer “{target.username}” {state}.")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/reset")
def reset_database_view(
    request: Request,
    user: AdminDep,
    session: SessionDep,
    scope: Annotated[str, Form()] = "",
    confirmation: Annotated[str, Form()] = "",
):
    if confirmation != "RESET":
        add_flash(request, "Database reset cancelled. Type RESET to confirm.")
        return RedirectResponse("/admin/users", status_code=303)
    if scope not in {"tickets-only", "full"}:
        add_flash(request, "Database reset cancelled. Choose a valid reset scope.")
        return RedirectResponse("/admin/users", status_code=303)

    keep_users = scope == "tickets-only"
    try:
        summary = reset_database_in_app(
            session,
            request.app.state.settings,
            keep_users=keep_users,
            admin_username=user.username,
        )
    except ResetRefused as exc:
        add_flash(request, str(exc))
        return RedirectResponse("/admin/users", status_code=303)

    scope_label = "tickets only" if keep_users else "tickets and ticketers"
    add_flash(
        request,
        f"Reset complete ({scope_label}): {summary.tickets_deleted} tickets and "
        f"{summary.users_deleted} ticketers removed. Backup: {summary.backup}",
    )
    return RedirectResponse("/admin/users", status_code=303)


def _form_error(
    request: Request,
    user: User,
    session: Session,
    message: str,
    username: str,
) -> HTMLResponse:
    users = session.exec(select(User).order_by(User.created_at)).all()
    return render(
        request,
        "users.html",
        user=user,
        users=users,
        error=message,
        form_username=username,
        status_code=400,
    )
