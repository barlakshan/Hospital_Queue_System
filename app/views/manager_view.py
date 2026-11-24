from flask import abort, render_template, request, redirect, session, url_for, flash
from flask_login import login_required, current_user
from app.views import manager_view_bp
from app.models import (QueueToken, TokenStatus, User, UserRole,
                        ServiceType, SatisfactionRating, Branch, Customer)
from app.services import metrics, user_service
from app.services.audit_service import AuditService
from app.services.user_service import UserInputError
from app.utils.decorators import audit_action, require_role
from app.utils.timeutil import local_today
from app.extensions import db
from sqlalchemy import func
from datetime import timedelta
from sqlalchemy.exc import IntegrityError

_MANAGER_STAFF_ROLES = (UserRole.OPD, UserRole.PHARMACY)


def _scoped_branch():
    """The location these pages operate on.

    Managers always see their own location. Admins (who have none) pick one
    with ?branch_id=, which is remembered for the rest of their session.
    """
    if current_user.role != UserRole.ADMIN:
        return current_user.branch
    branch_id = request.args.get("branch_id", type=int) or session.get("manager_branch_id")
    branch = db.session.get(Branch, branch_id) if branch_id else None
    if branch:
        session["manager_branch_id"] = branch.id
    return branch


def _choose_branch():
    flash("Choose a hospital location to open its operations pages.", "warning")
    return redirect(url_for("admin_view.branches"))


@manager_view_bp.route("/dashboard")
@login_required
@require_role(UserRole.BRANCH_MANAGER, UserRole.ADMIN)
def dashboard():
    branch = _scoped_branch()
    if branch is None:
        return _choose_branch()
    today     = local_today()
    branch_id = branch.id

    total     = QueueToken.query.filter_by(branch_id=branch_id, queue_date=today).count()
    completed = QueueToken.query.filter_by(branch_id=branch_id, queue_date=today,
                                           status=TokenStatus.COMPLETED).count()
    waiting   = QueueToken.query.filter_by(branch_id=branch_id, queue_date=today,
                                           status=TokenStatus.WAITING).count()
    avg_r_v   = db.session.query(func.avg(SatisfactionRating.rating)).join(QueueToken).filter(
        QueueToken.branch_id == branch_id, QueueToken.queue_date == today).scalar()
    r_count   = (db.session.query(func.count(SatisfactionRating.id)).join(QueueToken).filter(
        QueueToken.branch_id == branch_id, QueueToken.queue_date == today).scalar()) or 0

    priority_tokens = (QueueToken.query
                       .filter_by(branch_id=branch_id, queue_date=today, is_priority=True)
                       .order_by(QueueToken.issued_at.desc()).limit(20).all())

    waiting_tokens = (QueueToken.query
                      .filter_by(branch_id=branch_id, queue_date=today, status=TokenStatus.WAITING)
                      .order_by(QueueToken.is_priority.desc(), QueueToken.id.asc()).all())

    officers_raw = (db.session.query(User,
                        func.count(QueueToken.id).label("served"),
                        func.avg(SatisfactionRating.rating).label("avg_rating"))
                    .join(QueueToken, QueueToken.served_by == User.id)
                    .outerjoin(SatisfactionRating, SatisfactionRating.token_id == QueueToken.id)
                    .filter(User.branch_id == branch_id,
                            QueueToken.queue_date == today,
                            QueueToken.status == TokenStatus.COMPLETED)
                    .group_by(User.id).all())

    officers = [{"user": u, "served": s,
                 "avg_rating": round(float(r), 1) if r is not None else None,
                 "avg_time": metrics.officer_avg_minutes(u.id, today),
                 "is_active": metrics.is_recently_active(u)}
                for u, s, r in officers_raw]

    svc_rows = (db.session.query(ServiceType.name, ServiceType.code,
                                 func.count(QueueToken.id))
                .join(QueueToken, QueueToken.service_type_id == ServiceType.id)
                .filter(QueueToken.branch_id == branch_id, QueueToken.queue_date == today)
                .group_by(ServiceType.id).all())

    colour_map = {"G": "#087f8c", "S": "#21a5a8", "L": "#99c630",
                  "R": "#FFCB47", "P": "#4c4e54", "B": "#d1d2d5"}
    service_stats = [{"name": n, "code": c, "count": cnt,
                      "colour": colour_map.get(c, "#888")}
                     for n, c, cnt in svc_rows]

    return render_template("manager/dashboard.html",
        branch=branch, today=today,
        stats={
            "total": total, "completed": completed, "waiting": waiting,
            "completion_pct": round(completed / total * 100) if total else 0,
            "avg_wait": metrics.avg_wait_minutes(branch_id, today),
            "avg_rating": round(float(avg_r_v), 1) if avg_r_v is not None else None,
            "rating_count": r_count,
            "delta_pct": metrics.branch_day_comparison(branch_id, today),
        },
        priority_tokens=priority_tokens, waiting_tokens=waiting_tokens,
        officers=officers, service_stats=service_stats,
        service_labels=[s["name"] for s in service_stats],
        service_counts=[s["count"] for s in service_stats],
        manager_counter_id=None,
    )


@manager_view_bp.route("/officers", methods=["GET", "POST"])
@manager_view_bp.route("/officers/", methods=["GET", "POST"])
@login_required
@require_role(UserRole.BRANCH_MANAGER, UserRole.ADMIN)
@audit_action("manager_create_staff")
def officers():
    branch = _scoped_branch()

    if request.method == "POST":
        employee_id = request.form.get("employee_id", "").strip()
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role_value = request.form.get("role", "")

        try:
            if not all([employee_id, full_name, email, password, role_value]):
                raise UserInputError("Employee ID, name, email, password and role are required.")
            user_service.check_password(password)
            if not branch:
                raise UserInputError("No hospital location is selected for this account.")
            user_service.check_unique(email, employee_id)
            role = user_service.resolve_role(role_value, allowed=_MANAGER_STAFF_ROLES)
        except UserInputError as error:
            message = str(error)
            if message == "Select a valid role.":
                message = "Managers can only add OPD or Pharmacy staff."
            flash(message, "danger")
            return redirect(url_for("manager_view.officers"))

        user = User(
            employee_id=employee_id,
            full_name=full_name,
            email=email,
            role=role,
            branch_id=branch.id,
            # The manager chose this password; the staff member must replace it.
            must_change_password=True,
        )
        user.set_password(password)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Email or employee ID already registered.", "danger")
            return redirect(url_for("manager_view.officers"))
        AuditService.log(current_user.id, "user_created_detail",
                         {"user_id": user.id, "after": user_service.snapshot(user)})
        flash("Staff account created. They will be asked to choose their own password.", "success")
        return redirect(url_for("manager_view.officers"))

    officers = []
    if branch:
        officers = (
            User.query.filter_by(branch_id=branch.id, is_active=True)
            .filter(User.role.in_(_MANAGER_STAFF_ROLES))
            .order_by(User.full_name)
            .all()
        )

    return render_template(
        "manager/officers.html",
        branch=branch,
        officers=officers,
        allowed_roles=list(_MANAGER_STAFF_ROLES),
    )


@manager_view_bp.route("/analytics")
@login_required
@require_role(UserRole.BRANCH_MANAGER, UserRole.ADMIN)
def analytics():
    branch = _scoped_branch()
    if branch is None:
        return _choose_branch()
    today     = local_today()
    branch_id = branch.id

    total       = QueueToken.query.filter_by(branch_id=branch_id, queue_date=today).count()
    completed   = QueueToken.query.filter_by(branch_id=branch_id, queue_date=today,
                                             status=TokenStatus.COMPLETED).count()
    waiting     = QueueToken.query.filter_by(branch_id=branch_id, queue_date=today,
                                             status=TokenStatus.WAITING).count()
    avg_rating  = db.session.query(func.avg(SatisfactionRating.rating)).join(QueueToken).filter(
        QueueToken.branch_id == branch_id, QueueToken.queue_date == today).scalar()

    service_rows = (db.session.query(ServiceType.name, ServiceType.code,
                                     func.count(QueueToken.id))
                    .join(QueueToken, QueueToken.service_type_id == ServiceType.id)
                    .filter(QueueToken.branch_id == branch_id, QueueToken.queue_date == today)
                    .group_by(ServiceType.id).all())

    daily_counts = []
    for days_ago in range(6, -1, -1):
        day = today - timedelta(days=days_ago)
        daily_counts.append({
            "label": day.strftime("%a"),
            "count": QueueToken.query.filter_by(branch_id=branch_id, queue_date=day).count(),
        })

    return render_template("manager/analytics.html",
        branch=branch,
        stats={
            "total": total,
            "completed": completed,
            "waiting": waiting,
            "avg_rating": round(float(avg_rating or 0), 1),
            "completion_pct": round(completed / total * 100) if total else 0,
        },
        service_stats=[{"name": n, "code": c, "count": cnt} for n, c, cnt in service_rows],
        priority_waiting=QueueToken.query.filter_by(
            branch_id=branch_id,
            queue_date=today,
            status=TokenStatus.WAITING,
            is_priority=True,
        ).count(),
        daily_counts=daily_counts,
    )


def latest_visit_notes(customer_ids, branch_id=None):
    """{customer_id: most recent per-visit condition summary}."""
    if not customer_ids:
        return {}
    query = (QueueToken.query
             .filter(QueueToken.customer_id.in_(customer_ids),
                     QueueToken.condition_summary.isnot(None))
             .order_by(QueueToken.completed_at.desc(), QueueToken.id.desc()))
    if branch_id is not None:
        query = query.filter(QueueToken.branch_id == branch_id)
    notes = {}
    for token in query:
        notes.setdefault(token.customer_id, token)
    return notes


@manager_view_bp.route("/customers")
@login_required
@require_role(UserRole.BRANCH_MANAGER, UserRole.ADMIN)
def customers():
    branch = _scoped_branch()
    if branch is None:
        return _choose_branch()
    page = request.args.get("page", 1, type=int)

    rows = (db.session.query(Customer, func.count(QueueToken.id).label("visits"))
            .join(QueueToken, QueueToken.customer_id == Customer.id)
            .filter(QueueToken.branch_id == branch.id)
            .group_by(Customer.id)
            .order_by(func.count(QueueToken.id).desc())
            .paginate(page=page, per_page=25, error_out=False))

    AuditService.log(current_user.id, "view_patients",
                     {"branch_id": branch.id, "page": page, "count": len(rows.items)})
    return render_template("manager/customers.html",
        branch=branch,
        customers=rows.items,
        latest_notes=latest_visit_notes([c.id for c, _ in rows.items], branch.id),
        total=rows.total,
        page=page,
        pages=rows.pages,
    )


@manager_view_bp.route("/customers/<int:customer_id>/summary", methods=["POST"])
@login_required
@require_role(UserRole.BRANCH_MANAGER)
@audit_action("update_customer_summary")
def update_customer_summary(customer_id):
    customer = db.get_or_404(Customer, customer_id)
    if not QueueToken.query.filter_by(
        customer_id=customer.id, branch_id=current_user.branch_id
    ).first():
        abort(404)

    condition_summary = request.form.get("condition_summary", "").strip()
    if len(condition_summary) > 500:
        flash("Condition summary must be 500 characters or fewer.", "danger")
        return redirect(url_for("manager_view.customers"))

    customer.condition_summary = condition_summary or None
    db.session.commit()
    flash("Patient condition summary updated.", "success")
    return redirect(url_for("manager_view.customers"))


@manager_view_bp.route("/queue/other")
@manager_view_bp.route("/queue/departments")
@login_required
@require_role(UserRole.BRANCH_MANAGER, UserRole.ADMIN)
def other_queue():
    branch = _scoped_branch()
    if branch is None:
        return _choose_branch()

    waiting_tokens = (QueueToken.query
                      .filter_by(branch_id=branch.id, queue_date=local_today(),
                                 status=TokenStatus.WAITING)
                      .order_by(QueueToken.is_priority.desc(), QueueToken.id.asc()).all())

    return render_template("manager/other_queue.html",
        branch=branch,
        other_tokens=waiting_tokens,
        manager_counter_id=None,
    )


@manager_view_bp.route("/qr")
@manager_view_bp.route("/qr/")
@login_required
def branch_qr():
    branch = current_user.branch
    if not branch and current_user.role == UserRole.ADMIN:
        flash("Select a branch before viewing the QR code.", "danger")
        return redirect(url_for("admin_view.branches"))
    if not branch:
        flash("No branch is assigned to this account.", "danger")
        return redirect(url_for("index"))

    try:
        from app.utils.qr_generator import generate_branch_qr
        generate_branch_qr(branch)
    except Exception:
        from flask import current_app
        current_app.logger.exception("QR generation failed for branch %s", branch.id)

    return render_template(
        "manager/branch_qr.html",
        branch=branch,
        qr_image_url=url_for("static", filename=f"qrcodes/{branch.code}.png"),
    )
