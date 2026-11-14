from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.deps import SessionDep, UserDep
from app.models import Role
from app.services.stats import recent_admissions, sales_by_staff, ticket_totals
from app.templating import render

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: UserDep, session: SessionDep):
    issued_by = (user.id or 0) if user.role is Role.ticketer else None

    return render(
        request,
        "dashboard.html",
        user=user,
        totals=ticket_totals(session, issued_by=issued_by),
        sales=sales_by_staff(session, issued_by=issued_by),
        admissions=recent_admissions(session, issued_by=issued_by),
    )
