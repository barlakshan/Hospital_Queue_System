# Melsta Hospitals — Smart Queue Management

## Overview

This Flask application adapts the existing queue platform for hospital outpatient and service queues. Patients can register from a location QR code or through reception, receive a token and estimated wait, follow live status on their phone, and submit feedback after service. Staff call patients from department queues, while managers and administrators review activity and configure services.

The queue stores the patient's name and mobile number, plus short condition summaries that staff record per visit. It is not an electronic health record. Agree with your data-protection officer which roles may see condition summaries and how long patient records are kept (see `flask purge-patients`). Multi-step visits and HIS/appointment integration are not included.

## Hospital services

`flask seed-db` inserts these defaults when missing; it preserves service settings changed later by an administrator:

| Service | Prefix | Initial average | Default staff access |
|---|---:|---:|---|
| General OPD | G | 10 min | Reception / OPD staff |
| Specialist Consultation | S | 15 min | Reception / OPD staff, department staff |
| Laboratory | L | 8 min | Department staff |
| Radiology | R | 12 min | Department staff |
| Pharmacy | P | 6 min | Reception / OPD staff, department staff |
| Billing / Cashier | B | 5 min | Reception / OPD staff, department staff |

System administrators add services at **Departments & Services**. Queue codes, token prefixes, and wait-time defaults are managed by the system. Active services appear automatically on patient ticket and appointment forms and can be assigned to staff in **User Management**.

Priority categories are Emergency, Urgent referral, Elderly, Pregnant, Disabled, and Child. On the public form a patient can only *request* priority; staff confirm or decline it from the queue (set `ALLOW_SELF_DECLARED_PRIORITY=true` to skip confirmation). Confirmed priority patients are called before regular patients, with FIFO order within each group. Patients experiencing a medical emergency must alert hospital staff immediately rather than wait for a queue token.

Each mobile number can hold one open token per service and at most `MAX_TOKENS_PER_MOBILE_PER_DAY` (default 3) tokens a day. Registering again for the same service returns the existing token.

## Roles

- **Admin** — manage locations, users, departments/services, QR codes, audit logs, and analytics.
- **OPD** and **Pharmacy** — work the services assigned to each role.
- **Dentist**, **Cardiologists**, and **Dermatologists** — work their assigned service queues and see appointment requests for those services.
- **Location Manager** — operations dashboard, analytics, patients and OPD/Pharmacy staff for one location. Admins can open the same pages for any location from **Hospital Locations → Operations**.

The existing internal route and database identifiers (`branch`, `customer`, and legacy role names) remain for compatibility; the user-facing interface describes hospital locations, patients, and hospital staff roles.

Admins create staff accounts with one of the roles above; every role except Admin must belong to a location. Accounts whose password was set by an admin or manager must choose their own password at first sign-in. Patient appointment requests are listed on the Admin dashboard and only on staff dashboards assigned to the selected service, scoped to the staff member's location when assigned.

## Architecture

- Python 3 / Flask, Flask-SQLAlchemy, Alembic migrations, and Gunicorn
- MySQL for production; SQLite for development and tests
- Socket.IO for live waiting-area display updates
- SMS sent from a background thread; `flask retry-sms` and `flask close-day` run from cron (EC2) or the `scheduler` container (Docker)
- Responsive server-rendered pages for patient phones, staff consoles, and display screens
- Gunicorn with **one worker and many threads** (required by Socket.IO without a message queue), behind Nginx
- All times are stored in UTC and shown in `APP_TIMEZONE` (default Asia/Colombo); the queue day also follows that zone

The public waiting-area display shows tokens, service names, and counters only; it does not display patient names or the reason for priority.

## Local development

1. Create and activate a virtual environment:

   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   ```

2. Install dependencies and configure environment values:

   ```powershell
   pip install -r requirements-dev.txt
   copy .env.example .env
   ```

3. Initialize or upgrade the database, seed hospital services, and create an administrator:

   ```powershell
   flask db upgrade
   flask seed-db
   flask create-admin
   ```

   To deliberately remove all records except Admin accounts (including hospital locations and services), run `flask clean-data --confirm`. This command is never run automatically. Re-create locations and run `flask seed-db` afterward.

   For an existing database created by the original app without an Alembic version, stamp its existing initial schema once before applying the hospital migration:

   ```powershell
   flask db stamp 001_initial
   flask db upgrade
   flask seed-db
   ```

4. Start the server:

   ```powershell
   flask run
   ```

## Docker deployment

```powershell
copy .env.example .env
docker compose build
docker compose up -d
docker compose exec web flask db upgrade
docker compose exec web flask seed-db
docker compose exec web flask create-admin
```

Set a strong `SECRET_KEY`, `BASE_URL`, `DB_PASSWORD`/`DB_ROOT_PASSWORD`, HTTPS, backups, and retention policies before go-live. Configure the hospital's approved SMS provider and a sender ID registered with it. The app refuses to start in production without `SECRET_KEY` and `DATABASE_URL`.

## Important configuration

- `FLASK_APP` — `wsgi.py`
- `APP_ENV` — `development` or `production` (`FLASK_ENV` is still accepted)
- `SECRET_KEY` — session signing key (32+ random characters; required in production)
- `DATABASE_URL` — SQLAlchemy URI (required in production)
- `APP_TIMEZONE` — hospital time zone, default `Asia/Colombo`
- `TRUSTED_PROXY_COUNT` — reverse proxies whose `X-Forwarded-*` headers are trusted (production default 1)
- `ALLOW_SELF_DECLARED_PRIORITY`, `MAX_TOKENS_PER_MOBILE_PER_DAY` — public registration rules
- `SMS_PROVIDER` — `mock`, `dialog`, `mobitel`, `textlk`, or `twilio`
- Provider credentials and sender IDs — see `.env.example`
- `BASE_URL` — public base URL embedded in QR registration pages; when unset, QR codes use the host from the page that generates them

## Patient journey

1. Scan the QR code for a hospital location or register at reception.
2. Enter name and mobile number, select a department/service, and indicate a priority category if applicable.
3. Receive a token, queue position, and estimated wait; the status page refreshes while waiting.
4. Receive an SMS when staff call the token and proceed to the assigned room or counter.
5. Staff start and complete the service; the system records queue and service timing.
6. Submit a service rating from the patient status page.

Patients can also open `/appointments/` to request an appointment for an active department or service. Requests start as *pending*; Admins, the location manager and staff assigned to the service can mark them *scheduled*, *rejected* or *done*. Dashboards list open (pending and scheduled) requests.

## Staff workflow

1. Sign in to the role-specific console.
2. Call the next eligible patient, **Start consultation**, then complete or mark no-show. A staff member can't call another patient while one is still open; reloading the console resumes the open patient.
3. Review waiting queues and recent service history.

## Main endpoints

- `POST /api/auth/login`
- `POST /api/queue/register`
- `POST /api/queue/appointments`
- `GET /api/queue/token/<token_ref>/status` (public; `token_ref` is the unguessable reference returned at registration)
- `POST /api/queue/call-next`
- `POST /api/queue/token/<id>/start`
- `POST /api/queue/token/<id>/complete`
- `POST /api/queue/token/<token_ref>/rate`
- `POST /api/queue/token/<id>/priority` (staff: confirm with a reason, or `NONE` to decline)
- `GET /api/queue/current` (staff: the patient you are serving)
- `POST /api/queue/appointments/<id>/status`
- `GET /healthz` (database health check)
- `GET /api/queue/display/<location_code>`
- `GET /api/admin/branches`
- `GET /api/admin/users`
- `GET /api/admin/audit-logs`
- `GET /api/manager/dashboard`

## CLI commands

- `flask seed-db` — initialize schema when needed and seed hospital services
- `flask create-admin` — create the first system administrator
- `flask reset-queue --branch CODE --confirm` — delete today's queue tokens for a location (testing only)
- `flask close-day` — mark earlier days' waiting tokens skipped and open ones no-show (run daily after midnight)
- `flask retry-sms` — re-send SMS that failed in the last 10 minutes (run every few minutes)
- `flask purge-patients --older-than DAYS [--confirm]` — delete patients with no visit in DAYS days
- `flask clean-data --confirm` — delete everything except Admin accounts
- `flask generate-qr --branch CODE` — generate a location registration QR code
- `flask generate-qr --all` — generate QR codes for all active locations

## Tests

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

Tests always use the mock SMS provider and a temporary database; they never touch `.env` credentials or `pbqms_dev.sqlite3`. CI also runs `pip-audit` and applies every migration to an empty MySQL 8 database.
