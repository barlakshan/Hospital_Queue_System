import time
from threading import Lock

from flask import (current_app, flash, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_login import current_user, login_required, login_user

from app.api import auth_bp
from app.extensions import db
from app.models import User
from app.services.audit_service import AuditService
from app.utils.timeutil import utcnow
from app.utils.validators import validate_password  # noqa: F401  (re-exported)

# Login throttling (in-process; each gunicorn worker keeps its own counters,
# nginx `limit_req` provides the hard outer limit):
#   * per client IP: a hard lock after LOGIN_IP_MAX_FAILURES failures, so one
#     source can't brute-force many accounts;
#   * per account: only a growing *delay*, never a lock, so nobody can lock a
#     colleague out by typing wrong passwords for their email.
_ip_failures = {}
_account_failures = {}
_lock = Lock()


def _window():
    return current_app.config.get("LOGIN_IP_LOCK_SECONDS", 300)


def _ip_locked(ip):
    with _lock:
        count, first = _ip_failures.get(ip, (0, 0))
        if count and time.time() - first >= _window():
            _ip_failures.pop(ip, None)
            return False
        return count >= current_app.config.get("LOGIN_IP_MAX_FAILURES", 20)


def _record_failure(ip, email):
    now = time.time()
    with _lock:
        count, first = _ip_failures.get(ip, (0, now))
        _ip_failures[ip] = (count + 1, first)
        acc_count, acc_first = _account_failures.get(email, (0, now))
        if now - acc_first >= _window():
            acc_count, acc_first = 0, now
        _account_failures[email] = (acc_count + 1, acc_first)
        return acc_count + 1


def _account_delay(failures):
    cap = current_app.config.get("LOGIN_FAILURE_DELAY_MAX", 5)
    if failures <= 3 or cap <= 0:
        return 0
    return min(cap, 2 ** (failures - 4))


def _clear_failures(ip, email):
    with _lock:
        _ip_failures.pop(ip, None)
        _account_failures.pop(email, None)


def reset_login_throttle():
    """For tests."""
    with _lock:
        _ip_failures.clear()
        _account_failures.clear()


@auth_bp.route("/login-page", methods=["GET"])
def login_page():
    """Old URL; the login page now lives at /auth/login."""
    return redirect(url_for("auth_view.login_page"), code=301)


@auth_bp.post("/login")
def login():
    # Support both JSON (API) and form (browser)
    if request.is_json:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "A JSON object is required"}), 400
    else:
        data = request.form

    email    = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")
    remember = str(data.get("remember") or "").lower() in {"1", "true", "on", "yes"}
    ip = request.remote_addr or "unknown"

    def fail(message, code):
        if request.is_json:
            return jsonify({"error": message}), code
        flash(message, "danger")
        return redirect(url_for("auth_view.login_page"))

    if _ip_locked(ip):
        return fail("Too many failed attempts. Try again in a few minutes.", 429)

    user = User.query.filter_by(email=email, is_active=True).first()
    if not user or not user.check_password(password):
        failures = _record_failure(ip, email)
        AuditService.log(user_id=user.id if user else None, action="login_failed",
                         payload={"email": email[:120]})
        delay = _account_delay(failures)
        if delay:
            time.sleep(delay)
        return fail("Invalid email or password.", 401)

    _clear_failures(ip, email)
    session.clear()
    login_user(user, remember=remember)
    # Without "keep me signed in" the session ends after one shift.
    session.permanent = True
    user.last_login = utcnow()
    user.last_seen = user.last_login
    db.session.commit()
    AuditService.log(user_id=user.id, action="login_success",
                     payload={"remember": remember})

    if request.is_json:
        return jsonify({
            "user_id":   user.id,
            "role":      user.role.value,
            "name":      user.full_name,
            "branch_id": user.branch_id,
            "must_change_password": user.must_change_password,
        })

    if user.must_change_password:
        return redirect(url_for("auth_view.change_password"))
    from app import home_endpoint_for
    return redirect(url_for(home_endpoint_for(user)))


@auth_bp.get("/me")
@login_required
def me():
    return jsonify({
        "id":        current_user.id,
        "name":      current_user.full_name,
        "email":     current_user.email,
        "role":      current_user.role.value,
        "branch_id": current_user.branch_id,
        "branch":    current_user.branch.name if current_user.branch else None,
        "must_change_password": current_user.must_change_password,
    })


def change_user_password(user, current_password, new_password):
    """Shared by the API and the HTML form. Returns an error message or None."""
    if not user.check_password(str(current_password or "")):
        return "Current password is incorrect"
    error = validate_password(new_password)
    if error:
        return error
    if user.check_password(new_password):
        return "Choose a password different from the current one"
    user.set_password(new_password)
    user.must_change_password = False
    db.session.commit()
    AuditService.log(user_id=user.id, action="change_password")
    return None


@auth_bp.post("/change-password")
@login_required
def change_password():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "A JSON object is required"}), 400
    error = change_user_password(current_user, data.get("current_password"),
                                 data.get("new_password", ""))
    if error:
        return jsonify({"error": error}), 400
    return jsonify({"message": "Password changed successfully"})
