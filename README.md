# Melsta Hospitals — Smart Queue & Patient Flow Management

## Overview

This Flask application adapts the existing queue platform for hospital outpatient and service queues. Patients can register from a location QR code or through reception, receive a token and estimated wait, follow live status on their phone, and submit feedback after service. Staff call patients from department queues, while managers and administrators review activity and configure services.

The queue stores only patient name and mobile number for identification; it is not an electronic health record and must not be used to store diagnoses or clinical notes. Multi-step visits and HIS/appointment integration are not included in this initial adaptation.

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

System administrators can adjust service names, token prefixes, average service times, active status, and staff access at **Departments & Services**. Estimates are based on the number of people ahead multiplied by the configured average; tune these values using actual queue data.

Priority categories are Emergency, Urgent referral, Elderly, Pregnant, Disabled, and Child. Priority patients are called before regular patients, with FIFO order within each group. Patients experiencing a medical emergency must alert hospital staff immediately rather than wait for a queue token.

## Roles

- **System Administrator** — manage locations, users, departments/services, QR codes, audit logs, and analytics.
- **Department / Operations Manager** — review department queues, staffing, analytics, and location performance.
- **Reception / OPD Staff** — manage the services assigned to the role.
- **Department Staff** — manage the services assigned to the role.
- **Specialist Doctor** — serve specialist consultation queues. An administrator creates the doctor’s account, entering the doctor’s name as the full name.

The existing internal route and database identifiers (`branch`, `customer`, and legacy role names) remain for compatibility; the user-facing interface describes hospital locations, patients, and hospital staff roles.

## Architecture

- Python 3 / Flask, Flask-SQLAlchemy, Alembic migrations, and Gunicorn
- MySQL for production; SQLite for development and tests
- Socket.IO for live waiting-area display updates
- Celery and Redis for asynchronous SMS and scheduled tasks
- Responsive server-rendered pages for patient phones, staff consoles, and display screens
- Docker Compose deployment behind Nginx

The public waiting-area display shows tokens, service names, and counters only; it does not display patient names or the reason for priority.

## Local development

1. Create and activate a virtual environment:

   ```powershell
   python -m venv venv
   .venv\Scripts\activate
   ```

2. Install dependencies and configure environment values:

   ```powershell
   pip install -r requirements.txt
   copy .env.example .env
   ```

3. Initialize or upgrade the database, seed hospital services, and create an administrator:

   ```powershell
   flask db upgrade
   flask seed-db
   flask create-admin
   ```

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

Set a strong `SECRET_KEY`, production `DATABASE_URL`, Redis/Celery URLs, HTTPS, backups, and retention policies before go-live. Configure the hospital's approved SMS provider and sender ID through environment variables.

## Important configuration

- `FLASK_APP` — `wsgi.py`
- `FLASK_ENV` — `development` or `production`
- `SECRET_KEY` — session signing key
- `DATABASE_URL` — SQLAlchemy URI
- `REDIS_URL`, `CELERY_BROKER_URL` — background task infrastructure
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

## Staff workflow

1. Sign in to the role-specific console.
2. Call the next eligible patient, start service, then complete, skip, or mark no-show.
3. Review waiting queues and recent service history.

## Main endpoints

- `POST /api/auth/login`
- `POST /api/queue/register`
- `GET /api/queue/token/<id>/status`
- `POST /api/queue/call-next`
- `POST /api/queue/token/<id>/start`
- `POST /api/queue/token/<id>/complete`
- `POST /api/queue/token/<id>/rate`
- `GET /api/queue/display/<location_code>`
- `GET /api/admin/branches`
- `GET /api/admin/users`
- `GET /api/admin/audit-logs`
- `GET /api/manager/dashboard`

## CLI commands

- `flask seed-db` — initialize schema when needed and seed hospital services
- `flask create-admin` — create the first system administrator
- `flask reset-queue --branch CODE` — clear today's queue tokens for a location
- `flask generate-qr --branch CODE` — generate a location registration QR code
- `flask generate-qr --all` — generate QR codes for all active locations

## Tests

Run the focused queue and configuration tests with:

```powershell
python -m pytest tests/test_seed_db.py tests/test_uat_flows.py
```
