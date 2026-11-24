from functools import wraps
from flask import abort, request, jsonify
from flask_login import current_user
from app.models import UserRole


def require_role(*roles):
    """Usage: @require_role(UserRole.ADMIN, UserRole.BRANCH_MANAGER)"""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated:
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"error": "Authentication required"}), 401
                abort(401)
            if current_user.role not in roles:
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"error": "Insufficient permissions"}), 403
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator


def _status_of(result):
    if isinstance(result, tuple) and len(result) > 1 and isinstance(result[1], int):
        return result[1]
    return getattr(result, "status_code", 200)


def audit_action(action_name: str):
    """Record a state-changing request in audit_logs, including its outcome.

    GET/HEAD requests are not recorded. The HTTP status is stored so a
    rejected attempt (4xx) can be told apart from a completed action, and a
    failure raised by the view (e.g. a 403/409 error handler) is recorded
    before it propagates.
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if request.method in ("GET", "HEAD"):
                return f(*args, **kwargs)
            status = 500
            try:
                result = f(*args, **kwargs)
                status = _status_of(result)
                return result
            except Exception as exc:
                status = getattr(exc, "status_code", None) or getattr(exc, "code", None) or 500
                raise
            finally:
                try:
                    from app.services.audit_service import AuditService
                    from app.extensions import db
                    db.session.rollback() if status >= 400 else None
                    AuditService.log(
                        user_id=current_user.id if current_user.is_authenticated else None,
                        action=action_name,
                        token_id=kwargs.get("token_id"),
                        payload={
                            "url": request.path,
                            "method": request.method,
                            "status": status,
                            "outcome": "ok" if status < 400 else "rejected",
                            "target": {k: v for k, v in kwargs.items()
                                       if isinstance(v, (int, str))},
                        },
                    )
                except Exception:
                    pass
        return decorated
    return decorator
