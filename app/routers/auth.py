from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.deps import SessionDep
from app.ratelimit import login_key
from app.security import (
    CurrentUser,
    end_session,
    is_safe_next,
    start_session,
)
from app.services.users import authenticate
from app.templating import add_flash, render

router = APIRouter()


def _client_ip(request: Request) -> str:
    if request.app.state.settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: CurrentUser):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    next_url = request.query_params.get("next")
    return render(
        request,
        "login.html",
        next_url=next_url if is_safe_next(next_url) else "",
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    session: SessionDep,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next_url: Annotated[str, Form(alias="next")] = "",
):
    limiter = request.app.state.limiter
    key = login_key(_client_ip(request), username)

    limit = limiter.check(key)
    if not limit.allowed:
        return render(
            request,
            "login.html",
            status_code=429,
            error=f"Too many attempts. Try again in {limit.retry_after} seconds.",
            username=username,
            next_url=next_url if is_safe_next(next_url) else "",
        )

    user = authenticate(session, username, password)
    if user is None:
        limiter.record_failure(key)
        return render(
            request,
            "login.html",
            status_code=401,
            error="Wrong username or password.",
            username=username,
            next_url=next_url if is_safe_next(next_url) else "",
        )

    limiter.reset(key)
    start_session(request, user)
    return RedirectResponse(next_url if is_safe_next(next_url) else "/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    end_session(request)
    add_flash(request, "Signed out.")
    return RedirectResponse("/login", status_code=303)
