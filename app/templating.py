from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def format_money(cents: int | None) -> str:
    """Render integer minor units as a human amount, e.g. 150050 -> '1,500.50'."""
    return f"{(cents or 0) / 100:,.2f}"


def format_local_time(value: datetime | None, tz_name: str) -> str:
    """Render a stored UTC timestamp in the event timezone."""
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(tz_name)).strftime("%d %b %Y, %H:%M")


def format_local_clock(value: datetime | None, tz_name: str) -> str:
    """Render just the wall-clock time, e.g. '20:14'."""
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(tz_name)).strftime("%H:%M")


templates.env.filters["money"] = format_money
templates.env.filters["local_time"] = format_local_time
templates.env.filters["local_clock"] = format_local_clock


def add_flash(request: Request, message: str) -> None:
    request.session["flash"] = message


def pop_flash(request: Request) -> str | None:
    return request.session.pop("flash", None)


def render(
    request: Request,
    template_name: str,
    *,
    user=None,  # noqa: ANN001
    status_code: int = 200,
    **context: object,
):
    settings = request.app.state.settings
    context.update(
        {
            "user": user,
            "flash": pop_flash(request),
            "event_name": settings.event_name,
            "event_date": settings.event_date,
            "event_venue": settings.event_venue,
            "currency": settings.currency,
            "event_tz": settings.event_timezone,
        }
    )
    return templates.TemplateResponse(request, template_name, context, status_code=status_code)
