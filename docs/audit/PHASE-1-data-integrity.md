# Phase 1 — Data integrity

These findings produce wrong, lost or inconsistent data. Fix them before adding features, because every day they run, more bad data accumulates.

---

## P1-1 · Phone normalisation corrupts common inputs
**Severity:** High · **Status:** Confirmed (reproduced)

**Where:** [app/services/sms_service.py:295](../../app/services/sms_service.py#L295) `SmsService.normalise_mobile`

**Problem:** the function only handles a leading `0` or `+`. Anything else just gets a `+` prepended. Actual output:

| Input | Output | Correct |
|---|---|---|
| `0771234567` | `+94771234567` | ✅ |
| `771234567` | `+771234567` | ❌ Russian/Kazakh number |
| `0094771234567` | `+94094771234567` | ❌ invalid |
| `94771234567` | `+94771234567` | ✅ |

**Impact:**
- SMS goes to the wrong country or fails, so the patient never hears they've been called.
- `customers.mobile` is the **unique patient key**, so the same person ends up as two different patients (`+94771234567` and `+771234567`).
- Visit history is split.

**Fix:**
```python
import re

@staticmethod
def normalise_mobile(mobile: str) -> str:
    """Return E.164 (+94XXXXXXXXX for Sri Lanka) or raise ValueError."""
    m = re.sub(r"[\s\-()]", "", mobile or "")
    if m.startswith("00"):
        m = "+" + m[2:]
    if m.startswith("+"):
        digits = m[1:]
    elif m.startswith("94") and len(m) == 11:
        digits = m
    elif m.startswith("0") and len(m) == 10:
        digits = "94" + m[1:]
    elif len(m) == 9 and m[0] == "7":          # local mobile without trunk 0
        digits = "94" + m
    else:
        raise ValueError("Unrecognised phone number")
    if not re.fullmatch(r"[1-9][0-9]{8,14}", digits):
        raise ValueError("Unrecognised phone number")
    return "+" + digits
```
Use the same function for appointment requests too (see P1-14).

**Data repair:**
```sql
SELECT id, mobile FROM customers WHERE mobile NOT LIKE '+94%';
```
Review each row by hand and merge duplicates. Re-point `queue_tokens.customer_id` to the surviving record before deleting.

**Verify:** add a parametrised unit test with the table above.

---

## P1-2 · Public registration overwrites an existing patient's name
**Severity:** High · **Status:** Confirmed (code)

**Where:** [app/services/queue_service.py:124](../../app/services/queue_service.py#L124)

**Problem:**
- `register_customer` looks up the patient by mobile.
- If one exists and the submitted name differs, it **overwrites `customer.full_name`**.
- The endpoint is public and unauthenticated.

**Impact:**
- Anyone can rename any patient record if they know (or guess) the phone number.
- A family member registering with a parent's phone renames the parent.
- The admin customer list, the exports, and the name staff see attached to the patient's medical condition summary all change, with no audit trail.

**Fix:** never mutate the master record from the public form. Store the name given at registration on the visit:
```python
# model: QueueToken
patient_name = db.Column(db.String(120))   # name as given for this visit
# service
customer = Customer.query.filter_by(mobile=mobile).first()
if not customer:
    customer = Customer(full_name=full_name, mobile=mobile)
    db.session.add(customer); db.session.flush()
token = QueueToken(..., patient_name=full_name)
```
- Show `token.patient_name` in the officer queue.
- Let only staff (admin/manager) edit `Customer.full_name`, and audit the change.
- Needs migration `010_token_patient_name`. Backfill it from `customers.full_name`.

---

## P1-3 · Concurrent registrations collide on token numbers
**Severity:** High · **Status:** Confirmed (code); the race window is real on both databases

**Where:** [app/services/queue_service.py:45](../../app/services/queue_service.py#L45) `generate_token_number`

**Problem:** the next number is "last token today + 1". It's read with `SELECT … FOR UPDATE`, then inserted later.
- **SQLite** ignores `FOR UPDATE`. Two simultaneous requests both read `G004` and both insert `G005`. The unique constraint `uq_token_per_service_per_day` rejects one, and that patient gets *"Registration failed"* (HTTP 500).
- **MySQL/InnoDB:** for the first token of the day there's no row to lock, so a gap lock is taken instead. Two first-of-day requests can deadlock.
- A prefix change during the day (e.g. `G` to `GN`) breaks the parse. `seq` resets to `1`, which also collides.

**Fix (simple and robust):** retry on conflict, inside a savepoint.
```python
from sqlalchemy.exc import IntegrityError

for attempt in range(5):
    token_number = QueueService.generate_token_number(branch_id, service_type_id)
    token = QueueToken(..., token_number=token_number)
    try:
        with db.session.begin_nested():
            db.session.add(token)
        break
    except IntegrityError:
        continue
else:
    raise ValueError("Queue is busy, please try again")
db.session.commit()
```
**Better long-term:** a per-day counter table, `token_counters(branch_id, service_type_id, queue_date, last_seq)`, incremented atomically with `UPDATE … SET last_seq = last_seq + 1`. Also store the sequence as an integer column instead of parsing it out of `token_number`.

---

## P1-4 · Time zones: UTC stored, shown as local, queue day is the server's date
**Severity:** High · **Status:** Confirmed (code)

**Where:**
- [app/models/queue_token.py:37-41](../../app/models/queue_token.py#L37-L41): `issued_at` uses `datetime.now(timezone.utc)`; `queue_date` uses `date.today` (server local).
- Templates: `t.issued_at.strftime('%H:%M')` in `officer/queue.html`, `manager/other_queue.html`, `admin/audit_logs.html`, `admin/dashboard.html`.
- [app/views/officer.py:62](../../app/views/officer.py#L62): the hourly chart compares `datetime.now().hour` (local) with `completed_at.hour` (UTC).
- `/api/queue/waiting` returns `issued_at.isoformat()` with no offset, so browsers treat it as local time.

**Impact:**
- On a UTC server (EC2 default):
  - every time staff see is **5 h 30 min behind** Sri Lanka time;
  - the queue "day" rolls over at **05:30 local**, so tokens issued before 05:30 count toward the previous day;
  - the officer hourly chart puts work in the wrong hour.
- On a local-time server, `queue_date` is right but the stored UTC times are still shown unconverted.

**Fix:**
```python
# app/utils/timeutil.py
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LOCAL_TZ = ZoneInfo("Asia/Colombo")

def local_today():
    return datetime.now(LOCAL_TZ).date()

def to_local(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)
```
- Replace **all 24** `date.today()` / `datetime.now()` call sites (including the model default `queue_date`) with `local_today()`.
- Register a Jinja filter `|localtime` and use it in every template that formats a datetime.
- Return `to_local(dt).isoformat()` from the APIs, so the offset is included.
- Officer chart: bucket by `to_local(t.completed_at).hour`.

---

## P1-5 · Patient-level condition summary is overwritten on every visit
**Severity:** Medium · **Status:** Confirmed (code)

**Where:** [app/services/queue_service.py:243](../../app/services/queue_service.py#L243)

**Problem:**
- Completing any visit copies that visit's summary onto `customer.condition_summary`.
- A pharmacy note ("collected meds") replaces a doctor's diagnosis, and any summary an admin curated is silently lost.

**Fix:**
- Keep `QueueToken.condition_summary` as the per-visit record; it's already stored.
- Stop copying it to `Customer`.
- Make `Customer.condition_summary` staff-curated only, or derive "latest summary" in queries from the most recent completed token.

---

## P1-6 · Deactivating a service strands its waiting patients
**Severity:** Medium · **Status:** Confirmed (code)

**Where:** [app/views/admin_view.py:402](../../app/views/admin_view.py#L402) `service_status`

**Problem:**
- `User.allowed_service_codes` only returns **active** services.
- After an admin deactivates a service, tokens already `WAITING` for it disappear from every officer's queue, and `call_next` can never reach them.
- The patients still see "waiting" on their phones.

**Fix:**
```python
if action == "deactivate":
    pending = QueueToken.query.filter(
        QueueToken.service_type_id == service.id,
        QueueToken.queue_date == local_today(),
        QueueToken.status.in_([TokenStatus.WAITING, TokenStatus.CALLED, TokenStatus.SERVING]),
    ).count()
    if pending:
        flash(f"{pending} patients are still queued for this service. "
              "Serve or transfer them first.", "danger")
        return redirect(url_for("admin_view.services"))
```

---

## P1-7 · Tokens get stuck in CALLED/SERVING; end-of-day cleanup never runs
**Severity:** Medium · **Status:** Confirmed (code)

**Where:** [app/tasks/cleanup_tasks.py:10](../../app/tasks/cleanup_tasks.py#L10), `QueueService.call_next`

**Problem:**
- `daily_queue_reset` is never scheduled: there's no Celery beat config, and Celery doesn't work anyway (P2-1).
- Even if it ran, it only moves `WAITING` to `SKIPPED`. `CALLED` and `SERVING` tokens stay open forever.
- `call_next` doesn't check whether the officer already has an open token. Pressing "Call next" repeatedly leaves several `CALLED` tokens assigned to one officer.

**Impact:** completion rates, no-show counts and officer performance numbers are wrong, and the "now serving" lists keep showing patients nobody is serving.

**Fix:**
1. Replace the Celery task with a CLI command run by cron or a systemd timer:
   ```python
   @app.cli.command("close-day")
   def close_day():
       cutoff = local_today()
       QueueToken.query.filter(QueueToken.queue_date < cutoff,
                               QueueToken.status == TokenStatus.WAITING
       ).update({"status": TokenStatus.SKIPPED}, synchronize_session=False)
       QueueToken.query.filter(QueueToken.queue_date < cutoff,
                               QueueToken.status.in_([TokenStatus.CALLED, TokenStatus.SERVING])
       ).update({"status": TokenStatus.NO_SHOW}, synchronize_session=False)
       db.session.commit()
   ```
   Crontab: `5 0 * * * cd /home/ubuntu/pbqms && venv/bin/flask close-day`. Use the `Asia/Colombo` TZ.
2. In `call_next`, refuse to call another patient while the officer has an open token. Return 409 with *"Complete or no-show token G012 first"*.

---

## P1-8 · `reset-queue` and `clean-data` CLI commands break referential integrity
**Severity:** Medium · **Status:** Confirmed (reproduced: orphan rows on SQLite); FK errors on MySQL confirmed by code

**Where:** [app/__init__.py:194-237](../../app/__init__.py#L194-L237)

**Problem:**
- `reset-queue` bulk-deletes today's tokens with `query.delete()`, which does not cascade.
  - `sms_logs.token_id` is `NOT NULL` with a foreign key, so on MySQL the delete fails with an FK error.
  - `satisfaction_ratings` and `audit_logs` rows also reference tokens.
- `clean-data` deletes non-admin users and all services but never touches the `user_services` association table.
  - Reproduced: **1 orphan `user_services` row** remained after the run.
  - On MySQL it fails outright.

**Fix:** delete children first.
```python
token_ids = [t.id for t in q.with_entities(QueueToken.id)]
for model in (SmsLog, SatisfactionRating, AuditLog):
    model.query.filter(model.token_id.in_(token_ids)).delete(synchronize_session=False)
q.delete(synchronize_session=False)
```
In `clean-data`, run `db.session.execute(user_services.delete())` before deleting users and services. Also, SQLite doesn't enforce FKs unless asked, so turn them on for dev and tests so these bugs show up locally:
```python
@event.listens_for(Engine, "connect")
def _sqlite_fk(dbapi_conn, _):
    if isinstance(dbapi_conn, sqlite3.Connection):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")
```

---

## P1-9 · Schema drift between models and migrations
**Severity:** Medium · **Status:** Confirmed (reproduced via Alembic `compare_metadata`)

The diff between a freshly migrated DB and the models reports:

| Drift | Consequence |
|---|---|
| Indexes `ix_tokens_branch_date_status`, `ix_tokens_customer`, `ix_tokens_served_by`, `ix_tokens_priority`, `ix_audit_user`, `ix_audit_created`, `ix_customers_mobile` exist in DB, **not in models** | The next `flask db migrate` autogenerates `drop_index` for all 7. Merged unnoticed, the main queue queries lose their index. |
| `branches.is_active` is nullable in DB, `nullable=False` in model | `NULL` rows are possible from raw SQL or old data. `filter_by(is_active=True)` silently hides them. |
| `users.role` is `VARCHAR(14)` on SQLite; `SPECIALIST_DOCTOR` is 17 chars | Only works because SQLite ignores length. Shows the migration chain isn't modelled consistently. |
| `ix_customers_mobile` duplicates the unique index on `customers.mobile` | Wasted write cost. |

**Fix:**
1. Declare the wanted indexes in the models:
   ```python
   class QueueToken(db.Model):
       __table_args__ = (
           db.UniqueConstraint(...),
           db.Index("ix_tokens_branch_date_status", "branch_id", "queue_date", "status"),
           db.Index("ix_tokens_customer", "customer_id"),
           db.Index("ix_tokens_served_by", "served_by"),
       )
   ```
   - Drop `ix_tokens_priority`: a boolean index is low-value.
   - Drop `ix_customers_mobile`: it duplicates the unique index.
2. Add migration `011_schema_alignment`: `UPDATE branches SET is_active = 1 WHERE is_active IS NULL`, then `alter_column(nullable=False)`. Drop the two redundant indexes.
3. Add a CI test that fails on drift:
   ```python
   def test_models_match_migrations(tmp_path): ...  # run upgrade, compare_metadata, assert diff == []
   ```

---

## P1-10 · Migration 002 likely fails on a fresh MySQL database
**Severity:** Medium · **Status:** Needs verification (MySQL not available in this audit)

**Where:** [migrations/versions/002_hospital_services_and_priorities.py](../../migrations/versions/002_hospital_services_and_priorities.py) `_normalize_enum_column`

**Problem:**
- To convert lowercase enum values to uppercase, it alters the column to a *transitional* ENUM holding both, e.g. `('waiting', …, 'WAITING', …)`.
- MySQL compares ENUM members with the column collation. The default `utf8mb4_0900_ai_ci` is case-insensitive, so this should be rejected with **error 1291 "Column has duplicated value in ENUM"**.
- The same applies to `priority_reason`.

**Impact:**
- If production already migrated past 002, there's no impact.
- But a **new MySQL install** (Docker, disaster recovery, a new hospital) can't run `flask db upgrade`.

**Fix:** go through `VARCHAR` instead of a mixed-case ENUM.
```python
op.alter_column(table, column, existing_type=sa.Enum(*old_values, name=enum_name),
                type_=sa.String(32))
connection.execute(sa.text(f"UPDATE {table} SET {column} = UPPER({column})"))
op.alter_column(table, column, existing_type=sa.String(32),
                type_=sa.Enum(*new_values, name=enum_name))
```
**Verify:** `docker compose up db` then `DATABASE_URL=mysql+pymysql://… flask db upgrade` from empty. Add this as a CI job using a `mysql:8.0` service container.

---

## P1-11 · Production config silently falls back to SQLite
See **P0-5**. It's listed here too because it's a data-integrity risk, not only a config issue.

---

## P1-12 · Service-duration data is always zero
**Severity:** Medium · **Status:** Confirmed (code + route/JS map)

**Where:** [app/api/queue.py:285](../../app/api/queue.py#L285) `start_service`; [static/js/officer-console.js](../../static/js/officer-console.js)

**Problem:**
- No UI calls `/api/queue/token/<id>/start`. The console goes `CALLED` → *Mark complete* directly.
- `complete_service` then sets `served_at = completed_at`.
- So `service_duration_minutes` is always 0, and the `SERVING` status never happens in practice.

**Fix:** pick one.
- Add a **"Start consultation"** button that calls `/start`. Complete is then only allowed from `SERVING`. This is the better data.
- Or remove the `SERVING` state and `/start` entirely, and measure duration as `completed_at - called_at`.

---

## P1-13 · Patients can hold unlimited simultaneous tokens
**Severity:** Medium · **Status:** Confirmed (code)

**Problem:**
- The same mobile can register for the same service any number of times on one day.
- With the public endpoint unthrottled (P3-4), one person or script can flood a queue with fake patients and SMS sends.

**Fix:** in `register_customer`:
```python
existing = QueueToken.query.filter_by(customer_id=customer.id, service_type_id=service_type_id,
                                      queue_date=local_today()).filter(
    QueueToken.status.in_([TokenStatus.WAITING, TokenStatus.CALLED, TokenStatus.SERVING])).first()
if existing:
    return existing   # idempotent: show the token they already have
```

---

## P1-14 · Appointment requests have no lifecycle and are stored unnormalised
**Severity:** Medium · **Status:** Confirmed (code)

**Where:** [app/api/queue.py:32](../../app/api/queue.py#L32), [app/models/appointment_request.py](../../app/models/appointment_request.py)

**Problem:**
- The API answers `"status": "pending"`, but the model has **no status column**. Nobody can accept, reject or close a request, so the dashboards list them forever (latest 20 only).
- `mobile` is stored as typed, not normalised (unlike `Customer`), so it can't be matched to a patient.
- There's no duplicate protection.

**Fix:**
- Add `status` (`pending|scheduled|rejected|done`), `handled_by`, `handled_at` and `customer_id`. Normalise `mobile` with the P1-1 function.
- Add staff actions to change the status.
- Filter dashboards to `pending`.

---

## P1-15 · The audit trail is incomplete and records failures as actions
**Severity:** Medium · **Status:** Confirmed (code)

**Where:** [app/utils/decorators.py:25-30](../../app/utils/decorators.py#L25-L30); [app/views/admin_view.py](../../app/views/admin_view.py)

**Problem:**
- The **HTML admin pages**, which are what admins actually use, are not audited: `user_new` (L573), `user_edit`, `user_deactivate` (L723), `branch_new` (L505), `branch_edit` (L537), `branch_deactivate`. Only their JSON API twins are. Role changes, password resets and deactivations leave no record.
- Logins, failed logins and logouts are not audited.
- `@audit_action` logs **after** the view returns, regardless of outcome. A rejected 403/409 attempt is recorded as if the action happened.
- `AuditLog.token_id` exists but is never filled in.
- The IP column holds the nginx address (see P3-1).

**Fix:**
```python
def audit_action(action_name):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            result = f(*args, **kwargs)
            status = getattr(result, "status_code", None) or (
                result[1] if isinstance(result, tuple) else 200)
            AuditService.log(user_id=..., action=action_name,
                             token_id=kwargs.get("token_id"),
                             payload={"url": request.path, "method": request.method,
                                      "status": status, "target": kwargs})
            return result
        return decorated
    return decorator
```
- Apply it to every admin/manager HTML mutation.
- Log `login_success` / `login_failed`, with the email in the payload (never the password).
- For user edits, log *before/after* values of role, branch, is_active and services.
