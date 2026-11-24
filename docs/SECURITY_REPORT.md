# Security Audit Report — Hospital Queue System

Scope: full repository (Flask app, JS, templates, migrations, Docker/nginx/CI, git contents).
Method: manual code review plus a regression test suite. Tests: 32 existing + 12 new, all passing.
Not done: dynamic scanning, dependency CVE scan (no network/`pip-audit`), review of production server state.

## 1. Needs your action (cannot be fixed from code)

| # | Item | What to do |
|---|------|-----------|
| A | **`mestal.pem` (RSA private key) was committed** and is in git history (4+ commits). | Treat the key as compromised. Remove it from the EC2 `authorized_keys`, generate a new keypair, and purge history (`git filter-repo --path mestal.pem --invert-paths`) if the repo is or was ever shared/pushed. |
| B | **`.env` was committed**, including a live `TEXTLK_API_TOKEN` and the `SECRET_KEY`. | Rotate the Text.lk token; set a new random `SECRET_KEY` (`python -c "import secrets;print(secrets.token_hex(32))"`) on the server. Rotating the key logs everyone out. |
| C | `docker-compose.yml` had hard-coded DB passwords and published MySQL/Redis on all interfaces. | Fixed in code (see below), but if that stack was ever run on a public host, rotate DB credentials and check Redis/MySQL for access. |
| D | `.github/workflows/deploy.yml` runs deploy on a **self-hosted runner**. | Make sure the repo is private or fork-PR workflows are disabled; self-hosted runners on public repos allow arbitrary code execution. Consider pinning actions by commit SHA. |

I removed these files from the git index but did **not** rewrite history or commit. Nothing is committed yet.

## 2. Findings fixed

### Critical / High
1. **Stored XSS in the staff console** — `static/js/officer-console.js` put public, unauthenticated input (customer name) into `innerHTML`. Anyone could register as `<img onerror=…>` and run script in an officer's session. All interpolated values are now escaped; the officer name on the customer page now uses `textContent`.
2. **Broken access control (cross-branch IDOR)** — `start / complete / no-show / priority` accepted any token ID. Staff from branch B could alter branch A's tokens, and could re-open completed ones. `QueueService.load_token_for_staff` now enforces branch, assigned service, and valid state transitions (403/409).
3. **Manager token transfer** had no branch check. A manager could move any branch's token to any branch's counter. Now both must belong to the manager's branch.
4. **Counter creation** let a manager create counters in any branch and bound them to arbitrary users. Now scoped to own branch and same-branch staff.
5. **Deactivated users stayed logged in** (session and 'remember me' cookie). `load_user` now rejects inactive accounts.
6. **No CSRF protection** on state-changing endpoints. Added an Origin/Referer same-origin check for all non-GET requests, plus `SameSite=Lax`/`HttpOnly` cookies in every config.
7. **Committed secrets / no `.gitignore`** — added `.gitignore`; untracked `.env`, the PEM, SQLite DBs, `__pycache__`, generated QR files. Deleted `tmp_*.py` scripts that created accounts with password `secret123`.

### Medium
8. **Path traversal via branch code** — the code becomes `static/qrcodes/<code>.png`; the API accepted `../../x`. Codes now validated (`[A-Z0-9_-]{2,10}`) in API, form, and generator.
9. **No brute-force protection on login** — per IP+email lockout (5 failures / 5 min → 429). In-process only; the nginx `limit_req` zone added to `nginx/pbqms.conf` is the hard limit across workers.
10. **WebSocket CORS was `*`** — now same-origin, or `BASE_URL` when set.
11. **Weak password handling** — admin/manager create and update paths accepted any password. One shared `validate_password` (8–128 chars) now applies everywhere.
12. **Admin lock-out** — an admin could deactivate or demote themselves. Blocked.
13. **Customer token page** exposed the first 6 digits of the patient's phone to anyone with the sequential URL. Now shows only the last 2.
14. **Ratings** could be submitted on unfinished visits, with unbounded comments. Now completed-only, 500 chars.
15. **Unbounded public input** — `custom_reason`, names and mobile numbers were unbounded or weakly validated (DB-column overflow errors). Now length-limited; mobile must normalise to `+` and 9–15 digits.
16. **Empty/weak `SECRET_KEY`** — production check no longer rejects valid keys containing "dev" but now requires 32+ chars; non-production without a key gets an ephemeral random one rather than failing or running unsigned.
17. **Docker/compose** — MySQL/Redis no longer published; DB passwords come from `.env`; web bound to 127.0.0.1; container runs as non-root.

### Bugs (functional)
18. **`/api/admin/qr/<id>` (GET and POST) always returned 500** — `current_user` was used but never imported (`NameError`). Fixed. These endpoints also leaked the server filesystem path; they now return a `/static/...` URL.
19. **500 responses leaked exception text** (`str(e)`) in register, call-next, priority, rating. Now logged server-side with a generic message.
20. **Uncaught crashes** on malformed input: non-dict JSON bodies (`force=True` + `.get`), non-string `priority_reason`, non-integer `branch_id` in the user form, duplicate email/employee ID on edit (IntegrityError). All now return 4xx.
21. **`analytics_range?days=` and `per_page`** unbounded (a single request could fire millions of queries). Capped at 90 / 200.
22. **Race in `call_next`** — two officers could be handed the same token; now `SELECT … FOR UPDATE SKIP LOCKED` (effective on MySQL; no-op on SQLite).
23. Removed unused `Flask-Cors` dependency; added security headers (`nosniff`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `no-store` on authenticated pages); nginx now has HSTS, TLS ≥1.2, body-size limit, `X-Forwarded-For`.

## 3. Open issues (not changed — need a product/design decision)

| Severity | Issue | Recommendation |
|----------|-------|----------------|
| Medium | **Patients can self-declare priority** (even "emergency") on the public form, jumping the queue. | Make priority staff-assigned, or require triage confirmation. |
| Medium | **Public token pages/APIs use sequential IDs** (`/register/token/<id>`, `/api/queue/token/<id>/status`, `/rate`). Anyone can enumerate other patients' queue status, service and the officer name. | Use unguessable per-token identifiers (e.g. `secrets.token_urlsafe`) in URLs. |
| Medium | **No rate limit on public endpoints** (`/register`, `/appointments`). Each registration can send an SMS (cost + SMS-bombing of any phone number). | Add Flask-Limiter or nginx `limit_req`; add CAPTCHA or OTP phone verification. |
| Medium | **Logout is a GET link** (`/auth/logout`) — cross-site logout is possible. | Convert to a POST form button. |
| Low | **No Content-Security-Policy.** Templates rely on inline scripts and CDNs. | Move scripts to files, add SRI for CDN assets, then add a CSP. |
| Low | `Branch.qr_payload` falls back to the request `Host` header when `BASE_URL` is empty. | Require `BASE_URL` in production. |
| Low | `audit_action` logs a call even when it was rejected (403/409), and customer/patient health summaries (PII/PHI) are stored and exported (`customers/export`) in clear text. | Log result status; decide retention/encryption; export is audited already. |
| Low | `docker-compose` still runs `python wsgi.py` with Werkzeug `allow_unsafe_werkzeug`; Redis has no auth; `DEBUG` is on whenever `FLASK_ENV` is unset (default is *development*). | Run gunicorn in the container; default to production when unset. |
| Info | Dashboards show hard-coded numbers (`served_delta: 3`, `avg_service_min: 8`, `avg_wait: 18`, `delta_pct: 8`). | Replace with real calculations or remove. |
| Info | Dependencies were not CVE-scanned. | Run `pip-audit -r requirements.txt` in CI. |

## 4. Files changed

Code: `app/__init__.py`, `app/config.py`, `app/extensions.py`, `app/api/{auth,admin,manager,queue}.py`, `app/services/queue_service.py`, `app/views/{admin_view,manager_view,customer}.py`, `app/utils/qr_generator.py`, `static/js/officer-console.js`, `app/templates/customer/token.html`.
Infra: `.gitignore` (new), `Dockerfile`, `docker-compose.yml`, `nginx/pbqms.conf`, `requirements.txt`.
Tests: `tests/test_security_audit.py` (new, 12 tests).

**Deploy note:** `docker-compose.yml` now requires `DB_PASSWORD` and `DB_ROOT_PASSWORD` in `.env`. Production needs `SECRET_KEY` of 32+ characters and `BASE_URL` set to the public HTTPS origin (required for WebSocket CORS).
