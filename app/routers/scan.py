"""Scanner page and the two-step JSON API used by it."""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from app.deps import SessionDep, UserDep
from app.services.scan import ScanTicket, check_in, resolve, search_by_name
from app.templating import format_local_clock, render

router = APIRouter(tags=["scanner"])


class ScanRequest(BaseModel):
    """Either a scanned token or a ticket picked from manual search."""

    token: str | None = Field(default=None, max_length=256)
    ticket_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def exactly_one_identifier(self) -> "ScanRequest":
        if (self.token is None) == (self.ticket_id is None):
            raise ValueError("Provide exactly one of token or ticket_id.")
        return self


def _payload(
    snapshot: ScanTicket | None, tz_name: str, *, just_checked_in: bool = False
) -> dict[str, object]:
    if snapshot is None:
        return {"state": "unknown"}
    payload: dict[str, object] = {
        "state": snapshot.status.value,
        "ticket_id": snapshot.ticket_id,
        "name": snapshot.name,
        "batch": snapshot.batch,
        "checked_in_at": (
            format_local_clock(snapshot.checked_in_at, tz_name) if snapshot.checked_in_at else None
        ),
        "checked_in_by": snapshot.checked_in_by,
    }
    if just_checked_in:
        payload["just_checked_in"] = True
    return payload


@router.get("/scan")
def scan_page(request: Request, user: UserDep):
    return render(request, "scan.html", user=user)


@router.post("/api/scan/lookup")
def scan_lookup(
    body: ScanRequest,
    request: Request,
    user: UserDep,
    session: SessionDep,
):
    """Read-only. Shows the guest and state without admitting anyone."""
    snapshot = resolve(session, token=body.token or "", ticket_id=body.ticket_id)
    return _payload(snapshot, request.app.state.settings.event_timezone)


@router.post("/api/scan/checkin")
def scan_checkin(
    body: ScanRequest,
    request: Request,
    user: UserDep,
    session: SessionDep,
):
    """Atomic admit. Refuses anything that is not `valid` right now."""
    snapshot, admitted = check_in(session, user, token=body.token or "", ticket_id=body.ticket_id)
    tz_name = request.app.state.settings.event_timezone
    if snapshot is None:
        return JSONResponse({"state": "unknown"}, status_code=404)
    if admitted:
        return JSONResponse(_payload(snapshot, tz_name, just_checked_in=True))
    return JSONResponse(_payload(snapshot, tz_name), status_code=409)


@router.get("/api/scan/search")
def scan_search(
    request: Request,
    user: UserDep,
    session: SessionDep,
    q: Annotated[str, Query(max_length=120)] = "",
):
    """Name search fallback. Results carry no tokens."""
    tz_name = request.app.state.settings.event_timezone
    return [_payload(item, tz_name) for item in search_by_name(session, q)]
