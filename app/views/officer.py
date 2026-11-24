from flask import render_template
from flask_login import login_required, current_user
from app.views import officer_bp
from app.models import (
    DOCTOR_ROLES, STAFF_ROLES, AppointmentRequest, AppointmentStatus, Counter,
    QueueToken, SatisfactionRating, ServiceType, TokenStatus,
)
from app.services import metrics
from app.services.notification_service import serving_payload
from app.services.queue_service import QueueService
from app.utils.decorators import require_role
from app.utils.timeutil import local_now, local_today, to_local
from app.extensions import db
from sqlalchemy import func


@officer_bp.route("/dashboard")
@login_required
@require_role(*STAFF_ROLES)
def dashboard():
    today      = local_today()
    branch_id  = current_user.branch_id
    counter    = Counter.query.filter_by(user_id=current_user.id, is_active=True).first()

    # Allow officers without an assigned counter to serve from the dashboard.
    if not counter:
        counter = type("Counter", (), {"id": None, "label": "Not assigned"})()

    services = ServiceType.query.filter(
        ServiceType.code.in_(current_user.allowed_service_codes)
    ).order_by(ServiceType.name).all()
    service_ids = [service.id for service in services]

    summary = metrics.officer_summary(current_user, today)

    waiting = QueueToken.query.filter_by(
        branch_id=branch_id, status=TokenStatus.WAITING, queue_date=today,
    ).filter(QueueToken.service_type_id.in_(service_ids)).count()

    priority_waiting = QueueToken.query.filter_by(
        branch_id=branch_id, status=TokenStatus.WAITING,
        queue_date=today, is_priority=True,
    ).filter(QueueToken.service_type_id.in_(service_ids)).count()

    avg_rating_v = db.session.query(func.avg(SatisfactionRating.rating)).join(QueueToken).filter(
        QueueToken.branch_id == branch_id,
        QueueToken.served_by == current_user.id,
        QueueToken.queue_date == today,
    ).scalar()
    rating_count = (db.session.query(func.count(SatisfactionRating.id))
                    .join(QueueToken)
                    .filter(QueueToken.served_by == current_user.id, QueueToken.queue_date == today)
                    .scalar()) or 0

    # Hourly chart — last 8 hours, in the hospital's local time.
    current_hour  = local_now().hour
    start_hour    = max(8, current_hour - 7) if current_hour >= 8 else 0

    completed_today = (QueueToken.query
                       .filter(
                           QueueToken.branch_id == branch_id,
                           QueueToken.served_by == current_user.id,
                           QueueToken.queue_date == today,
                           QueueToken.status == TokenStatus.COMPLETED,
                           QueueToken.completed_at.isnot(None),
                       ).all())
    counts_by_hour = {}
    for t in completed_today:
        hour = to_local(t.completed_at).hour
        counts_by_hour[hour] = counts_by_hour.get(hour, 0) + 1

    hourly_labels = []
    hourly_data   = []
    for h in range(start_hour, current_hour + 1):
        if h == 0:
            label = "12am"
        elif h < 12:
            label = f"{h}am"
        elif h == 12:
            label = "12pm"
        else:
            label = f"{h-12}pm"
        hourly_labels.append(label)
        hourly_data.append(counts_by_hour.get(h, 0))
    peak_hour = hourly_data.index(max(hourly_data)) if hourly_data and max(hourly_data) > 0 else 0

    recent = (QueueToken.query
              .filter_by(served_by=current_user.id, queue_date=today, status=TokenStatus.COMPLETED)
              .order_by(QueueToken.completed_at.desc())
              .limit(8).all())

    appointment_requests = []
    if services or current_user.role in DOCTOR_ROLES:
        appointment_requests = (
            AppointmentRequest.query.filter(
                AppointmentRequest.branch_id == branch_id,
                AppointmentRequest.service_type_id.in_(service_ids),
                AppointmentRequest.status.in_(AppointmentStatus.OPEN),
            )
            .order_by(AppointmentRequest.created_at.desc())
            .limit(20)
            .all()
        )

    open_token = QueueService.open_token_for(current_user)
    stats = {
        **summary,
        "waiting": waiting, "priority_waiting": priority_waiting,
        "avg_rating": round(float(avg_rating_v), 1) if avg_rating_v is not None else None,
        "rating_count": rating_count,
    }
    return render_template("officer/dashboard.html",
        counter=counter, branch=current_user.branch,
        stats=stats, recent_tokens=recent,
        appointment_requests=appointment_requests,
        appointment_services=services,
        hourly_labels=hourly_labels, hourly_data=hourly_data, peak_hour=peak_hour,
        current_token=serving_payload(open_token) if open_token else None,
    )


@officer_bp.route("/queue")
@login_required
@require_role(*STAFF_ROLES)
def queue():
    service_ids = [s.id for s in ServiceType.query.filter(
        ServiceType.code.in_(current_user.allowed_service_codes)).all()]
    tokens = (QueueToken.query
              .filter_by(branch_id=current_user.branch_id,
                         status=TokenStatus.WAITING,
                         queue_date=local_today())
              .filter(QueueToken.service_type_id.in_(service_ids))
              .order_by(QueueToken.is_priority.desc(), QueueToken.id.asc())
              .all())

    return render_template("officer/queue.html",
        branch=current_user.branch,
        tokens=tokens,
        waiting_count=len(tokens),
    )


@officer_bp.route("/history")
@login_required
@require_role(*STAFF_ROLES)
def history():
    completed = (QueueToken.query
                 .filter_by(branch_id=current_user.branch_id,
                            served_by=current_user.id,
                            status=TokenStatus.COMPLETED,
                            queue_date=local_today())
                 .order_by(QueueToken.completed_at.desc())
                 .limit(40).all())

    return render_template("officer/history.html",
        branch=current_user.branch,
        completed=completed,
    )
