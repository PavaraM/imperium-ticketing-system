# Imperium Ticketing

QR tickets for a school party of about 200 guests. Staff-only: there is no public
registration and no self-service checkout. A ticket exists only after an admin or
ticketer enters a guest who has already paid by bank transfer.

Each ticket gets a QR code that encodes nothing but a random token. At the door,
staff scan it, see the guest's name, and tap **Let in**. The first tap wins.

## Requirements

- Python 3.12 (`uv` can install it for you)
- `uv`
- No database server, no Docker, no Node build step

## Quick start

```bash
git clone <your-repo> && cd imperium-ticketing-system
uv sync

cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste into SECRET_KEY
# then set ADMIN_PASSWORD and the EVENT_* values in .env

uv run python -m scripts.seed        # 50 fake guests + demo staff, dev only
uv run uvicorn app.asgi:app --reload
```

Open <http://localhost:8000> and sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
The seed script also creates `door-1` and `door-2` with the password `demo-pass-123`.

> The module path is `app.asgi:app`, not `app.main:app`. `app.main` only exposes
> `create_app()` so that tests can import it without real environment variables.

## Configuration

Everything comes from environment variables, loaded from `.env` (git-ignored).
`.env.example` is the source of truth and lists every variable.

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | — | **Required.** Signs the session cookie. Long random string. |
| `DB_PATH` | `var/tickets.db` | SQLite file. `-wal` and `-shm` sit next to it. |
| `ADMIN_USERNAME` | — | **Required.** Seeded once, on first start. |
| `ADMIN_PASSWORD` | — | **Required.** Seeded once. Changing it later does not change the account. |
| `SESSION_MAX_AGE` | `43200` | Cookie lifetime in seconds (12 h). |
| `COOKIE_SECURE` | `false` | Set `true` once served over HTTPS. |
| `LOGIN_MAX_ATTEMPTS` | `5` | Failed logins per IP per window before a 429. |
| `LOGIN_WINDOW_SECONDS` | `300` | Rate-limit window. |
| `TRUST_PROXY` | `false` | Trust `X-Forwarded-For`. Only behind your own reverse proxy. |
| `EVENT_NAME` | `School Party 2026` | Shown in the UI and the ticket email. |
| `EVENT_DATE` | — | Free text, e.g. `Sat 12 Dec 2026, 7:00 PM`. |
| `EVENT_VENUE` | — | Free text. |
| `EVENT_TIMEZONE` | `Asia/Colombo` | Timestamps are stored in UTC and displayed in this zone. |
| `CURRENCY` | `LKR` | Prefix for amounts. |
| `BASE_URL` | `http://localhost:8000` | Public URL, used in emails. |
| `MAIL_MODE` | `console` | `console` writes `.eml` files to `var/outbox/`; `smtp` sends for real. |
| `SMTP_HOST` / `SMTP_PORT` | — / `587` | Required when `MAIL_MODE=smtp`. |
| `SMTP_USE_STARTTLS` | `true` | Port 587 usually needs it, 465 uses implicit TLS (not supported here). |
| `SMTP_USER` / `SMTP_PASSWORD` | — | SMTP credentials. Gmail needs an App Password. |
| `SMTP_FROM_NAME` / `SMTP_FROM_EMAIL` | — | Sender shown to guests. |

Missing required values make the app fail at startup with a clear message rather
than falling back to a default password.

## Commands

| Task | Command |
|---|---|
| Run (dev) | `uv run uvicorn app.asgi:app --reload` |
| Run (one worker) | `uv run uvicorn app.asgi:app --host 0.0.0.0 --port 8000` |
| Tests | `uv run pytest` |
| Lint | `uv run ruff check . && uv run ruff format --check .` |
| Seed demo data | `uv run python -m scripts.seed` |
| Reset the database | `uv run python -m scripts.reset_db` |
| Health check | `curl -fsS http://localhost:8000/healthz` |

`uv run pytest` already runs quietly; adding `-q` makes it `-qq` and hides the
pass/fail summary. Seed and reset are documented in [Data](#data-backups-and-reset).

## How it works

### Roles

| Role | Can do |
|---|---|
| `admin` (exactly one) | Everything: issue tickets, scan, revoke, export CSV, dashboard, manage ticketers |
| `ticketer` | Issue tickets, search them, resend email, scan |

There is no way to create a second admin in the app, and the database enforces it
with a partial unique index on `role = 'admin'`. The admin account cannot be
disabled, so there is no lockout path.

Ticketers are created, disabled and re-enabled from **Staff**. Disabling takes
effect on the next request, including sessions that are already signed in.

The admin can also reset the database from **Staff**. Choose to clear tickets
only (keeping all staff) or to clear tickets and ticketers (keeping the current
admin account and password). The panel always creates a SQLite backup first and
requires typing `RESET` before it deletes anything.

### Ticket lifecycle

```
valid ──(Let in, atomic)──> checked_in
  │
  └──(admin revoke)──> revoked
```

- `valid` — admitted at the door
- `checked_in` — has `checked_in_at` and `checked_in_by`; cannot be revoked, because
  the admission already happened
- `revoked` — the QR is refused at the door, but the row stays for the audit trail
  and keeps its bank reference reserved

Tickets are never deleted from the UI. Deleting would break the link between a
guest and their bank transfer, and a second ticketer could then issue a duplicate
ticket for the same payment.

### Issuing a ticket

1. The ticketer submits guest name, email, WhatsApp number, optional batch, amount
   paid and the bank reference.
2. Input is validated (email format, 9–15 digit phone, amount, unique reference).
   Bank references are upper-cased, so `slip-1` and `SLIP-1` collide on purpose.
3. The ticket is saved as `valid` with `email_status = pending` and a fresh
   32-byte `secrets.token_urlsafe` token.
4. A QR PNG is generated in memory and emailed in a FastAPI background task.
5. `email_status` becomes `sent` or `failed`. A mail failure never loses the
   ticket; **Resend email** re-sends the same token, so an old QR keeps working.

Amounts are stored as integer minor units (cents) to avoid float rounding, and
rendered with `CURRENCY`.

### The scanner

`/scan` opens the rear camera (via the vendored `html5-qrcode`, no CDN needed at
the venue) and works in two steps:

1. **Lookup** (`POST /api/scan/lookup`) is read-only. It shows the guest's name,
   batch and status so staff can compare the name to the person in front of them.
2. **Let in** (`POST /api/scan/checkin`) performs exactly one statement:

```sql
UPDATE tickets
   SET status = 'checked_in', checked_in_at = ?, checked_in_by = ?
 WHERE token = ? AND status = 'valid';
```

The code checks `rowcount == 1`. Two phones scanning the same QR at the same
moment cannot both succeed: the second update matches zero rows and gets a 409
with "Already checked in at HH:MM by …". A test covers this, including the case
where one session still holds a stale `valid` copy of the row.

If a QR won't scan, the manual name search finds the ticket and admits it through
the same atomic path. The search response never contains a token.

### Email

`MAIL_MODE=console` (default) writes one `.eml` per ticket to `var/outbox/`. Open
it in any mail client to check the HTML, the inline QR and the attachment; no
mail leaves the machine. The log line contains the file path and ticket id only.

For real mail, set `MAIL_MODE=smtp` and fill in the `SMTP_*` variables. With Gmail:
enable 2-Step Verification, create an App Password, use `smtp.gmail.com:587`, and
set `SMTP_FROM_EMAIL` to the same address as `SMTP_USER`. Send yourself one test
ticket and check the spam folder before sales open.

## Camera needs HTTPS

Browsers only expose the camera in a secure context. `http://localhost` counts as
secure; `http://192.168.x.x` on a phone does not. For real phones, serve the app
over HTTPS (Caddy, nginx + certbot, or a Cloudflare Tunnel), then set
`COOKIE_SECURE=true`. If a proxy terminates TLS, also set `TRUST_PROXY=true` so
the login rate limiter sees real client IPs.

The scanner degrades gracefully: if the camera is blocked or unavailable, it says
so and the manual search still works. That is also the fallback when a guest's
screen is cracked or the venue wifi is bad.

## Data, backups and reset

SQLite in WAL mode (`DB_PATH`, plus `-wal` and `-shm`). One writer at a time is
plenty for 200 guests, but run a **single** uvicorn worker: the login rate limiter
is in process memory.

Back up with SQLite's own API, not `cp`, so you never copy a half-written WAL:

```bash
mkdir -p var/backups
sqlite3 var/tickets.db ".backup 'var/backups/tickets-$(date -u +%Y%m%dT%H%M%SZ).db'"
```

If the `sqlite3` CLI is missing:

```bash
uv run python -c "
import sqlite3, pathlib
pathlib.Path('var/backups').mkdir(parents=True, exist_ok=True)
src = sqlite3.connect('var/tickets.db')
dst = sqlite3.connect('var/backups/tickets-copy.db')
src.backup(dst)
"
```

To restore: stop the server, move `tickets.db`, `tickets.db-wal` and
`tickets.db-shm` out of the way, copy the backup into `DB_PATH`, start the server.

To wipe data between rehearsals:

```bash
# stop the server first
uv run python -m scripts.reset_db                 # asks you to type "reset"
uv run python -m scripts.reset_db --yes           # full wipe, admin re-seeded
uv run python -m scripts.reset_db --tickets-only --yes   # keep staff accounts
uv run python -m scripts.reset_db --no-backup --yes      # skip the snapshot
```

The same reset scopes are available to the admin from **Staff** in the app; the
panel always creates a backup and does not require stopping the server.

It refuses to run while a process has the database open (it names the PID), takes
a `.backup` snapshot into `var/backups/` before deleting anything, and re-seeds the
admin from `ADMIN_USERNAME` / `ADMIN_PASSWORD`. `var/` is git-ignored, so backups
never reach the repo.

## Tests

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

93 tests, all against a temporary SQLite file and fake guests (`@example.com`,
`077…` numbers). The mailer is replaced with an in-memory fake, so no test can
send real email. Covered: login, rate limiting, disabled accounts, role
permissions on every route, ticket validation, duplicate bank references, QR
contents, email failure not blocking creation, resend reusing the token, all four
scanner lookup states, atomic double check-in, revoked tickets refused, dashboard
totals, CSV export, database reset and seed idempotency.

## Project layout

```
app/
  main.py            app factory, lifespan, /healthz, error pages
  asgi.py            uvicorn entry point
  config.py          env settings (pydantic-settings)
  db.py              engine + SQLite PRAGMAs (WAL, busy timeout, FKs)
  models.py          User, Ticket
  security.py        argon2, session cookie, role dependencies, Origin guard
  ratelimit.py       in-memory login throttle
  routers/           auth, pages, tickets, scan, admin
  services/          tickets, scan, mailer, qr, delivery, stats, users, reset, seed
  templates/         Jinja2, mobile-first
  static/            CSS, scan.js, vendored html5-qrcode
scripts/             seed.py, reset_db.py
tests/               pytest suite
```

## Security notes

- Passwords are hashed with argon2 (via `pwdlib`). Unknown usernames still cost a
  hash verification, so response timing does not reveal valid accounts.
- Sessions are signed cookies: `HttpOnly`, `SameSite=Lax`, `Secure` when
  `COOKIE_SECURE=true`. State-changing requests also require a matching `Origin`.
- Login is rate-limited per IP + username hash. Raw usernames and IPs are not kept
  in the limiter.
- The QR holds only the token. Tokens never appear in list pages, API responses,
  CSV exports or the DOM of the scanner; the QR image endpoint requires a login.
- Names, emails and phone numbers are never logged. Delivery logs carry the ticket
  id only.
- CSV export contains guest data by design (it is the paper backup). Treat the file
  as personal data, and never commit it.
- A QR screenshot can be shared. Only the first scan is admitted and the scanner
  shows the guest's name, so staff can compare it to the person presenting it.

Not in this repo, by design: `Dockerfile`, CI workflows and deployment config.

## Known limits

- No ticket editing. Revoke and issue a new one; the old reference stays reserved.
- The rate limiter lives in process memory, so it resets on restart and assumes one
  worker.
- The ticket list is capped at 500 rows (no pagination). CSV export is unbounded.
- Schema is created with `create_all`; there is no migration tool. Adding columns is
  fine for a single event, and `scripts/reset_db.py` is the escape hatch.
- Email is sent in a background task after the response, so a hard crash between
  the two can leave `email_status = pending`. Use Resend in that case.
