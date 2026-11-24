from flask import render_template, request, redirect, url_for, flash, send_file
from flask_login import login_required, current_user
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from app.views import admin_view_bp
from app.models import (
    ASSIGNABLE_ROLES, OPEN_STATUSES, SERVICE_ROLES, AppointmentRequest, AppointmentStatus,
    Branch, User, UserRole, QueueToken, Customer, AuditLog, SatisfactionRating, ServiceType,
)
from app.services import metrics, user_service
from app.services.audit_service import AuditService
from app.services.user_service import UserInputError
from app.utils.decorators import require_role, audit_action
from app.utils.timeutil import local_today, to_local
from app.utils.validators import validate_branch_code
from app.extensions import db
from sqlalchemy import func, or_
from datetime import timedelta
import re
import tempfile
from sqlalchemy.exc import IntegrityError


SERVICE_ROLE_LABELS = {
    role.value: role.value.replace("_", " ").title()
    for role in (
        *SERVICE_ROLES,
        UserRole.LOBBY_OFFICER,
        UserRole.LOAN_OFFICER,
        UserRole.SPECIALIST_DOCTOR,
    )
}


def _service_code(name, existing_codes):
    base = re.sub(r"[^A-Z0-9]", "", name.upper())
    if not base:
        raise ValueError("Service name must contain letters or numbers.")

    candidate = base[:5]
    if candidate not in existing_codes:
        return candidate

    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for suffix in range(1, 36 ** 5):
        suffix_text = ""
        remaining = suffix
        while remaining:
            remaining, digit = divmod(remaining, 36)
            suffix_text = alphabet[digit] + suffix_text
        candidate = f"{base[:5 - len(suffix_text)]}{suffix_text}"
        if candidate not in existing_codes:
            return candidate
    raise ValueError("No available service codes remain.")


def _service_prefix(name, existing_prefixes):
    base = re.sub(r"[^A-Z0-9]", "", name.upper())
    if not base:
        raise ValueError("Service name must contain letters or numbers.")

    candidates = [character for character in base]
    candidates.extend(
        character for character in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        if character not in candidates
    )
    for candidate in candidates:
        if candidate not in existing_prefixes:
            return candidate

    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    for first in alphabet:
        for second in alphabet:
            candidate = first + second
            if candidate not in existing_prefixes:
                return candidate
    raise ValueError("No available token prefixes remain.")


def _validated_service_name(name, exclude_id=None):
    name = name.strip()
    if not name:
        raise ValueError("Enter a name for the service.")
    if len(name) > 80:
        raise ValueError("Service name must be 80 characters or fewer.")

    duplicate = ServiceType.query.filter(
        func.lower(ServiceType.name) == name.lower()
    )
    if exclude_id is not None:
        duplicate = duplicate.filter(ServiceType.id != exclude_id)
    if duplicate.first():
        raise ValueError("A service with that name already exists.")
    return name


def _validated_service_updates(service, form):
    name = _validated_service_name(form.get("name", ""), exclude_id=service.id)

    prefix = form.get("prefix", "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{1,2}", prefix):
        raise ValueError("Token prefix must be one or two letters or numbers.")
    duplicate_prefix = ServiceType.query.filter(
        func.upper(ServiceType.prefix) == prefix,
        ServiceType.id != service.id,
    ).first()
    if duplicate_prefix:
        raise ValueError("That token prefix is already in use.")

    try:
        avg_service_min = int(form.get("avg_service_min", ""))
    except (TypeError, ValueError):
        raise ValueError("Average service time must be a whole number.")
    if not 1 <= avg_service_min <= 240:
        raise ValueError("Average service time must be between 1 and 240 minutes.")

    allowed_roles = form.getlist("allowed_roles")
    if any(role not in SERVICE_ROLE_LABELS for role in allowed_roles):
        raise ValueError("Select valid staff roles for this service.")

    return name, prefix, avg_service_min, allowed_roles


def _admin_demand_analytics(today):
    period_start = today - timedelta(days=29)
    previous_start = today - timedelta(days=59)
    previous_end = period_start - timedelta(days=1)

    daily_counts = dict(
        db.session.query(QueueToken.queue_date, func.count(QueueToken.id))
        .filter(QueueToken.queue_date.between(period_start, today))
        .group_by(QueueToken.queue_date)
        .all()
    )
    daily_stats = [
        {
            "date": (today - timedelta(days=days_ago)).isoformat(),
            "tokens": daily_counts.get(today - timedelta(days=days_ago), 0),
        }
        for days_ago in range(29, -1, -1)
    ]

    tokens_30d = sum(daily_counts.values())
    tokens_previous_30d = (
        QueueToken.query
        .filter(QueueToken.queue_date.between(previous_start, previous_end))
        .count()
    )
    growth_percent = (
        round((tokens_30d - tokens_previous_30d) * 100 / tokens_previous_30d)
        if tokens_previous_30d
        else None
    )

    branch_counts = dict(
        db.session.query(Branch.id, func.count(QueueToken.id))
        .join(QueueToken, QueueToken.branch_id == Branch.id)
        .filter(
            Branch.is_active.is_(True),
            QueueToken.queue_date.between(period_start, today),
        )
        .group_by(Branch.id)
        .all()
    )
    branches = Branch.query.filter_by(is_active=True).order_by(Branch.name).all()
    branch_stats = [
        {
            "label": branch.name,
            "city": branch.city,
            "tokens": branch_counts.get(branch.id, 0),
        }
        for branch in branches
    ]
    branch_stats.sort(key=lambda item: (-item["tokens"], item["label"].casefold()))

    service_counts = dict(
        db.session.query(ServiceType.id, func.count(QueueToken.id))
        .join(QueueToken, QueueToken.service_type_id == ServiceType.id)
        .filter(
            ServiceType.is_active.is_(True),
            QueueToken.queue_date.between(period_start, today),
        )
        .group_by(ServiceType.id)
        .all()
    )
    services = ServiceType.query.filter_by(is_active=True).order_by(ServiceType.name).all()
    service_stats = [
        {
            "label": service.name,
            "tokens": service_counts.get(service.id, 0),
        }
        for service in services
    ]
    service_stats.sort(key=lambda item: (-item["tokens"], item["label"].casefold()))

    return {
        "daily_stats": daily_stats,
        "branch_stats": branch_stats,
        "service_stats": service_stats,
        "metrics": {
            "tokens_30d": tokens_30d,
            "tokens_previous_30d": tokens_previous_30d,
            "growth_percent": growth_percent,
            "active_branches": len(branches),
            "active_services": len(services),
            "top_branch": next(
                (branch["label"] for branch in branch_stats if branch["tokens"]),
                None,
            ),
            "top_service": next(
                (service["label"] for service in service_stats if service["tokens"]),
                None,
            ),
        },
    }


@admin_view_bp.route("/dashboard")
@login_required
@require_role(UserRole.ADMIN)
def dashboard():
    today = local_today()
    demand_analytics = _admin_demand_analytics(today)
    yest  = today - timedelta(days=1)

    total_branches = Branch.query.filter_by(is_active=True).count()
    tokens_today   = QueueToken.query.filter_by(queue_date=today).count()
    tokens_yest    = QueueToken.query.filter_by(queue_date=yest).count()
    active_users   = User.query.filter_by(is_active=True).count()

    avg_r_v     = db.session.query(func.avg(SatisfactionRating.rating)).join(QueueToken).filter(
        QueueToken.queue_date == today).scalar()
    rating_count = (db.session.query(func.count(SatisfactionRating.id)).join(QueueToken).filter(
        QueueToken.queue_date == today).scalar()) or 0

    top_branches_raw = (db.session.query(Branch,
                            func.count(QueueToken.id).label("cnt"),
                            func.avg(SatisfactionRating.rating).label("avg_r"))
                        .outerjoin(QueueToken, (QueueToken.branch_id == Branch.id) &
                                               (QueueToken.queue_date == today))
                        .outerjoin(SatisfactionRating,
                                   SatisfactionRating.token_id == QueueToken.id)
                        .filter(Branch.is_active.is_(True))
                        .group_by(Branch.id)
                        .order_by(func.count(QueueToken.id).desc())
                        .limit(8).all())

    top_branches = [{
        "id": b.id,
        "code": b.code,
        "name": b.name,
        "city": b.city,
        "is_active": b.is_active,
        "tokens_today": cnt,
        "avg_rating": round(float(r or 0), 1) if r else None,
    } for b, cnt, r in top_branches_raw]

    recent_users    = User.query.filter_by(is_active=True).order_by(User.created_at.desc()).limit(8).all()
    audit_logs      = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(10).all()
    appointment_requests = (
        AppointmentRequest.query
        .filter(AppointmentRequest.status.in_(AppointmentStatus.OPEN))
        .order_by(AppointmentRequest.created_at.desc())
        .limit(20)
        .all()
    )

    customers_raw = (db.session.query(Customer,
                         func.count(QueueToken.id).label("vc"))
                     .outerjoin(QueueToken, QueueToken.customer_id == Customer.id)
                     .group_by(Customer.id)
                     .order_by(func.count(QueueToken.id).desc())
                     .limit(10).all())

    def last_visit(customer):
        return (QueueToken.query.filter_by(customer_id=customer.id)
                .order_by(QueueToken.id.desc()).first())

    recent_customers = []
    for c, vc in customers_raw:
        last = last_visit(c)
        recent_customers.append({
            "full_name": c.full_name, "mobile": c.mobile,
            "created_at": c.created_at, "visit_count": vc,
            "last_service": last.service_type.name if last else None,
            "last_branch": last.branch.name if last else None,
        })

    return render_template("admin/dashboard.html",
        stats={
            "total_branches": total_branches, "tokens_today": tokens_today,
            "today_delta": metrics.pct_change(tokens_today, tokens_yest),
            "national_rating": round(float(avg_r_v), 1) if avg_r_v is not None else None,
            "rating_count": rating_count, "active_users": active_users,
            "online_now": metrics.online_now(),
        },
        top_branches=top_branches, recent_users=recent_users,
        audit_logs=audit_logs, recent_customers=recent_customers,
        appointment_requests=appointment_requests,
        demand_analytics=demand_analytics,
    )


@admin_view_bp.route("/branches")
@login_required
@require_role(UserRole.ADMIN)
def branches():
    branches = Branch.query.order_by(Branch.name).all()
    return render_template("admin/branches.html", branches=branches)


@admin_view_bp.route("/services", methods=["GET", "POST"])
@login_required
@require_role(UserRole.ADMIN)
def services():
    services = ServiceType.query.order_by(
        ServiceType.is_active.desc(), ServiceType.name
    ).all()
    if request.method == "POST":
        try:
            new_name = _validated_service_name(request.form.get("new_name", ""))

            existing_services = ServiceType.query.all()
            new_service = ServiceType(
                code=_service_code(new_name, {service.code for service in existing_services}),
                name=new_name,
                prefix=_service_prefix(new_name, {service.prefix for service in existing_services}),
                avg_service_min=10,
                allowed_roles=[],
            )
            db.session.add(new_service)
            db.session.add(AuditLog(
                user_id=current_user.id,
                action="add_service",
                payload={"service_code": new_service.code, "service_name": new_name},
                ip_address=request.remote_addr,
            ))
            db.session.commit()
        except (TypeError, ValueError) as error:
            db.session.rollback()
            flash(str(error), "danger")
            return redirect(url_for("admin_view.services"))

        flash("Service added successfully.", "success")
        return redirect(url_for("admin_view.services"))

    return render_template(
        "admin/services.html",
        services=services,
        active_service_count=sum(service.is_active for service in services),
        role_labels=SERVICE_ROLE_LABELS,
    )


@admin_view_bp.post("/services/<int:service_id>/edit")
@login_required
@require_role(UserRole.ADMIN)
def service_edit(service_id):
    service = db.get_or_404(ServiceType, service_id)
    try:
        name, prefix, avg_service_min, allowed_roles = _validated_service_updates(
            service, request.form
        )
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("admin_view.services"))

    service.name = name
    service.prefix = prefix
    service.avg_service_min = avg_service_min
    service.allowed_roles = allowed_roles
    db.session.add(AuditLog(
        user_id=current_user.id,
        action="update_service",
        payload={
            "service_code": service.code,
            "service_name": service.name,
            "service_prefix": service.prefix,
            "avg_service_min": service.avg_service_min,
            "allowed_roles": service.allowed_roles,
        },
        ip_address=request.remote_addr,
    ))
    db.session.commit()
    flash("Service updated successfully.", "success")
    return redirect(url_for("admin_view.services"))


@admin_view_bp.post("/services/<int:service_id>/status")
@login_required
@require_role(UserRole.ADMIN)
def service_status(service_id):
    service = db.get_or_404(ServiceType, service_id)
    action = request.form.get("action")
    if action not in {"activate", "deactivate"}:
        flash("Invalid service action.", "danger")
        return redirect(url_for("admin_view.services"))

    if action == "deactivate":
        pending = QueueToken.query.filter(
            QueueToken.service_type_id == service.id,
            QueueToken.queue_date == local_today(),
            QueueToken.status.in_(OPEN_STATUSES),
        ).count()
        if pending:
            flash(f"{pending} patient(s) are still queued for {service.name}. "
                  "Serve, transfer or mark them before deactivating the service.", "danger")
            return redirect(url_for("admin_view.services"))

    service.is_active = action == "activate"
    db.session.add(AuditLog(
        user_id=current_user.id,
        action=f"{action}_service",
        payload={"service_code": service.code, "service_name": service.name},
        ip_address=request.remote_addr,
    ))
    db.session.commit()
    flash(
        f"Service {service.name} {'activated' if service.is_active else 'deactivated'}.",
        "success",
    )
    return redirect(url_for("admin_view.services"))


@admin_view_bp.post("/services/<int:service_id>/delete")
@login_required
@require_role(UserRole.ADMIN)
def service_delete(service_id):
    service = db.get_or_404(ServiceType, service_id)
    if (
        service.tokens.count()
        or service.assigned_users
        or AppointmentRequest.query.filter_by(service_type_id=service.id).first()
    ):
        flash(
            "This service is linked to users or records and cannot be permanently deleted. "
            "Deactivate it instead.",
            "danger",
        )
        return redirect(url_for("admin_view.services"))

    db.session.add(AuditLog(
        user_id=current_user.id,
        action="delete_service",
        payload={"service_code": service.code, "service_name": service.name},
        ip_address=request.remote_addr,
    ))
    db.session.delete(service)
    db.session.commit()
    flash(f"Service {service.name} deleted.", "success")
    return redirect(url_for("admin_view.services"))


def _user_form_service_ids(user):
    if not user or user.role == UserRole.ADMIN:
        return set()
    allowed_codes = set(user.allowed_service_codes)
    if user.assigned_services:
        return {
            service.id for service in user.assigned_services
            if service.code in allowed_codes
        }
    return {
        service.id for service in ServiceType.query.filter(
            ServiceType.code.in_(allowed_codes)
        ).all()
    }


@admin_view_bp.route("/branches/<int:branch_id>/qr")
@login_required
@require_role(UserRole.ADMIN)
def branch_qr(branch_id):
    branch = db.get_or_404(Branch, branch_id)
    try:
        from app.utils.qr_generator import generate_branch_qr
        generate_branch_qr(branch)
    except Exception:
        pass
    return render_template(
        "admin/branch_qr.html",
        branch=branch,
        qr_image_url=url_for("static", filename=f"qrcodes/{branch.code}.png"),
    )


@admin_view_bp.route("/branches/new", methods=["GET", "POST"])
@login_required
@require_role(UserRole.ADMIN)
@audit_action("create_branch")
def branch_new():
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        name = request.form.get("name", "").strip()
        address = request.form.get("address", "").strip() or None
        city = request.form.get("city", "").strip() or None
        phone = request.form.get("phone", "").strip() or None
        if not code or not name:
            flash("Branch code and name are required.", "danger")
            return render_template("admin/branch_form.html", branch=None)
        code_error = validate_branch_code(code)
        if code_error:
            flash(code_error + ".", "danger")
            return render_template("admin/branch_form.html", branch=None)
        if Branch.query.filter_by(code=code).first():
            flash("Branch code already exists.", "danger")
            return render_template("admin/branch_form.html", branch=None)
        branch = Branch(name=name, code=code, address=address, city=city, phone=phone)
        db.session.add(branch)
        db.session.commit()
        try:
            from app.utils.qr_generator import generate_branch_qr
            generate_branch_qr(branch)
        except Exception:
            from flask import current_app
            current_app.logger.exception("QR generation failed for branch %s", branch.id)
        flash("Branch created successfully.", "success")
        return redirect(url_for("admin_view.branches"))
    return render_template("admin/branch_form.html", branch=None)


@admin_view_bp.route("/branches/<int:branch_id>/edit", methods=["GET", "POST"])
@login_required
@require_role(UserRole.ADMIN)
@audit_action("update_branch")
def branch_edit(branch_id):
    branch = db.get_or_404(Branch, branch_id)
    if request.method == "POST":
        branch.name = request.form.get("name", branch.name).strip() or branch.name
        branch.address = request.form.get("address", "").strip() or None
        branch.city = request.form.get("city", "").strip() or None
        branch.phone = request.form.get("phone", "").strip() or None
        branch.is_active = bool(request.form.get("is_active"))
        db.session.commit()
        flash("Branch updated successfully.", "success")
        return redirect(url_for("admin_view.branches"))
    return render_template("admin/branch_form.html", branch=branch)


@admin_view_bp.route("/branches/<int:branch_id>/deactivate", methods=["POST"])
@login_required
@require_role(UserRole.ADMIN)
@audit_action("deactivate_branch")
def branch_deactivate(branch_id):
    branch = db.get_or_404(Branch, branch_id)
    branch.is_active = False
    db.session.commit()
    flash(f"Branch {branch.name} has been deactivated.", "success")
    return redirect(url_for("admin_view.branches"))


@admin_view_bp.route("/users")
@login_required
@require_role(UserRole.ADMIN)
def users():
    users = User.query.filter_by(is_active=True).order_by(User.full_name).all()
    return render_template("admin/users.html", users=users)


@admin_view_bp.route("/users/new", methods=["GET", "POST"])
@login_required
@require_role(UserRole.ADMIN)
@audit_action("create_user")
def user_new():
    branches = Branch.query.order_by(Branch.name).all()
    roles = list(ASSIGNABLE_ROLES)
    services = ServiceType.query.filter_by(is_active=True).order_by(ServiceType.name).all()

    def form():
        return render_template(
            "admin/user_form.html", user=None, branches=branches, roles=roles,
            services=services, selected_service_ids=set(),
        )

    if request.method == "POST":
        employee_id = request.form.get("employee_id", "").strip()
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        try:
            if not (employee_id and full_name and email and password
                    and request.form.get("role")):
                raise UserInputError("Employee ID, name, email, password and role are required.")
            user_service.check_password(password)
            user_service.check_unique(email, employee_id)
            role = user_service.resolve_role(request.form.get("role"), roles)
            branch_id = user_service.resolve_branch_id(request.form.get("branch_id"), role)
            selected_services = user_service.resolve_services(
                request.form.getlist("service_ids"), role)
        except UserInputError as error:
            flash(str(error), "danger")
            return form()
        user = User(
            employee_id=employee_id,
            full_name=full_name,
            email=email,
            role=role,
            branch_id=branch_id,
            assigned_services=selected_services,
            must_change_password=bool(request.form.get("must_change_password")),
        )
        user.set_password(password)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Email or employee ID already registered.", "danger")
            return form()
        AuditService.log(current_user.id, "user_created_detail",
                         {"user_id": user.id, "after": user_service.snapshot(user)})
        flash("User created successfully.", "success")
        return redirect(url_for("admin_view.users"))
    return form()


@admin_view_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@require_role(UserRole.ADMIN)
@audit_action("update_user")
def user_edit(user_id):
    user = db.get_or_404(User, user_id)
    branches = Branch.query.order_by(Branch.name).all()
    roles = list(ASSIGNABLE_ROLES)
    services = ServiceType.query.filter_by(is_active=True).order_by(ServiceType.name).all()
    if request.method == "POST":
        def _form_error(message):
            db.session.rollback()
            flash(message, "danger")
            return render_template(
                "admin/user_form.html", user=user, branches=branches, roles=roles,
                services=services, selected_service_ids=_user_form_service_ids(user),
            )

        before = user_service.snapshot(user)
        employee_id = request.form.get("employee_id", user.employee_id).strip()
        email = request.form.get("email", user.email).strip().lower()
        is_active = bool(request.form.get("is_active"))
        password = request.form.get("password", "")
        try:
            if not employee_id or not email:
                raise UserInputError("Employee ID and email are required.")
            user_service.check_unique(email, employee_id, exclude_id=user.id)
            if user.id == current_user.id and not is_active:
                raise UserInputError("You cannot deactivate your own account.")
            role = (user_service.resolve_role(request.form.get("role"), roles)
                    if request.form.get("role") else user.role)
            if user.id == current_user.id and role != user.role:
                raise UserInputError("You cannot change your own role.")
            branch_id = user_service.resolve_branch_id(request.form.get("branch_id"), role)
            selected_services = user_service.resolve_services(
                request.form.getlist("service_ids"), role)
            if password:
                user_service.check_password(password)
        except UserInputError as error:
            return _form_error(str(error))

        user.employee_id = employee_id
        user.full_name = request.form.get("full_name", user.full_name).strip() or user.full_name
        user.email = email
        user.branch_id = branch_id
        user.is_active = is_active
        user.role = role
        user.assigned_services = selected_services
        if password:
            user.set_password(password)
            user.must_change_password = bool(request.form.get("must_change_password"))
        db.session.commit()
        changes = AuditService.diff(before, user_service.snapshot(user))
        if password:
            changes["password"] = "reset"
        AuditService.log(current_user.id, "user_updated_detail",
                         {"user_id": user.id, "changes": changes})
        flash("User updated successfully.", "success")
        return redirect(url_for("admin_view.users"))
    return render_template(
        "admin/user_form.html", user=user, branches=branches, roles=roles,
        services=services, selected_service_ids=_user_form_service_ids(user),
    )


@admin_view_bp.route("/users/<int:user_id>/deactivate", methods=["POST"])
@login_required
@require_role(UserRole.ADMIN)
@audit_action("deactivate_user")
def user_deactivate(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("You cannot deactivate your own account.", "danger")
        return redirect(url_for("admin_view.users"))
    user.is_active = False
    db.session.commit()
    flash(f"User {user.full_name} has been deactivated.", "success")
    return redirect(url_for("admin_view.users"))


@admin_view_bp.route("/customers")
@login_required
@require_role(UserRole.ADMIN)
def customers():
    from app.views.manager_view import latest_visit_notes
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()[:80]
    query = Customer.query
    if search:
        like = f"%{search}%"
        query = query.filter(or_(Customer.full_name.ilike(like), Customer.mobile.like(like)))
    pagination = (query.order_by(Customer.created_at.desc())
                  .paginate(page=page, per_page=50, error_out=False))
    AuditService.log(current_user.id, "view_patients",
                     {"page": page, "search": bool(search), "count": len(pagination.items)})
    return render_template(
        "admin/customers.html",
        customers=pagination.items,
        pagination=pagination,
        search=search,
        latest_notes=latest_visit_notes([c.id for c in pagination.items]),
    )


@admin_view_bp.route("/customers/<int:customer_id>/summary", methods=["POST"])
@login_required
@require_role(UserRole.ADMIN)
@audit_action("update_customer_summary")
def update_customer_summary(customer_id):
    customer = db.get_or_404(Customer, customer_id)
    review_comments = request.form.get("review_comments")
    if review_comments is not None:
        review_comments = review_comments.strip()
    if review_comments is not None and len(review_comments) > 2000:
        flash("Review comments must be 2,000 characters or fewer.", "danger")
        return redirect(url_for("admin_view.customers"))

    condition_summary = request.form.get("condition_summary")
    if condition_summary is not None:
        condition_summary = condition_summary.strip()
    if condition_summary is not None and len(condition_summary) > 500:
        flash("Condition summary must be 500 characters or fewer.", "danger")
        return redirect(url_for("admin_view.customers"))

    if review_comments is not None:
        customer.review_comments = review_comments or None
    if condition_summary is not None:
        customer.condition_summary = condition_summary or None
    db.session.commit()
    flash("Patient summary updated.", "success")
    return redirect(url_for("admin_view.customers"))


def _xlsx_response(workbook, filename):
    # Spool to a temporary file so a large export isn't held in memory twice.
    output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    workbook.save(output)
    output.seek(0)
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@admin_view_bp.route("/customers/export")
@login_required
@require_role(UserRole.ADMIN)
def export_customers():
    AuditService.log(current_user.id, "export_customers", {"url": request.path})
    workbook = Workbook(write_only=True)
    worksheet = workbook.create_sheet("Customers")
    worksheet.column_dimensions["A"].width = 14
    worksheet.column_dimensions["B"].width = 32
    worksheet.column_dimensions["C"].width = 20
    worksheet.column_dimensions["D"].width = 28
    worksheet.freeze_panes = "A2"

    def cell(value, bold=False, as_text=False):
        c = WriteOnlyCell(worksheet, value=value)
        if bold:
            c.font = Font(bold=True)
        if as_text:
            c.data_type = "s"
        return c

    worksheet.append([cell(h, bold=True)
                      for h in ("Customer ID", "Full Name", "Mobile", "Registered")])
    # Stream plain rows in batches instead of loading every patient at once.
    rows = (db.session.query(Customer.id, Customer.full_name, Customer.mobile,
                             Customer.created_at)
            .order_by(Customer.created_at.desc())
            .yield_per(1000))
    for customer_id, full_name, mobile, created_at in rows:
        local = to_local(created_at)
        worksheet.append([
            customer_id,
            cell(full_name, as_text=True),
            cell(mobile, as_text=True),
            local.strftime("%Y-%m-%d %H:%M") if local else "",
        ])
    return _xlsx_response(workbook, "hospital_customers.xlsx")


@admin_view_bp.route("/analytics/export")
@login_required
@require_role(UserRole.ADMIN)
def export_analytics():
    AuditService.log(current_user.id, "export_analytics", {"url": request.path})
    data = _admin_demand_analytics(local_today())
    workbook = Workbook()
    daily = workbook.active
    daily.title = "Daily tokens (30 days)"
    daily.append(["Date", "Tokens"])
    for row in data["daily_stats"]:
        daily.append([row["date"], row["tokens"]])
    by_branch = workbook.create_sheet("By location")
    by_branch.append(["Location", "City", "Tokens (30 days)"])
    for row in data["branch_stats"]:
        by_branch.append([row["label"], row["city"] or "", row["tokens"]])
    by_service = workbook.create_sheet("By service")
    by_service.append(["Service", "Tokens (30 days)"])
    for row in data["service_stats"]:
        by_service.append([row["label"], row["tokens"]])
    for sheet in workbook.worksheets:
        for c in sheet[1]:
            c.font = Font(bold=True)
    return _xlsx_response(workbook, f"hospital_analytics_{local_today().isoformat()}.xlsx")


@admin_view_bp.route("/audit-logs")
@login_required
@require_role(UserRole.ADMIN)
def audit_logs_view():
    page = request.args.get("page", 1, type=int)
    logs = (AuditLog.query.order_by(AuditLog.created_at.desc())
            .paginate(page=page, per_page=50, error_out=False))
    return render_template("admin/audit_logs.html", logs=logs)


@admin_view_bp.route("/analytics")
@login_required
@require_role(UserRole.ADMIN)
def analytics():
    today = local_today()
    demand_analytics = _admin_demand_analytics(today)
    return render_template(
        "admin/analytics.html",
        demand_analytics=demand_analytics,
    )
