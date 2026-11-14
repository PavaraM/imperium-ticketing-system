from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.deps import SessionDep, UserDep
from app.models import Role
from app.services.stats import recent_admissions, sales_by_staff, ticket_totals
from app.templating import render

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: UserDep, session: SessionDep):
    if user.role is not Role.admin:
        return RedirectResponse("/tickets", status_code=303)

    return render(
        request,
        "dashboard.html",
        user=user,
        totals=ticket_totals(session),
        sales=sales_by_staff(session),
        admissions=recent_admissions(session),
    )
