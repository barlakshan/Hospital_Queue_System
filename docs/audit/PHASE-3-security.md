# Phase 3 — Remaining security issues

Round 1 ([SECURITY_REPORT.md](../SECURITY_REPORT.md)) fixed XSS, cross-branch access, CSRF, session revocation and the like. This phase covers what is still open, including one weakness the round-1 login throttle introduces when deployed behind nginx (P3-1).

---

## P3-1 · No ProxyFix: every request appears to come from nginx
**Severity:** High · **Status:** Confirmed (code; there's no `ProxyFix` anywhere)

**Where:** `create_app` in [app/__init__.py](../../app/__init__.py); [app/api/auth.py:20](../../app/api/auth.py#L20); [app/services/audit_service.py:13](../../app/services/audit_service.py#L13)

**Problem:** gunicorn sits behind nginx on 127.0.0.1, so `request.remote_addr` is always `127.0.0.1`.
- **Account lockout DoS.** The login throttle key is `ip|email`, so effectively it's just `email`. Five wrong passwords from *anyone on the internet* lock that staff member out for 5 minutes, and a script can keep every account locked permanently.
- **Audit logs** record `127.0.0.1` for every action.
- `request.scheme` is `http`, so `url_for(..., _external=True)` and the QR fallback URL are wrong.

**Fix:**
```python
# app/__init__.py, in create_app()
from werkzeug.middleware.proxy_fix import ProxyFix
if app.config.get("BEHIND_PROXY", True):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
```
Both nginx configs must set `X-Forwarded-For`. The EC2 one already does; the round-1 change added it to `nginx/pbqms.conf`.

Also change the throttle so it can't be used to lock out a victim:
- key on the IP alone for the hard limit, and
- add a per-account counter that only *delays* responses (e.g. 1 s × failures) rather than blocking.

---

## P3-2 · Public patient pages are enumerable by sequential ID
**Severity:** Medium · **Status:** Confirmed (code)

**Where:**
- `/register/token/<int:id>`
- `/api/queue/token/<int:id>/status`
- `/api/queue/token/<int:id>/rate`

**Problem:** IDs are sequential integers. Iterating `1…N` reveals, for every patient:
- service and department (e.g. Dermatology or Cardiology, which is health information);
- queue status;
- the name of the staff member who served them.

The same loop also lets anyone **rate** any completed visit.

**Fix:** add an unguessable public reference.
```python
public_ref = db.Column(db.String(32), unique=True, nullable=False,
                       default=lambda: secrets.token_urlsafe(16))
```
- Use `/register/token/<public_ref>` everywhere public. Keep the integer ID for staff APIs only.
- Needs a migration with a backfill.

---

## P3-3 · Patients can self-declare "Emergency" priority
**Severity:** Medium · **Status:** Confirmed (code)

**Where:** [app/templates/customer/register.html:71-76](../../app/templates/customer/register.html#L71-L76), `register()` in [app/api/queue.py](../../app/api/queue.py)

**Problem:** the public form lets anyone set `EMERGENCY`, `URGENT`, `ELDERLY` and so on. Priority tokens are always called first, so anyone can jump the queue.

**Fix:**
- Public registrations may *request* priority. Store `priority_requested` and set `is_priority=False`.
- Officers confirm it with the existing `/priority` endpoint. Wire a button for this; `setPriority` exists in the JS but no UI calls it.
- Real emergencies shouldn't go through a QR queue anyway, so show a "go to the emergency desk" notice instead.

---

## P3-4 · Public endpoints have no rate limiting (SMS cost and SMS bombing)
**Severity:** Medium · **Status:** Confirmed (code)

**Where:** `POST /api/queue/register`, `POST /api/queue/appointments`, `POST /api/queue/token/<id>/rate`

**Problem:** each registration sends an SMS to the number supplied. A script can:
- send unlimited hospital-branded SMS to any victim's phone;
- drain the Text.lk balance;
- fill queues with fake patients (see also P1-13).

**Fix:**
- nginx: `limit_req zone=pbqms_public rate=10r/m burst=5` on `/api/queue/register` and `/api/queue/appointments`.
- App: at most 3 tokens per mobile per day.
- Optional: an OTP confirmation step before the first SMS to a new number.

---

## P3-5 · Session handling gaps
**Severity:** Low–Medium · **Status:** Confirmed (code)

| Issue | Where | Fix |
|---|---|---|
| `login_user(user, remember=True)` always. On shared hospital PCs, staff stay logged in for 7 days after closing the browser. | [app/api/auth.py:89](../../app/api/auth.py#L89) | Add a "Keep me signed in" checkbox and default it off. Set `PERMANENT_SESSION_LIFETIME` to about 8 h. |
| Logout is a GET link, so any page can log staff out (`<img src=/auth/logout>`). | `base.html:141` | Use a POST form button. |
| Two logout routes (`/auth/logout`, `/api/auth/logout`) and an HTML login page under `/api/auth/login-page`. | [app/api/auth.py:53](../../app/api/auth.py#L53), [:113](../../app/api/auth.py#L113) | Keep one logout route. Move the login page to `/auth/login`. |
| The password change API exists, but there's no UI and no forced change for admin-set passwords. | `/api/auth/change-password` | Add a "Change password" page. Add a `must_change_password` flag on accounts created by an admin. |

---

## P3-6 · Dependencies with known vulnerabilities
**Severity:** Medium · **Status:** Confirmed (pip-audit, 2026-11-24) · **Fixed**

The first version of this finding was written from memory and listed gunicorn,
which pip-audit does **not** flag. The actual pip-audit result for the original
`requirements.txt`:

| Package | Pinned | Fixed in | Now pinned |
|---|---|---|---|
| Werkzeug | 3.0.3 | 3.1.9 (several advisories) | 3.1.9 |
| Flask | 3.0.3 | 3.1.3 | 3.1.3 |
| requests | 2.32.3 | 2.33.0 | 2.33.0 |
| cryptography | 42.0.8 | 50.0.0 | 50.0.2 |
| pillow | 12.3.0 | 13.0.0 | 13.0.0 |
| PyMySQL | 1.1.0 | 1.1.1 | 1.1.1 |
| python-dotenv | 1.0.1 | 1.2.2 | 1.2.2 |
| python-engineio | 4.9.1 | 4.13.2 | 4.15.1 |
| python-socketio | 5.11.3 | 5.16.2 | 5.18.1 (with Flask-SocketIO 5.7.2) |

`pip-audit -r requirements.txt` now reports no known vulnerabilities.

Also done:
- Celery, kombu, redis and their dependencies removed (P2-1).
- `pytest` and its dependencies moved to `requirements-dev.txt`.
- `requirements_old.txt` deleted.
- CI runs `pip-audit -r requirements.txt --strict`.

Not changed: `PyJWT`, `aiohttp`, `aiohttp-retry` are dependencies of `twilio`, which is still a supported SMS provider.

---

## P3-7 · CDN assets without integrity hashes, and no CSP
**Severity:** Low–Medium · **Status:** Confirmed (code)

**Where:** [app/templates/base.html:10-167](../../app/templates/base.html#L10-L167) and `display/screen.html`

**Problem:**
- Bootstrap, Tabler icons and the Socket.IO client load from jsDelivr with **no `integrity=` attribute**, on every staff page.
- A CDN or package compromise would run script with full staff privileges.
- There's no Content-Security-Policy to limit the damage.

**Fix:**
- Add `integrity="sha384-…" crossorigin="anonymous"` to each tag, or vendor the files into `static/vendor/`.
- Then move inline `<script>` blocks into files and send a CSP:
  ```
  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' wss:
  ```

---

## P3-8 · Patient phone numbers and messages in application logs
**Severity:** Medium (privacy) · **Status:** Confirmed (code)

**Where:**
- [app/services/sms_service.py:69,81,108,111,156](../../app/services/sms_service.py#L108)
- [app/services/queue_service.py:112,170](../../app/services/queue_service.py#L170)
- [app/tasks/sms_tasks.py:40-57](../../app/tasks/sms_tasks.py#L40)

**Problem:**
- Full mobile numbers are written to the logs at INFO, ERROR and DEBUG levels.
- The mock provider logs the full SMS text: patient token, service, and the staff member's name.
- `capture_output=True` in gunicorn sends all of it to journald, which has no retention policy.

**Fix:**
```python
def mask(m): return f"{m[:3]}•••{m[-2:]}" if m else m
logger.info("SMS sent to %s type=%s id=%s", mask(mobile), sms_type, msg_id)
```
- Use `%s`-style logging, not f-strings, so arguments aren't formatted when the level is off.
- Never log message bodies outside development.
- Set a journald retention period (`MaxRetentionSec=30day`).

---

## P3-9 · Health data handling
**Severity:** Medium (compliance) · **Status:** Confirmed (code)

**Problem:**
- Condition summaries (health data) are stored in plain text.
- They're viewable and editable by every admin and every manager of a branch the patient visited.
- Views aren't logged; only edits are.
- There's no retention or deletion process for patient records, and `clean-data` is all-or-nothing.

**Fix (policy plus code):**
- Decide which roles may see condition summaries, and enforce that in templates and views.
- Log reads of patient detail.
- Define a retention period, and add a `purge-patients --older-than` command.
- Consider column-level encryption for `condition_summary` and `review_comments`.

---

## P3-10 · Socket.IO room join is unauthenticated
**Severity:** Low · **Status:** Confirmed (code)

**Where:** [app/views/display.py:38](../../app/views/display.py#L38) `on_join_branch`

**Problem:** any anonymous client can join `branch_<any id>`. The payload is display-level only (token numbers, counters, service names), so the impact today is low. It becomes a leak as soon as anyone adds staff-only data (e.g. patient names) to that room.

**Fix:**
- Keep `branch_*` rooms strictly for public display data.
- For staff events, use `staff_branch_<id>` rooms and join them only when `current_user.is_authenticated` and `current_user.branch_id == id`.
- Validate `data` is a dict before calling `.get`.
