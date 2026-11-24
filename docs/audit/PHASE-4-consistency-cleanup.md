# Phase 4 — Inconsistencies and cleanup

Nothing here loses data on its own. But these issues mislead users, hide bugs, or make the earlier phases harder to maintain.

---

## P4-1 · Hard-coded fake metrics shown as real data
**Severity:** Medium (people make staffing decisions on these numbers) · **Status:** Confirmed (code)

| Screen | Value shown | Where | Real calculation |
|---|---|---|---|
| Officer dashboard: "served vs yesterday" | always **+3** | [app/views/officer.py:104](../../app/views/officer.py#L104) `served_delta: 3` | today − yesterday for this officer |
| Officer dashboard: avg service time | always **8 min** | same dict, `avg_service_min: 8` | avg(`completed_at − served_at`). Needs P1-12 fixed first. |
| Manager dashboard: avg wait | always **18 min** | [app/views/manager_view.py:72](../../app/views/manager_view.py#L72) | avg(`called_at − issued_at`) for today |
| Manager dashboard: change % | always **8 %** | same, `delta_pct: 8` | today vs same weekday last week |
| Manager officer table: avg time | always **8**, every officer "active" | [app/views/manager_view.py:52](../../app/views/manager_view.py#L52) | per-officer avg duration; active = logged in or recent action |
| Admin dashboard: online now | always **0** | [app/views/admin_view.py:298](../../app/views/admin_view.py#L298) | users with `last_seen` in the last 5 min, or remove the tile |
| Admin dashboard: today delta | yesterday=0, today=5 shows **+500 %** | [app/views/admin_view.py:242](../../app/views/admin_view.py#L242) | show "—" when yesterday is 0 |
| Queue officer list: wait per row | `(i+1) × 8` min | `officer-console.js` `renderQueue` | position × `service.avg_service_min` |

**Fix:** compute these in one module (`app/services/metrics.py`). Until a metric is real, show "—", never a made-up number.

---

## P4-2 · Five different "estimated wait" formulas
**Severity:** Low · **Status:** Confirmed (code)

| Place | Formula |
|---|---|
| `register_customer` (stored, then sent by SMS) | position × service `avg_service_min`, frozen at issue time |
| `/api/queue/token/<id>/status` | position × `avg_service_min`, live |
| `/api/queue/branch/<code>/info` | `max(5, waiting × 8)` across all services |
| `customer.token_page` progress bar | `100 − position/20 × 100` |
| Officer console list | `(i+1) × 8` |

**Fix:** one function, `QueueService.estimate_wait(token)`, which also divides by the number of active staff serving that service. Use it everywhere.

---

## P4-3 · Duplicated logic that has already drifted
**Severity:** Low · **Status:** Confirmed (code)

| Duplicate | Copies | Action |
|---|---|---|
| Display payload builder | `api/queue.py display_state` and `notification_service.push_queue_update` (already differ in ordering, see P2-6) | One `build_display_state()` |
| Staff role list (9 roles) | Repeated in **6 decorators** in `api/queue.py` and **3** in `views/officer.py` | `STAFF_ROLES` constant in `models/user.py` |
| Legacy role mapping (dentist → specialist_doctor, …) | `api/queue.py request_appointment` and `User._legacy_service_role` | One function in `models/user.py` |
| SMS provider normaliser | `config._normalize_sms_provider` and `SmsService._normalize_provider` | Keep the config one; `SmsService` reads `current_app.config` |
| Branch-code regex | `api/admin.py` and `views/admin_view.py` (round 1) | `app/utils/validators.py` |
| Password, email and employee-ID validation for user create/edit | `api/admin.py`, `views/admin_view.py`, `views/manager_view.py` | `UserService.create/update` used by all three |
| Logout | `api/auth.py` and `views/auth_view.py` | Keep one |

---

## P4-4 · Condition summary passed through `current_user` attributes
**Severity:** Low · **Status:** Confirmed (code)

**Where:** [app/api/queue.py:309](../../app/api/queue.py#L309), `QueueService.complete_service`

**Problem:** the view does `setattr(current_user, "_complete_condition_summary", …)` and the service reads it back with `getattr`. This is hidden coupling: any other caller of `complete_service` gets whatever value a previous request left on the object.

**Fix:**
```python
def complete_service(token_id, officer_user, condition_summary=None): ...
token = QueueService.complete_service(token_id, current_user, condition_summary or None)
```

---

## P4-5 · Three different sources of seed data
**Severity:** Low · **Status:** Confirmed (code)

| Source | Role names used for default services |
|---|---|
| Migration `002` | legacy `lobby_officer`, `loan_officer` |
| `flask seed-db` ([app/__init__.py](../../app/__init__.py)) | new `opd`, `pharmacy`, `dentist`, … (insert-only; never updates existing rows) |
| `setup_test.py` | legacy names |

**Effect:**
- A database built by migrations keeps legacy role names forever. It only works because of the compatibility mapping in `User.allowed_service_codes`.
- A database built by `seed-db` uses new names. The two can behave differently for the same service.

**Fix:**
- Add a data migration that rewrites `service_types.allowed_roles` from legacy to new names: `lobby_officer` → `opd`, `loan_officer` → `pharmacy`, `specialist_doctor` → the matching specialty.
- Then delete the legacy mapping code and `setup_test.py`.

---

## P4-6 · Branding and naming leftovers (People's Bank to Melsta Hospitals)
**Severity:** Low (one has functional impact) · **Status:** Confirmed (code)

| Leftover | Where |
|---|---|
| `server_name queue.peoplesbank.lk`, cert `peoplesbank.crt` | [nginx/pbqms.conf](../../nginx/pbqms.conf) |
| "People's Bank QEMS" | [migrations/versions/001_initial_schema.py:1](../../migrations/versions/001_initial_schema.py#L1) |
| Sender IDs `PPLSBANK`, `TextLKDemo` | [.env.example:23](../../.env.example#L23) |
| Default sender `MELSTAHOSP` | [app/config.py:50](../../app/config.py#L50) |
| `PBQMS`, `pb-*` CSS classes, `peoplesbank` paths | throughout |
| "branch / customer / officer" in code, "location / patient / staff" in UI | throughout (documented in the README, but leaks into error messages, e.g. "Invalid or inactive branch") |

**Functional impact:**
- An SMS sender ID must be registered with the gateway.
- If `TEXTLK_SENDER_ID` is unset, the code sends `MELSTAHOSP`; the example file suggests `TextLKDemo`.
- A mismatch makes Text.lk reject every message, and with the thread fallback that only shows up as `FAILED` rows in `sms_logs`.

**Fix:** settle one sender ID, put it in `.env.example`, and have the app refuse to start in production if `SMS_PROVIDER != mock` and the sender ID is unset. Rename the user-facing strings.

---

## P4-7 · Deployment configs contradict each other
**Severity:** Medium · **Status:** Confirmed (code)

| Topic | Docker path | EC2 path |
|---|---|---|
| App server | `python wsgi.py` (Werkzeug dev server, `allow_unsafe_werkzeug=True`) | gunicorn, [gunicorn.conf.py](../../gunicorn.conf.py) |
| Bind | `0.0.0.0:5000` | `127.0.0.1:8000` |
| nginx | [nginx/pbqms.conf](../../nginx/pbqms.conf): TLS, proxies to `web:5000` | heredoc in [deploy/ec2-setup.sh](../../deploy/ec2-setup.sh): **no TLS**, proxies to `127.0.0.1:8000` |
| Python | 3.12 (Dockerfile, CI) | system `python3` |
| Celery | separate container (broken, P2-1) | none |

Also:
- `ec2-setup.sh:19` runs `git pull … || true`, which hides failed updates.
- The deploy health check accepts any non-error response, including a redirect to the login page, which proves little.

**Fix:**
- Pick one production path. Make the Dockerfile `CMD` gunicorn with the same config, and generate the EC2 nginx file from `nginx/pbqms.conf`.
- Add TLS (certbot) to the EC2 path.
- Remove `|| true`.
- Add a `/healthz` endpoint that checks the DB (`SELECT 1`), and curl that.

---

## P4-8 · Dead code and stale documentation
**Severity:** Low · **Status:** Confirmed (code + route/JS map)

| Item | Why it's dead or stale |
|---|---|
| [static/js/queue-display.js](../../static/js/queue-display.js) | Never included; `display/screen.html` has its own inline copy |
| `token_called` listener | Never emitted (P2-11) |
| `app/tasks/*`, `make_celery` | Non-functional (P2-1) |
| `debug_analytics.py`, `setup_test.py`, `test_analytics_template.py` | Ad-hoc scripts; two of them break CI (P0-2) |
| `requirements_old.txt` | Superseded |
| Try/except with `print(traceback)` in `manager_view.analytics` | Debug leftover; Flask already logs exceptions |
| `/api/queue/token/<id>/start` | No caller (P1-12) |
| `setPriority` in `officer-console.js` | Exported, but no button calls it |
| [BUGFIXES_SUMMARY.md](../BUGFIXES_SUMMARY.md) | Describes Celery retries and SMS handling that don't run (P2-1) |
| README "Celery optional" / reset-queue docs | `reset-queue` fails on MySQL (P1-8) |

**Fix:** delete the dead code, and update the README once Phases 0–2 are done. Consolidate the two audit reports and BUGFIXES_SUMMARY into a `docs/` folder.

---

## P4-9 · Deprecated APIs (about 100 warnings per test run)
**Severity:** Low · **Status:** Confirmed (reproduced: pytest warnings)

- `Query.get()` is legacy in SQLAlchemy 2.0 and used throughout `app/` and `tests/`. Replace it with `db.session.get(Model, id)`; `get_or_404` becomes `db.get_or_404(Model, id)`.
- `FLASK_ENV` was removed from Flask itself. The app reads it manually, which works but is confusing. Rename it to `APP_ENV` and keep reading `FLASK_ENV` as a fallback for one release.
- The `ENV` config key is set in `ProductionConfig` and Flask no longer uses it. Check `config_name == "production"` instead.

---

## P4-10 · Test-suite hygiene
**Severity:** Low · **Status:** Confirmed (code)

- Every test file re-declares the same `client` fixture. Move it to `tests/conftest.py`, together with the SMS guard from P0-1.
- Tests use an on-disk `pbqms_test.sqlite3` shared between runs, so parallel runs (`pytest -n`) or two terminals corrupt each other. Use `sqlite:///:memory:` with `StaticPool`, or `tmp_path`.
- Missing tests for the Phase 1 bugs:
  - phone normalisation table
  - concurrent token generation
  - CLI commands with FKs on
  - migration/model drift
  - time-zone day boundary
- No MySQL CI job. Add one (see P1-10).

---

## P4-11 · Minor UI and data inconsistencies
**Severity:** Low · **Status:** Confirmed (code)

- `base.html:130` links to `/display/` with an empty code for users without a branch, which lands on another hospital (P2-5).
- `QueueToken.status`, `is_priority` and `priority_reason` are nullable in the model, though code assumes they're set. For example `token.priority_reason.value` crashes on `NULL`. Add `nullable=False`.
- `Counter.user_id` can point at a user of another branch. Round 1 fixed this for API-created counters; also add a model-level check or validate it in the admin UI.
- `Customer.mobile` is `String(20)` while `AppointmentRequest.mobile` is unvalidated free text (P1-14).
- `ServiceType.code` is limited to 5 characters but the admin form generates codes from names. Collision suffixing works, but it produces codes like `GENE1` that appear nowhere in the UI. Show them, or stop using them as identifiers.

---

## P4-12 · `.gitignore` now ignores generated QR codes, but Docker mounts them
**Severity:** Low · **Status:** Confirmed (round-1 change)

**Problem:** round 1 untracked `static/qrcodes/`. QR files are regenerated on demand, so nothing breaks. But the Docker volume `./static/qrcodes:/app/static/qrcodes` now starts empty, and the round-1 Dockerfile `USER appuser` owns `/app/static/qrcodes` only inside the image. A host-mounted directory owned by root becomes unwritable for `appuser`.

**Fix:** use a named volume (`qrcodes:/app/static/qrcodes`) instead of a bind mount, or `chown` the host directory to the container UID.
