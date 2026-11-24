# Phase 2 — Functional bugs

These are features that don't work, or don't work in the production setup.

---

## P2-1 · Celery is completely non-functional
**Severity:** High · **Status:** Confirmed (reproduced)

There are four separate faults, any one of which breaks it:

| # | Fault | Evidence |
|---|---|---|
| a | **Incompatible pins.** `kombu==5.7.0` needs `redis>=6.1`, but [requirements.txt:55](../../requirements.txt#L55) pins `redis==5.0.4`. | `send_sms_async.delay()` raises `VersionMismatch: Redis transport requires redis-py versions 6.1.0 or later. You have 5.0.4` in 0.06 s. Also `celery==5.4.0` is older than what `kombu 5.7` targets. `pip check` doesn't catch it. |
| b | **No Celery app instance.** `make_celery()` in [app/tasks/__init__.py:4](../../app/tasks/__init__.py#L4) is never called. `celery -A app.tasks worker` ([docker-compose.yml:48](../../docker-compose.yml#L48)) finds no app. | The `celery` container can't start. |
| c | **No Flask app context in tasks.** `send_sms_async` uses `SmsLog.query` and `current_app.config`. | Even a working worker would crash with "Working outside of application context". |
| d | **Nothing scheduled.** There's no `beat_schedule`, so `daily_queue_reset` never runs. | See P1-7. |

**Current behaviour:** every SMS falls back to a daemon thread inside the web worker. It works, but:
- there are no retries;
- messages are lost if the worker restarts;
- the documented retry logic ([BUGFIXES_SUMMARY.md](../BUGFIXES_SUMMARY.md) §3) is never used.

**Fix:** choose one.
- **A. Remove Celery (recommended for this size of system).**
  - Delete `app/tasks/sms_tasks.py`, `make_celery`, the `celery` compose service, and the `celery`, `kombu`, `billiard`, `amqp`, `vine` and `click-*` pins.
  - Make `SmsService._send_async` always use the thread path.
  - Add a small `flask retry-sms` cron command that re-sends `SmsLog` rows with `status=FAILED` and age under 10 minutes.
  - Replace the cleanup task with the `close-day` CLI command (P1-7).
- **B. Make Celery work.**
  - Bump to `celery>=5.5`, `kombu>=5.5`, `redis>=6.1`.
  - Create the app at `app/celery_app.py` with a Flask-context task base:
    ```python
    from celery import Celery, Task
    from app import create_app
    flask_app = create_app()
    class FlaskTask(Task):
        def __call__(self, *a, **kw):
            with flask_app.app_context():
                return self.run(*a, **kw)
    celery = Celery(flask_app.import_name, task_cls=FlaskTask,
                    broker=flask_app.config["CELERY_BROKER_URL"])
    celery.conf.beat_schedule = {"close-day": {"task": "app.tasks.cleanup_tasks.daily_queue_reset",
                                               "schedule": crontab(hour=0, minute=5)}}
    ```
  - Run `celery -A app.celery_app.celery worker` plus `beat`.

---

## P2-2 · Socket.IO breaks under the production gunicorn config
**Severity:** High · **Status:** Confirmed (code; this is documented Flask-SocketIO behaviour)

**Where:** [gunicorn.conf.py:4](../../gunicorn.conf.py#L4) `workers = max(2, …)`; [app/__init__.py:85](../../app/__init__.py#L85) `socketio.init_app(…)` (no `message_queue`); the EC2 nginx config has no sticky sessions.

**Problem:**
- Flask-SocketIO keeps each client's session in the memory of one worker process.
- With two or more gunicorn workers and no sticky load balancing, a long-polling request often lands on a different worker, which returns `400 Invalid session`.
- `socketio.emit()` in `push_queue_update` only reaches clients connected **to the worker that handled the request**.

**Impact:**
- Lobby display screens and officer consoles miss live updates at random.
- They fall back to their 30 s / 20 s polling, so a patient called by an officer may not appear on the TV for up to 30 s.

**Fix:** pick one.
- **Simplest:** one worker with threads.
  ```python
  # gunicorn.conf.py
  workers = 1
  threads = 50
  ```
- **Scalable:** add a message queue so emits reach every worker, plus sticky sessions.
  ```python
  socketio.init_app(app, message_queue=app.config["REDIS_URL"], ...)
  ```
  ```nginx
  upstream pbqms { ip_hash; server 127.0.0.1:8001; server 127.0.0.1:8002; }
  ```
  Use one gunicorn process per port, or switch to `eventlet`/`gevent` workers.

**Verify:** open the display at `/display/<code>`. Call patients from two browsers for 5 minutes. Every call should appear within about 1 s, with no `400` responses in the nginx log.

---

## P2-3 · Dead export buttons on the admin dashboard
**Severity:** Medium · **Status:** Confirmed (reproduced: route map vs templates)

**Where:** [app/templates/admin/dashboard.html:13](../../app/templates/admin/dashboard.html#L13) and [:196](../../app/templates/admin/dashboard.html#L196)

| Button | Link | Actual result |
|---|---|---|
| "Export" (header) | `/api/admin/analytics/export` | **404**: the route doesn't exist |
| "Export CSV" (customers) | `/api/admin/customers?format=csv` | JSON for page 1 (50 rows); `format` is ignored |

**Fix:**
- Point "Export CSV" at the working Excel export: `{{ url_for('admin_view.export_customers') }}`.
- Either implement an analytics export (an XLSX of `demand_analytics`) or remove the header button.

---

## P2-4 · Branch managers can be neither created nor edited
**Severity:** Medium · **Status:** Confirmed (code)

**Where:** [app/models/user.py:22](../../app/models/user.py#L22) `ASSIGNABLE_ROLES` (no `BRANCH_MANAGER`); [app/templates/admin/user_form.html:38](../../app/templates/admin/user_form.html#L38)

**Problem:**
- The whole `/manager/*` module (dashboard, analytics, officers, customers, QR) and `/api/manager/*` exist, but only for "legacy" `BRANCH_MANAGER` accounts.
- An admin can't create a manager.
- Opening an existing manager in *Edit user* shows **no selected role**. The field is `required`, so the admin can't save *any* change (name, password, branch) without changing the person's role away from manager.

**Fix:** this is a product decision.
- If managers are a live role, add `UserRole.BRANCH_MANAGER` to `ASSIGNABLE_ROLES` with the label "Location Manager", and require a branch for it.
- If they're retired, migrate existing managers to `ADMIN` or `OPD`, delete `manager_view.py`, `api/manager.py` and the templates, and remove the role from every decorator.

---

## P2-5 · Display screen silently shows the wrong hospital
**Severity:** Medium · **Status:** Confirmed (code)

**Where:** [app/views/display.py:17](../../app/views/display.py#L17)

**Problem:**
- `/display/<code>` falls back to the **first active branch** when the code is unknown or the branch has been deactivated.
- A lobby TV configured for location B, after B is deactivated or renamed, starts showing location A's queue with no warning.
- Patients in B then follow A's calls.

**Fix:**
```python
if branch_code:
    branch = Branch.query.filter_by(code=branch_code.upper(), is_active=True).first_or_404()
```
Keep the fallback only for the bare `/display` URL. The `short_screen` route is already correct.

---

## P2-6 · Live display and polled display order "now serving" differently
**Severity:** Low · **Status:** Confirmed (code)

**Where:** [app/services/notification_service.py:9-17](../../app/services/notification_service.py#L9-L17) vs `display_state` in [app/api/queue.py](../../app/api/queue.py)

**Problem:**
- The REST endpoint orders serving tokens by `called_at DESC`. The Socket.IO push has **no ORDER BY**.
- The display treats the first item as the big "Now calling" number, so it flips between tokens depending on whether the last update came from a push or a poll.

**Fix:** one function builds the payload for both paths.
```python
# app/services/display_service.py
def build_display_state(branch) -> dict: ...  # ordered, single source of truth
```
`display_state()` returns `jsonify(build_display_state(branch))`, and `push_queue_update()` emits the same dict.

---

## P2-7 · Staff can be saved with no location
**Severity:** Medium · **Status:** Confirmed (code)

**Problem:**
- The user form offers "All locations" (`branch_id = NULL`) for every role.
- For OPD, pharmacy and doctor roles this produces an account whose dashboards are empty, whose appointment list is empty, and for which `call_next` now returns *"Your account is not assigned to a branch"*.

**Fix:** in `user_new`/`user_edit` and the API, `if role != UserRole.ADMIN and not branch_id: error("Select a hospital location")`. Hide the "All locations" option unless the role is Admin.

---

## P2-8 · Manager pages accept admins but show them nothing
**Severity:** Low · **Status:** Confirmed (code)

**Problem:**
- Every `/manager/*` and `/api/manager/*` view allows `ADMIN`, but scopes by `current_user.branch_id`, which is `NULL` for admins.
- Admins see an all-zero dashboard and an empty customer list.

**Fix:** either drop `ADMIN` from those decorators, or accept `?branch_id=` for admins, as `manager_view.officers` already does. Wrap it in a helper `_scoped_branch_id()`.

---

## P2-9 · Admin customer list and export load every row
**Severity:** Low (grows with time) · **Status:** Confirmed (code)

**Where:** [app/views/admin_view.py:738](../../app/views/admin_view.py#L738) and [:776](../../app/views/admin_view.py#L776)

**Problem:** `Customer.query.….all()` renders all patients into one HTML page. The export also builds the whole workbook in memory. After a year of registrations the page becomes very slow.

**Fix:**
- Paginate the page (`.paginate(per_page=50)`, as the manager view does) and add a search box by name or mobile.
- For the export, stream rows with `yield_per(1000)` and openpyxl `write_only=True`.

---

## P2-10 · The root catch-all route swallows typos and can shadow pages
**Severity:** Low · **Status:** Confirmed (route map)

**Where:** [app/views/display.py:24](../../app/views/display.py#L24) `/<string:branch_code>`

**Problem:**
- Any unmatched single-segment URL, such as `/admin`, `/favicon.ico`, `/robots.txt` or `/login`, runs a branch lookup and returns a branch 404.
- A branch created with a code such as `ADMIN`, `DISPLAY` or `REGISTER` would make `/<that code>` show a display screen instead of the expected page.

**Fix:** reject reserved codes in branch validation:
```python
RESERVED_CODES = {"ADMIN", "API", "AUTH", "DISPLAY", "MANAGER", "OFFICER", "REGISTER",
                  "APPOINTMENTS", "STATIC"}
```
Or move the short display URL to `/d/<code>`.

---

## P2-11 · Officer console listens for an event that is never sent
**Severity:** Low · **Status:** Confirmed (code)

**Where:** [static/js/officer-console.js:48](../../static/js/officer-console.js#L48) `socket.on('token_called', …)`

**Problem:**
- The server only emits `queue_update`.
- The listener was meant to show a call made from another device or counter on this console, but that never happens.
- If an officer has the console open in two tabs, the second never shows the patient being served.

**Fix:** after `call_next` succeeds, emit `token_called` to a per-user room (`user_<id>`). Or delete the listener and the expectation.
