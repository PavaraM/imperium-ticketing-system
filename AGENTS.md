# AGENTS.md - QR Party Ticketing

Web app that issues and checks in QR tickets for a school party (~200 guests). Full spec: see `PARTY_TICKETING_PLAN.md`.

## Stack
Python 3.12, FastAPI, SQLModel + SQLite (WAL), Jinja2 + vanilla JS, `qrcode`, `html5-qrcode`, pytest, ruff. Mobile-first UI.

## How to work (IMPORTANT)
- Work on **ONE build step at a time** (see build order in the plan). When a step is done, run the tests, summarise what changed in a few lines, then **STOP and wait for my review**. Do not start the next step.
- Before large changes, state your plan in a short list and wait for a go-ahead.
- Keep changes small and focused. No unrelated refactors.
- Use the skills in `skills/` when the task matches: `add-endpoint`, `write-tests`, `security-review`, `db-change`.

## Hands-off files (the project owner writes these)
Never create or edit: `Dockerfile`, `docker-compose*.yml`, `.dockerignore`, `.github/workflows/**`, and any deployment or server config. If something seems to need a change there, **suggest it in chat only**.
`AGENTS.md` and `.github/copilot-instructions.md` may be edited only when I ask.

## Product rules
- No public registration. Tickets exist only after a logged-in admin/ticketer enters a paid buyer.
- Roles: one `admin` (seeded from env vars) and `ticketer` accounts created by the admin. Both can use the scanner.
- The QR contains ONLY the random token (`secrets.token_urlsafe`, 32+ chars). Never put personal data in it.
- Ticket statuses: `valid`, `checked_in`, `revoked`. `bank_reference` is unique.
- Scanner is two-step: `lookup` (read-only, shows name/status) then a "Let in" button that calls `checkin`.
- `checkin` must be ONE atomic `UPDATE ... WHERE token=? AND status='valid'` and must check `rowcount`.
- Email is sent in a background task; failure sets `email_status='failed'` and never blocks ticket creation. "Resend" re-sends the SAME token.

## Security rules
- Never log names, emails or phone numbers.
- Passwords hashed (argon2/bcrypt). Rate-limit login. HttpOnly session cookies.
- All secrets/config via env vars (SMTP, admin credentials, secret key, DB path). Never commit secrets or real data; keep `.env.example` up to date.
- Use only FAKE data in tests, seeds and examples.

## Code conventions
- Type hints everywhere; run `ruff check` and `ruff format` before finishing a step.
- Mailer behind an interface so tests can mock it; console mode for dev.
- Every endpoint has role checks and tests for allowed and forbidden roles.
- Expose `/healthz` returning 200.

## Commands
- Run: `uvicorn app.main:app --reload`
- Test: `pytest -q`
- Lint: `ruff check . && ruff format --check .`
