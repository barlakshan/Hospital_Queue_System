# Phase 0 — Urgent (do first, today)

These cause harm right now or block every later phase from shipping.

---

## P0-1 · The test suite sends real SMS with the live Text.lk token
**Severity:** Critical · **Status:** Confirmed (reproduced)

**Where:** [app/config.py:88](../../app/config.py#L88) (`TestingConfig`), [tests/test_uat_flows.py](../../tests/test_uat_flows.py)

**Problem:**
- `TestingConfig` inherits `SMS_PROVIDER` from the environment.
- With the local `.env` present, `create_app("testing").config["SMS_PROVIDER"]` resolves to **`textlk`**. I checked this.
- The UAT tests register patients with `0771234567`, `0771234568`, `0771234570` and similar numbers.
- Each registration and each call-next starts a background thread that calls `SmsService._dispatch`, which posts to `https://app.text.lk/...` with the real token.

**Impact:**
- Every local test run can send dozens of real SMS to numbers that probably belong to real people.
- Each run costs SMS credit.
- The messages carry hospital branding, so they look like spam from the hospital.

**Fix:**
```python
# app/config.py
class TestingConfig(BaseConfig):
    TESTING                 = True
    SMS_PROVIDER            = "mock"          # never hit a real gateway from tests
    TEXTLK_API_TOKEN        = ""
    ...
```
Add a guard as well, so a later config change can't undo this:
```python
# tests/conftest.py (new)
import pytest
from app.services.sms_service import SmsService

@pytest.fixture(autouse=True)
def _no_real_sms(monkeypatch):
    monkeypatch.setattr(SmsService, "_dispatch",
                        classmethod(lambda cls, m, msg: {"message_id": "test"}))
```
**Also:** check the Text.lk dashboard for sends to `07712345xx`, and rotate the token (see P0-4).

**Verify:** `python -c "from app import create_app;print(create_app('testing').config['SMS_PROVIDER'])"` prints `mock`.

---

## P0-2 · CI fails on a clean checkout, so deploys never run
**Severity:** Critical · **Status:** Confirmed (reproduced)

**Where:** `setup_test.py`, `test_analytics_template.py`, `debug_analytics.py` (repo root). There is no `pytest.ini`.

**Problem:**
- With no pytest config, pytest collects every `test_*.py` **and `*_test.py`** file from the repo root.
- `setup_test.py` and `test_analytics_template.py` are scripts that call `create_app('development')` and query the dev database **at import time**.
- On a developer machine `pbqms_dev.sqlite3` exists, so collection succeeds.
- On a fresh CI runner it doesn't exist. Reproduced with an empty DB:
  ```
  ERROR collecting setup_test.py
  sqlite3.OperationalError: no such table: service_types
  ```
- The `deploy` job in [.github/workflows/deploy.yml](../../.github/workflows/deploy.yml) has `needs: test`, so it never runs.

**Fix:**
1. Move the ad-hoc scripts out of test discovery: `git mv setup_test.py test_analytics_template.py debug_analytics.py scripts/`. Or delete them; they duplicate `flask seed-db`.
2. Pin discovery:
   ```ini
   # pytest.ini
   [pytest]
   testpaths = tests
   ```

**Verify:** `DATABASE_URL=sqlite:////tmp/empty.db python -m pytest --collect-only -q` collects 44 tests with no errors.

---

## P0-3 · The round-1 security fixes are only in the working tree
**Severity:** High · **Status:** Confirmed (`git status`)

All round-1 changes are uncommitted: the XSS fix, cross-branch authorisation, session revocation, CSRF origin check, `.gitignore`, and untracking `.env`/`mestal.pem`. Production still runs the vulnerable code.

**Fix:**
1. Do P0-1 and P0-2 first, so CI can pass.
2. Commit everything on a branch and open a PR.
3. Before deploying, set these on the server:
   - `SECRET_KEY`: 32 or more random characters. The app now refuses to start without it.
   - `BASE_URL`: the exact public HTTPS origin. Socket.IO now only accepts that origin.
   - `DB_PASSWORD` and `DB_ROOT_PASSWORD`, if you use docker-compose.

---

## P0-4 · Secrets in git history must be rotated
**Severity:** Critical · **Status:** Confirmed (git history)

These are already untracked, but they remain in history:
- `mestal.pem` (RSA private key), in 4 commits
- `.env`, including the live `TEXTLK_API_TOKEN` and `SECRET_KEY`

**Fix:**
1. Revoke the key pair on EC2 (`~/.ssh/authorized_keys`) and issue a new one.
2. Rotate the Text.lk token and set a new `SECRET_KEY`.
3. If the repository was ever pushed or shared, purge the files from history:
   ```bash
   git filter-repo --path mestal.pem --path .env --invert-paths
   ```
   Then force-push. Every collaborator must re-clone.

---

## P0-5 · Production may be running on SQLite without anyone knowing
**Severity:** High · **Status:** Needs verification (code confirmed, server state unknown)

**Where:** [app/config.py:79](../../app/config.py#L79)

**Problem:**
- `ProductionConfig.SQLALCHEMY_DATABASE_URI` falls back to `pbqms_dev.sqlite3` when `DATABASE_URL` is unset.
- The committed `.env` had `DATABASE_URL` commented out.
- If the EC2 `.env` was copied from it, production is SQLite, and several gunicorn workers share one SQLite file:
  - concurrent writes fail with `database is locked`;
  - row locks (`with_for_update`) are ignored, which is part of P1-3;
  - the data lives in a file the deploy job only avoids deleting through an rsync exclude.

**Check now (on EC2):**
```bash
grep DATABASE_URL /home/ubuntu/pbqms/.env; ls -la /home/ubuntu/pbqms/*.sqlite3
```

**Fix:** fail fast instead of falling back.
```python
class ProductionConfig(BaseConfig):
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")
# in create_app(), production branch:
if not app.config.get("SQLALCHEMY_DATABASE_URI"):
    raise RuntimeError("DATABASE_URL must be set in production")
```
If production *is* on SQLite, plan a migration to MySQL with `flask db upgrade` against an empty MySQL database, then copy the data. Test the MySQL migrations first (see P1-10).
