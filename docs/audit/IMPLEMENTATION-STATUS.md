# Implementation status

What was done for each finding in this audit, and what still needs a person.

**Legend:** ✅ done in code · 🟡 done, with a stated limitation · 👤 needs action outside the code

Verification: 95 automated tests pass (48 existing, many updated for the new behaviour; 47 new in `tests/test_audit_phase_fixes.py`). `pip-audit` is clean. The migrations produce a schema identical to the models (checked by a test).

---

## Phase 0 — Urgent

| ID | Status | What changed |
|---|---|---|
| P0-1 | ✅ | `TestingConfig` forces `SMS_PROVIDER="mock"` and blanks every gateway credential. `tests/conftest.py` patches `SmsService._dispatch` for every test (opt-out marker `real_sms_dispatch` for the one contract test that mocks HTTP itself). |
| P0-2 | ✅ | `setup_test.py`, `test_analytics_template.py`, `debug_analytics.py` deleted; `pytest.ini` sets `testpaths = tests`. **Also found and fixed:** `TestingConfig` had no `SECRET_KEY`, so 33 tests failed on any machine without a `.env`, including CI. |
| P0-3 | 👤 | Nothing has been committed. Commit on a branch, open a PR, and set `SECRET_KEY`, `BASE_URL` and `DATABASE_URL` on the server before deploying. |
| P0-4 | 👤 | Rotate the EC2 key pair, the Text.lk token and `SECRET_KEY`. Purge `mestal.pem` and `.env` from git history if the repo was ever shared. |
| P0-5 | ✅ 👤 | Production no longer falls back to SQLite: the app refuses to start without `DATABASE_URL`. **Still check the server** (`grep DATABASE_URL /home/ubuntu/pbqms/.env`): if production runs on SQLite today, the next deploy will fail to start until `DATABASE_URL` is set. |

## Phase 1 — Data integrity

| ID | Status | What changed |
|---|---|---|
| P1-1 | ✅ 👤 | `app/utils/validators.normalise_mobile` (used for registrations and appointment requests) returns E.164 or rejects. **Run the data-repair query** in PHASE-1 for existing bad rows. |
| P1-2 | ✅ | The public form never renames a patient. The name for each visit is stored in `queue_tokens.patient_name` and shown to staff. |
| P1-3 | 🟡 | Registration retries (up to 5 times) on a unique-constraint collision. Numbering ignores tokens with a different prefix, so a prefix change no longer collides. Not done: the per-day counter table. |
| P1-4 | ✅ | `app/utils/timeutil.py`. The queue day uses `APP_TIMEZONE` (Asia/Colombo). Every template uses the `localtime` filter, and APIs return ISO times with the offset. The officer chart buckets by local hour. |
| P1-5 | ✅ | Completing a visit stores the summary on the visit only. Patient pages show the curated summary plus the latest visit note. |
| P1-6 | ✅ | A service can't be deactivated while patients are queued for it today. |
| P1-7 | ✅ | `flask close-day` (cron on EC2, `scheduler` container on Docker). `call_next` returns 409 while the staff member has an open patient, and the console resumes that patient after a reload (`GET /api/queue/current`). |
| P1-8 | ✅ | `reset-queue` (now needs `--confirm`) and `clean-data` delete child rows first. SQLite now enforces foreign keys in dev and tests. That immediately exposed one more bug: auditing a rejected action on a non-existent token violated the FK and the audit row was silently lost. Fixed. |
| P1-9 | ✅ | Indexes declared on the models; migration `010` aligns the schema; `test_migrations_match_models` fails on any future drift. |
| P1-10 | 🟡 | Migration 002 now converts ENUM case through VARCHAR and keeps column nullability. It could not be run against MySQL here; the new CI job `mysql-migrations` runs upgrade → downgrade → upgrade on MySQL 8. |
| P1-11 | ✅ | See P0-5. |
| P1-12 | ✅ | "Start consultation" button in the console. A visit that is completed without being started is measured from the call time. |
| P1-13 | ✅ | Registering again for the same service returns the existing token (HTTP 200, `existing: true`). |
| P1-14 | ✅ | `status`, `handled_by`, `handled_at`, `customer_id` added. Mobile is normalised and duplicates are prevented. Schedule/Reject/Done buttons on the admin and staff dashboards; dashboards list open requests only. |
| P1-15 | ✅ | `@audit_action` skips GETs and records the HTTP status and outcome, `token_id` and target ids, including rejected attempts. It's applied to the admin and manager HTML forms. Logins (success and failure), logouts and password changes are logged. User edits log before/after values. |

## Phase 2 — Functional bugs

| ID | Status | What changed |
|---|---|---|
| P2-1 | ✅ | Option A: Celery removed (`app/tasks`, compose service, pins). SMS always uses the thread path. New `flask retry-sms` re-sends recent failures. |
| P2-2 | ✅ | Gunicorn runs 1 worker × 50 threads (`gunicorn.conf.py`); the Docker image uses the same config. |
| P2-3 | ✅ | New `/admin/analytics/export` (XLSX). "Export CSV" now points at the working Excel export. |
| P2-4 | ✅ | `BRANCH_MANAGER` is assignable as "Location Manager" and requires a location. Editing a manager keeps the role selected. |
| P2-5 | ✅ | `/display/<code>` returns 404 for unknown or inactive codes. Only bare `/display` picks the first location. |
| P2-6 | ✅ | `notification_service.build_display_state` is used by both the push and the poll (a test asserts they're equal). |
| P2-7 | ✅ | Every non-admin role requires a location (API, admin form, manager form). |
| P2-8 | ✅ | Admins open manager pages for a chosen location (`?branch_id=`, remembered in the session; "Operations" button on the locations page). `/api/manager/*` requires `branch_id` for admins. |
| P2-9 | ✅ | Admin patient list is paginated (50) with search. The Excel export streams rows in batches with a write-only workbook. |
| P2-10 | ✅ | Reserved location codes (ADMIN, API, DISPLAY, REGISTER, …) are rejected. |
| P2-11 | ✅ | `token_called` is emitted to a per-user room (`user_<id>`), so a second tab or device of the same staff member shows the called patient. |

## Phase 3 — Security

| ID | Status | What changed |
|---|---|---|
| P3-1 | ✅ | `ProxyFix` with `TRUSTED_PROXY_COUNT` (production default 1, otherwise 0 so headers can't be spoofed). The login throttle has a hard limit per IP (20/5 min) and only a growing **delay** per account, so nobody can lock a colleague out. |
| P3-2 | ✅ | `queue_tokens.public_ref` (random, unique, back-filled). Public pages and the status/rate APIs use it; integer ids return 404 there. |
| P3-3 | ✅ | Public priority is stored as `priority_requested`; staff Confirm or Decline in the console. Emergency notice on the form. `ALLOW_SELF_DECLARED_PRIORITY=true` restores the old behaviour. |
| P3-4 | ✅ | nginx `limit_req` on `/api/queue/register` and `/appointments` (both configs). App limits: 3 tokens per mobile per day and 3 appointment requests per mobile per day. Not done: OTP. |
| P3-5 | ✅ | "Keep me signed in" checkbox (off by default). Sessions last 8 hours. Logout is POST-only and there's one logout route. Login page moved to `/auth/login` (old URL redirects). Change-password page added. `must_change_password` is set for passwords chosen by an admin (checkbox, on by default) or a manager. |
| P3-6 | ✅ | See the corrected table in PHASE-3. `pip-audit` is clean and runs in CI. |
| P3-7 | 🟡 | SRI hashes on every CDN asset (computed from the files, and the Bootstrap hashes match the official ones). A CSP is sent on every HTML page. **Limitation:** it still allows `'unsafe-inline'` scripts because templates contain inline `<script>` blocks and `onclick` handlers; moving them into files is the remaining step. |
| P3-8 | ✅ 👤 | Mobile numbers masked in all logs, `%s`-style logging, and message bodies logged only in debug mode. The EC2 script sets journald retention to 30 days. Existing journald logs on the server still contain full numbers. |
| P3-9 | 🟡 👤 | Patient-list views are audited (`view_patients`). `flask purge-patients --older-than DAYS` added. **Policy decisions needed:** which roles may see condition summaries, the retention period, and whether to encrypt the summary columns (not implemented). |
| P3-10 | ✅ | Socket handlers validate input. Staff-only room `staff_branch_<id>` requires a signed-in member of that location. Public rooms carry display data only. |

## Phase 4 — Consistency and cleanup

| ID | Status | What changed |
|---|---|---|
| P4-1 | ✅ | `app/services/metrics.py`: real served-vs-yesterday, average consultation time, average wait, change vs same weekday last week, per-officer time, "active" status, and "online now" (from the new `users.last_seen`). Dashboards show "—" when there's no data. |
| P4-2 | ✅ | `QueueService.estimate_wait` (position × average ÷ staff active on that service in the last hour) is used by the SMS, status API, token page, officer list and location info. The progress bar is relative to the patient's starting position. |
| P4-3 | 🟡 | `STAFF_ROLES`, `legacy_service_role()`, one SMS-provider normaliser, `app/utils/validators.py`, `app/services/user_service.py` (shared by API, admin and manager forms), one logout, one display builder. Not done: per-file `client` fixtures in the older tests. |
| P4-4 | ✅ | `complete_service(token_id, user, condition_summary)`. |
| P4-5 | 🟡 | Migration 010 adds the current role names next to the legacy ones in `service_types.allowed_roles`; `setup_test.py` deleted. The legacy mapping code stays because accounts with legacy roles may still exist. |
| P4-6 | 🟡 | People's Bank names removed from nginx, `.env.example` and migration 001. `.env.example` uses one sender ID. In production the app refuses to start if `textlk` is selected without a token, but only **warns** when `TEXTLK_SENDER_ID` is unset, to avoid taking a working site down on deploy. Internal `pb-*`/`PBQMS` identifiers are unchanged. |
| P4-7 | ✅ | Docker runs gunicorn with the same config. EC2 nginx has rate limits and optional certbot TLS (`DOMAIN=`). `git pull` failures now stop the script. `/healthz` checks the database, and CI deploy curls it. |
| P4-8 | ✅ | Deleted `queue-display.js`, `app/tasks`, the debug scripts and `requirements_old.txt`, plus a debug `print` in manager analytics. `setPriority` is now used. Reports moved to `docs/`; the README and EC2 guide are updated. |
| P4-9 | ✅ | `Query.get`/`get_or_404` replaced with `db.session.get`/`db.get_or_404`. `APP_ENV` replaces `FLASK_ENV` (still read as a fallback), and the unused `ENV` key is gone. The suite has 0 warnings (one third-party openpyxl warning is filtered). |
| P4-10 | 🟡 | conftest guard; a temporary database per process; tests for phone numbers, collisions, time zone, CLI with FKs, migration drift; MySQL CI job. Not done: merging the per-file fixtures into conftest. |
| P4-11 | ✅ | `status`/`is_priority`/`priority_reason` are NOT NULL. Counters can't be assigned to staff of another location (flush-time check). The sidebar display link only shows when a location is set. Service codes are already visible on the services page. |
| P4-12 | ✅ | Docker uses a named `qrcodes` volume. |

---

## Behaviour changes to tell staff about

- Sign-out is now a button, and there's a **Change password** link in the sidebar.
- New accounts (and admin password resets, unless unticked) must choose their own password at first sign-in.
- Staff can't call the next patient until the current one is completed or marked no-show. The console now has **Start consultation**.
- Patients' priority requests show as "Requested" with **Confirm / Decline** buttons; they don't jump the queue until confirmed.
- Appointment requests have **Schedule / Reject / Done** buttons.
- Patient token links in SMS-confirmation pages now look like `/register/token/<random-reference>`; old numeric links stop working.

## Deploy checklist

1. Back up the production database.
2. Set `SECRET_KEY`, `DATABASE_URL`, `BASE_URL`, `TRUSTED_PROXY_COUNT=1` and the SMS settings in the server `.env`.
3. Deploy. CI runs `flask db upgrade`, which applies migration `010`.
4. On EC2, re-run `deploy/ec2-setup.sh` once (with `DOMAIN=` for HTTPS) to install the cron jobs, rate limits and journald retention.
