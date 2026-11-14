from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlmodel import Session, SQLModel
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings, get_settings
from app.db import create_db_engine
from app.ratelimit import LoginRateLimiter
from app.routers import admin, auth, pages, scan, tickets
from app.security import origin_guard
from app.services.mailer import get_mailer
from app.services.users import seed_admin
from app.templating import STATIC_DIR, render

SESSION_COOKIE = "imperium_session"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = create_db_engine(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ARG001
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            seed_admin(session, settings)
        yield

    app = FastAPI(
        title="Imperium Ticketing",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.limiter = LoginRateLimiter(settings.login_max_attempts, settings.login_window_seconds)
    app.state.mailer = get_mailer(settings)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=SESSION_COOKIE,
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    app.middleware("http")(origin_guard)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(auth.router)
    app.include_router(tickets.router)
    app.include_router(scan.router)
    app.include_router(admin.router)
    app.include_router(pages.router)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        wants_json = request.url.path.startswith("/api/") or "application/json" in (
            request.headers.get("accept") or ""
        )
        if wants_json:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

        if exc.status_code == 401:
            next_path = request.url.path
            if request.url.query:
                next_path = f"{next_path}?{request.url.query}"
            from urllib.parse import quote

            return RedirectResponse(f"/login?next={quote(next_path)}", status_code=303)

        return render(
            request,
            "error.html",
            status_code=exc.status_code,
            title=f"{exc.status_code}",
            message=exc.detail,
        )

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> RedirectResponse:
        return RedirectResponse("/static/favicon.svg", status_code=307)

    @app.get("/healthz")
    def healthz():
        with Session(engine) as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app
