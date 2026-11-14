import csv
import io
import unicodedata
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import select

from app.deps import AdminDep, SessionDep, UserDep
from app.models import EmailStatus, Role, Ticket, User
from app.services.delivery import deliver_ticket_email
from app.services.qr import render_qr_png
from app.services.tickets import (
    FORM_FIELDS,
    DuplicateBankReference,
    TicketValidationError,
    all_tickets,
    create_ticket,
    get_ticket,
    parse_ticket_input,
    revoke_ticket,
    search_tickets,
)
from app.templating import add_flash, format_local_time, render

router = APIRouter(prefix="/tickets")


def _issued_by_filter(user: User) -> int | None:
    return (user.id or 0) if user.role is Role.ticketer else None


def _accessible_ticket(session: SessionDep, user: User, ticket_id: int) -> Ticket | None:
    ticket = get_ticket(session, ticket_id)
    if ticket is not None and user.role is Role.ticketer and ticket.issued_by != user.id:
        return None
    return ticket


@router.get("", response_class=HTMLResponse)
def list_tickets(
    request: Request,
    user: UserDep,
    session: SessionDep,
    q: str = "",
    email: str = "",
):
    email_filter = {
        "failed": EmailStatus.failed,
        "sent": EmailStatus.sent,
        "pending": EmailStatus.pending,
    }.get(email)
    return render(
        request,
        "tickets_list.html",
        user=user,
        tickets=search_tickets(
            session, q, email_status=email_filter, issued_by=_issued_by_filter(user)
        ),
        q=q,
        email=email_filter.value if email_filter else "",
    )


@router.get("/new", response_class=HTMLResponse)
def new_ticket_form(request: Request, user: UserDep):
    return render(request, "ticket_new.html", user=user, form={}, errors=[])


CSV_COLUMNS = (
    "Ticket ID",
    "Name",
    "Email",
    "WhatsApp",
    "Batch",
    "Amount",
    "Currency",
    "Bank reference",
    "Status",
    "Issued by",
    "Created at",
    "Checked in at",
    "Checked in by",
    "Email status",
)


def _spreadsheet_safe(value: str) -> str:
    first_content = next(
        (
            character
            for character in value
            if not (character.isspace() or unicodedata.category(character) == "Cc")
        ),
        "",
    )
    if first_content in {"=", "+", "-", "@"}:
        return "'" + value
    return value


@router.get("/export.csv")
def export_tickets_csv(request: Request, user: AdminDep, session: SessionDep) -> Response:
    settings = request.app.state.settings
    staff = dict(session.exec(select(User.id, User.username)).all())

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for ticket in all_tickets(session):
        writer.writerow(
            [
                ticket.id,
                _spreadsheet_safe(ticket.name),
                _spreadsheet_safe(ticket.email),
                _spreadsheet_safe(ticket.whatsapp_number),
                _spreadsheet_safe(ticket.batch or ""),
                f"{ticket.amount_paid / 100:.2f}",
                settings.currency,
                _spreadsheet_safe(ticket.bank_reference),
                ticket.status.value,
                staff.get(ticket.issued_by, ""),
                format_local_time(ticket.created_at, settings.event_timezone),
                (
                    format_local_time(ticket.checked_in_at, settings.event_timezone)
                    if ticket.checked_in_at
                    else ""
                ),
                staff.get(ticket.checked_in_by or 0, ""),
                ticket.email_status.value,
            ]
        )

    today = datetime.now(UTC).date().isoformat()
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="tickets-{today}.csv"',
            "Cache-Control": "private, no-store",
        },
    )


@router.post("", response_class=HTMLResponse)
async def create_ticket_view(
    request: Request,
    user: UserDep,
    session: SessionDep,
    background: BackgroundTasks,
):
    raw = await request.form()
    form = {field: str(raw.get(field, "")).strip() for field in FORM_FIELDS}

    try:
        parsed = parse_ticket_input(form)
        ticket = create_ticket(session, parsed, issued_by=user.id)
    except TicketValidationError as exc:
        return render(
            request,
            "ticket_new.html",
            user=user,
            form=form,
            errors=exc.messages,
            status_code=400,
        )
    except DuplicateBankReference:
        return render(
            request,
            "ticket_new.html",
            user=user,
            form=form,
            errors=["That bank reference is already attached to another ticket."],
            status_code=400,
        )

    background.add_task(
        deliver_ticket_email, request.app.state.engine, request.app.state.mailer, ticket.id
    )
    add_flash(request, f"Ticket for {ticket.name} created. Email queued.")
    return RedirectResponse(f"/tickets/{ticket.id}", status_code=303)


@router.get("/{ticket_id}", response_class=HTMLResponse)
def ticket_detail(ticket_id: int, request: Request, user: UserDep, session: SessionDep):
    ticket = _accessible_ticket(session, user, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return render(
        request,
        "ticket_detail.html",
        user=user,
        ticket=ticket,
        issuer=session.get(User, ticket.issued_by),
        checker=session.get(User, ticket.checked_in_by) if ticket.checked_in_by else None,
    )


@router.get("/{ticket_id}/qr.png")
def ticket_qr(ticket_id: int, user: UserDep, session: SessionDep):
    ticket = _accessible_ticket(session, user, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return Response(
        content=render_qr_png(ticket.token),
        media_type="image/png",
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/{ticket_id}/resend", response_class=HTMLResponse)
def resend_ticket_email(
    ticket_id: int,
    request: Request,
    user: UserDep,
    session: SessionDep,
    background: BackgroundTasks,
):
    ticket = _accessible_ticket(session, user, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ticket.email_status = EmailStatus.pending
    session.add(ticket)
    session.commit()

    background.add_task(
        deliver_ticket_email, request.app.state.engine, request.app.state.mailer, ticket.id
    )
    add_flash(request, "Email queued. The same ticket QR will be sent again.")
    return RedirectResponse(f"/tickets/{ticket.id}", status_code=303)


@router.post("/{ticket_id}/revoke", response_class=HTMLResponse)
def revoke_ticket_view(ticket_id: int, request: Request, user: AdminDep, session: SessionDep):
    outcome = revoke_ticket(session, ticket_id)
    if outcome == "missing":
        raise HTTPException(status_code=404, detail="Ticket not found")

    add_flash(
        request,
        {
            "revoked": "Ticket revoked. That QR will be refused at the door.",
            "already_revoked": "That ticket was already revoked.",
            "checked_in": "That ticket has already been admitted, so it can’t be revoked.",
        }[outcome],
    )
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)
