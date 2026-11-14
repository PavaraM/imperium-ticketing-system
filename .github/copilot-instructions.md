# Copilot instructions for Imperium Ticketing

## Project overview
This repo is a FastAPI app for issuing and checking in QR tickets for a school party. It is intentionally small and self-contained: a single Python app, SQLite (WAL mode), vanilla HTML/JS templates, and no Docker or external database service.

The code splits cleanly by responsibility:
- `app/main.py` wires the FastAPI app, middleware, session handling, routers, and the `/healthz` endpoint.
- `app/models.py` defines the SQLModel schema: `User` and `Ticket`, including the “exactly one admin” invariant and the ticket lifecycle (`valid`, `checked_in`, `revoked`).
- `app/db.py` configures SQLite WAL mode and request-scoped sessions; `Session` objects are created per request and not shared.
- `app/routers/` contains HTTP endpoints. Keep routes thin and delegate business logic to `app/services/`.
- `app/services/` holds the real rules: ticket validation, scan logic, QR generation, email delivery, and seed/reset helpers.
- `app/config.py` loads runtime configuration from `.env`; `.env.example` is the source of truth.
- `scripts/seed.py` and `scripts/reset_db.py` are support tools for demo data and database resets, not application code.

## Required setup
- `uv sync`
- Copy `.env.example` to `.env` and fill in required values before running the app.
- Required env values are enforced at startup (`SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`); missing required values fail clearly instead of silently falling back.
- Dev data: `uv run python -m scripts.seed`
- Run the app: `uv run uvicorn app.asgi:app --reload`

## Build, test, and lint commands
Use the repo’s configured toolchain via `uv`:

```bash
# Run the app
uv run uvicorn app.asgi:app --reload

# Run the full test suite
uv run pytest

# Run a single test or a specific test file
uv run pytest tests/test_scan.py -q
uv run pytest tests/test_scan.py::test_checkin_rejects_duplicate_valid_ticket -q

# Lint and formatting checks
uv run ruff check . && uv run ruff format --check .
```

`uv run pytest` already uses `-q` via `pyproject.toml`; keep single-target invocations narrow when iterating.

## High-level business rules and architecture
Treat the app as a role-based ticketing system, not a generic CRUD app:
- There is no public registration flow. A ticket only exists after a logged-in admin or ticketer creates it from a paid guest.
- Role model: exactly one `admin`, plus `ticketer` accounts created by the admin.
- QR payloads are intentionally minimal: they contain only a random token (`secrets.token_urlsafe`), never guest names, phone numbers, or personal data.
- Two-step scanner flow is required:
  1. `POST /api/scan/lookup` is read-only and reveals guest info for confirmation.
  2. `POST /api/scan/checkin` performs an atomic check-in using `UPDATE tickets SET status = 'checked_in' ... WHERE token = ? AND status = 'valid'` and verifies `rowcount == 1`.
- Ticket validity is stateful: tickets can be `valid`, `checked_in`, or `revoked`. A checked-in ticket is never revoked; a revoked ticket stays in the database for audit trail purposes.
- Email is backgrounded and non-blocking. Failed deliveries set `email_status = failed` without losing the ticket; resend emails re-use the same token.
- Money is stored as integer minor units (`amount_paid` in cents) to avoid floating-point issues.

## Key conventions
- Prefer the existing dependency aliases from `app/deps.py`: `SessionDep`, `UserDep`, and `AdminDep`.
- Keep route files thin; do not put validation or business logic directly in `app/routers/*`.
- Put domain rules and DB operations in `app/services/*` and keep HTTP concerns in the router layer.
- `app/config.py` is the configuration source of truth; read all runtime settings from environment variables and `.env`.
- Use SQLModel + `Session` patterns already in the repo, and prefer `select(...)`/`session.exec(...)` over ad hoc SQL.
- Respect the app’s security posture: no secrets in source, no logging of names/emails/phones, and no public self-serve flow.
- For tickets and admissions, validate input early and preserve the project-specific constraints already implemented in `app/services/tickets.py` (email format, phone length, bank reference uniqueness, amount validation, uppercase normalization for bank refs).
- Use the existing tests in `tests/` as the behavioral contract; fixtures and helper patterns are already established there.

## When making changes
- Keep edits focused and minimal; do not broaden scope just because a nearby file is related.
- If a change affects the scanner, ticket lifecycle, or security-sensitive flows, preserve the atomic check-in semantics and the existing role checks.
- When touching config or env-driven behavior, update `.env.example` if the configuration contract changes.
- Use `pytest` to validate changed behavior, especially for ticketing, admin auth, and scanner flows.

## Relevant commands for reset / demo data
```bash
# Seed demo staff and guest data for local development
uv run python -m scripts.seed

# Reset the DB (requires confirmation unless --yes is passed)
uv run python -m scripts.reset_db --yes
```
