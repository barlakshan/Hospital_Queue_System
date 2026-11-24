from flask import request, jsonify
from flask_login import login_required, current_user
from app.api import manager_bp
from app.models import (QueueToken, TokenStatus, UserRole, SatisfactionRating,
                          ServiceType, User, Counter)
from app.utils.decorators import audit_action, require_role
from app.extensions import db
from sqlalchemy import func
from datetime import timedelta
from app.models import OPEN_STATUSES, Branch
from app.utils.timeutil import local_today


def _branch_id_or_error():
    """Managers use their own location; admins must pass ?branch_id=."""
    if current_user.role != UserRole.ADMIN:
        return current_user.branch_id, None
    branch_id = request.args.get("branch_id", type=int)
    if not branch_id or not db.session.get(Branch, branch_id):
        return None, (jsonify({"error": "branch_id query parameter is required for admins"}), 400)
    return branch_id, None


@manager_bp.get("/dashboard")
@login_required
@require_role(UserRole.BRANCH_MANAGER, UserRole.ADMIN)
def dashboard():
    today     = local_today()
    branch_id, error = _branch_id_or_error()
    if error:
        return error
    total     = QueueToken.query.filter_by(branch_id=branch_id, queue_date=today).count()
    done      = QueueToken.query.filter_by(branch_id=branch_id, queue_date=today,
                                            status=TokenStatus.COMPLETED).count()
    waiting   = QueueToken.query.filter_by(branch_id=branch_id, queue_date=today,
                                            status=TokenStatus.WAITING).count()
    avg_r     = db.session.query(func.avg(SatisfactionRating.rating)).join(QueueToken).filter(
        QueueToken.branch_id == branch_id, QueueToken.queue_date == today).scalar()
    return jsonify({
        "total_today":     total,
        "completed":       done,
        "waiting":         waiting,
        "avg_rating":      round(float(avg_r or 0), 1),
        "completion_pct":  round(done / total * 100) if total else 0,
    })


@manager_bp.get("/analytics/today")
@login_required
@require_role(UserRole.BRANCH_MANAGER, UserRole.ADMIN)
def analytics_today():
    today     = local_today()
    branch_id, error = _branch_id_or_error()
    if error:
        return error
    by_service = (db.session.query(ServiceType.name, ServiceType.code,
                                    func.count(QueueToken.id))
                  .join(QueueToken, QueueToken.service_type_id == ServiceType.id)
                  .filter(QueueToken.branch_id == branch_id,
                           QueueToken.queue_date == today)
                  .group_by(ServiceType.id).all())
    return jsonify({
        "by_service": [{"service": n, "code": c, "count": cnt}
                       for n, c, cnt in by_service]
    })


@manager_bp.get("/analytics/range")
@login_required
@require_role(UserRole.BRANCH_MANAGER, UserRole.ADMIN)
def analytics_range():
    days      = min(max(request.args.get("days", 7, type=int), 1), 90)
    branch_id, error = _branch_id_or_error()
    if error:
        return error
    today     = local_today()
    result    = []
    for i in range(days - 1, -1, -1):
        d   = today - timedelta(days=i)
        cnt = QueueToken.query.filter_by(branch_id=branch_id, queue_date=d).count()
        result.append({"date": d.isoformat(), "count": cnt})
    return jsonify(result)


@manager_bp.get("/officers/performance")
@login_required
@require_role(UserRole.BRANCH_MANAGER, UserRole.ADMIN)
def officer_performance():
    today     = local_today()
    branch_id, error = _branch_id_or_error()
    if error:
        return error
    rows = (db.session.query(User,
                func.count(QueueToken.id).label("served"),
                func.avg(SatisfactionRating.rating).label("avg_rating"))
            .join(QueueToken, QueueToken.served_by == User.id)
            .outerjoin(SatisfactionRating, SatisfactionRating.token_id == QueueToken.id)
            .filter(User.branch_id == branch_id,
                    QueueToken.queue_date == today,
                    QueueToken.status == TokenStatus.COMPLETED)
            .group_by(User.id).all())
    return jsonify([{
        "id": u.id, "name": u.full_name, "role": u.role.value,
        "served": s, "avg_rating": round(float(r or 0), 1),
    } for u, s, r in rows])


@manager_bp.post("/token/<int:token_id>/transfer")
@login_required
@require_role(UserRole.BRANCH_MANAGER)
@audit_action("transfer_token")
def transfer_token(token_id):
    data    = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("counter_id"), int):
        return jsonify({"error": "counter_id (int) is required"}), 400
    token   = db.get_or_404(QueueToken, token_id)
    counter = db.get_or_404(Counter, data["counter_id"])
    # A manager may only move tokens within their own branch.
    if token.branch_id != current_user.branch_id or counter.branch_id != current_user.branch_id:
        return jsonify({"error": "Access denied"}), 403
    if token.status not in OPEN_STATUSES:
        return jsonify({"error": f"Token is {token.status.value} and cannot be transferred"}), 409
    token.counter_id = counter.id
    db.session.commit()
    return jsonify({"message": f"Token {token.token_number} transferred to {counter.label}"})


@manager_bp.get("/customers")
@login_required
@require_role(UserRole.BRANCH_MANAGER, UserRole.ADMIN)
def branch_customers():
    from app.models import Customer
    branch_id, error = _branch_id_or_error()
    if error:
        return error
    page  = request.args.get("page", 1, type=int)
    rows  = (db.session.query(Customer, func.count(QueueToken.id).label("visits"))
             .join(QueueToken, QueueToken.customer_id == Customer.id)
             .filter(QueueToken.branch_id == branch_id)
             .group_by(Customer.id)
             .order_by(func.count(QueueToken.id).desc())
             .paginate(page=page, per_page=30, error_out=False))
    return jsonify({
        "items": [{"name": c.full_name, "mobile": c.mobile, "visits": v}
                  for c, v in rows.items],
        "total": rows.total,
    })
