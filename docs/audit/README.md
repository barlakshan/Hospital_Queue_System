# Hospital Queue System — Full Audit (Bugs, Inconsistencies, Integrity)

**Date:** 2026-11-23  
**Scope:** the whole repository as it stands in the working tree. That includes the security fixes from the first audit round ([SECURITY_REPORT.md](../SECURITY_REPORT.md)), which are **not committed yet**.  
**Audience:** developers who will implement the fixes.

> **Update 2026-11-24:** the fixes have been implemented. See
> [IMPLEMENTATION-STATUS.md](IMPLEMENTATION-STATUS.md) for what changed per
> finding, what still needs a person (secret rotation, server checks, policy
> decisions), and the deploy checklist.

## How findings were verified

Each finding carries one of three status labels:

| Label | Meaning |
|---|---|
| **Confirmed (reproduced)** | I ran code to show it: a script, test, CLI run, schema diff or route map. |
| **Confirmed (code)** | Clear from reading the code; not executed. |
| **Needs verification** | Likely, but depends on an environment I couldn't run, such as MySQL or the production server. |

Tools used:
- the test suite (44 tests, all passing);
- an Alembic `compare_metadata` diff between a migrated database and the models;
- the Flask route map compared with every `fetch()`, `href` and form `action` in the templates and JS;
- direct runs of the phone normaliser, the Celery dispatch, the CLI commands and `pytest --collect-only` against an empty DB.

## Phases

Do the phases in order. Each one is safe to ship on its own.

| Phase | File | What it covers | Findings |
|---|---|---|---|
| **0** | [PHASE-0-urgent.md](PHASE-0-urgent.md) | Stop active harm: real SMS from tests, broken CI, uncommitted fixes, secrets | 5 |
| **1** | [PHASE-1-data-integrity.md](PHASE-1-data-integrity.md) | Wrong or corrupted data: phone numbers, patient records, token numbering, time zones, schema drift, orphaned rows | 15 |
| **2** | [PHASE-2-functional-bugs.md](PHASE-2-functional-bugs.md) | Features that don't work: Celery, Socket.IO under gunicorn, dead links, unmanageable roles, display fallback | 11 |
| **3** | [PHASE-3-security.md](PHASE-3-security.md) | Security left after round 1: proxy IPs, enumeration, rate limits, dependencies, PII in logs | 10 |
| **4** | [PHASE-4-consistency-cleanup.md](PHASE-4-consistency-cleanup.md) | Inconsistencies, fake metrics, duplicated logic, dead code, branding, docs, test hygiene | 12 |

## Top 10 by impact

| ID | Severity | Finding |
|---|---|---|
| P0-1 | **Critical** | Running the test suite sends **real SMS** through Text.lk with the live token to real `07712345xx` numbers. |
| P0-2 | **Critical** | CI fails on a clean checkout (pytest imports root scripts that query a DB that doesn't exist), so **the deploy job never runs**. |
| P1-1 | **High** | Phone normalisation corrupts common Sri Lankan inputs: `771234567` becomes `+771234567` (Russia/Kazakhstan). SMS goes to the wrong country. |
| P1-2 | **High** | Anyone can rename an existing patient's master record by registering with that patient's mobile number. |
| P2-2 | **High** | Socket.IO does not work reliably under the production gunicorn config (two or more workers, no sticky sessions, no message queue). |
| P2-1 | **High** | Celery is completely non-functional: incompatible pins, no app instance, no Flask context, nothing scheduled. |
| P1-3 | **High** | Two registrations in the same second can collide on a token number, so one patient gets a 500 error. |
| P1-4 | **High** | Times are stored in UTC but shown as if local, so staff screens are off by 5½ hours. The queue "day" follows the server's date, not Sri Lanka's. |
| P3-1 | **High** | Behind nginx every request appears to come from 127.0.0.1. Five bad passwords from anyone lock that staff account for everyone, and audit-log IPs are useless. |
| P1-9 | **Medium** | Models and migrations have drifted. The next `flask db migrate` would generate a migration that **drops 7 production indexes**. |

## Severity scale

- **Critical:** causing harm now (money, privacy, deploys blocked).
- **High:** wrong data or a broken core feature in normal use.
- **Medium:** wrong under specific but realistic conditions.
- **Low:** edge cases, maintainability, misleading UI.
