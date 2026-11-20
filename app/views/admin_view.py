from io import BytesIO

from flask import render_template, request, redirect, url_for, flash, send_file
from flask_login import login_required, current_user
from openpyxl import Workbook
from openpyxl.styles import Font
from app.views import admin_view_bp
from app.models import (
    ASSIGNABLE_ROLES, SERVICE_ROLES, AppointmentRequest, Branch, User, UserRole,
    QueueToken, Customer, AuditLog, SatisfactionRating, ServiceType,
)
from app.utils.decorators import require_role, audit_action
from app.extensions import db
from sqlalchemy import func
from datetime import date, timedelta
import re


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


@admin_view_bp.route("/dashboard")
@login_required
@require_role(UserRole.ADMIN)
def dashboard():
    today = date.today()
    yest  = today - timedelta(days=1)

    total_branches = Branch.query.filter_by(is_active=True).count()
    tokens_today   = QueueToken.query.filter_by(queue_date=today).count()
    tokens_yest    = QueueToken.query.filter_by(queue_date=yest).count()
    today_delta    = round(((tokens_today - tokens_yest) / max(tokens_yest, 1)) * 100)
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
                        .filter(Branch.is_active == True)
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

    role_counts = {
        role.name.lower(): User.query.filter_by(is_active=True, role=role).count()
        for role in ASSIGNABLE_ROLES
    }

    weekly_data, weekly_labels = [], []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        weekly_labels.append(d.strftime("%a"))
        weekly_data.append(QueueToken.query.filter_by(queue_date=d).count())

    recent_users    = User.query.filter_by(is_active=True).order_by(User.created_at.desc()).limit(8).all()
    audit_logs      = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(10).all()
    appointment_requests = (
        AppointmentRequest.query
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

    recent_customers = [{"full_name": c.full_name, "mobile": c.mobile,
                          "created_at": c.created_at, "visit_count": vc,
                          "last_service": None, "last_branch": None}
                        for c, vc in customers_raw]

    return render_template("admin/dashboard.html",
        stats={
            "total_branches": total_branches, "tokens_today": tokens_today,
            "today_delta": today_delta,
            "national_rating": round(float(avg_r_v or 0), 1),
            "rating_count": rating_count, "active_users": active_users, "online_now": 0,
        },
        top_branches=top_branches, recent_users=recent_users,
        audit_logs=audit_logs, recent_customers=recent_customers,
        appointment_requests=appointment_requests,
        role_counts=role_counts,
        weekly_labels=weekly_labels, weekly_data=weekly_data,
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
    service = ServiceType.query.get_or_404(service_id)
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
    service = ServiceType.query.get_or_404(service_id)
    action = request.form.get("action")
    if action not in {"activate", "deactivate"}:
        flash("Invalid service action.", "danger")
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
    service = ServiceType.query.get_or_404(service_id)
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


def _selected_active_services(form):
    try:
        service_ids = {int(value) for value in form.getlist("service_ids")}
    except (TypeError, ValueError):
        raise ValueError("Select valid services.")

    if not service_ids:
        return []

    services = ServiceType.query.filter(
        ServiceType.id.in_(service_ids),
        ServiceType.is_active.is_(True),
    ).all()
    if len(services) != len(service_ids):
        raise ValueError("Select only active services.")
    return services


def _user_form_service_ids(user):
    if not user or user.role == UserRole.ADMIN:
        return set()
    if user.assigned_services:
        return {service.id for service in user.assigned_services}
    return {
        service.id for service in ServiceType.query.filter(
            ServiceType.code.in_(user.allowed_service_codes)
        ).all()
    }


@admin_view_bp.route("/branches/<int:branch_id>/qr")
@login_required
@require_role(UserRole.ADMIN)
def branch_qr(branch_id):
    branch = Branch.query.get_or_404(branch_id)
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
            pass
        flash("Branch created successfully.", "success")
        return redirect(url_for("admin_view.branches"))
    return render_template("admin/branch_form.html", branch=None)


@admin_view_bp.route("/branches/<int:branch_id>/edit", methods=["GET", "POST"])
@login_required
@require_role(UserRole.ADMIN)
def branch_edit(branch_id):
    branch = Branch.query.get_or_404(branch_id)
    if request.method == "POST":
        branch.name = request.form.get("name", branch.name).strip()
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
def branch_deactivate(branch_id):
    branch = Branch.query.get_or_404(branch_id)
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
def user_new():
    branches = Branch.query.order_by(Branch.name).all()
    roles = list(ASSIGNABLE_ROLES)
    services = ServiceType.query.filter_by(is_active=True).order_by(ServiceType.name).all()
    if request.method == "POST":
        employee_id = request.form.get("employee_id", "").strip()
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role_value = request.form.get("role")
        branch_id = request.form.get("branch_id") or None
        if not employee_id or not full_name or not email or not password or not role_value:
            flash("Employee ID, name, email, password and role are required.", "danger")
            return render_template(
                "admin/user_form.html", user=None, branches=branches, roles=roles,
                services=services, selected_service_ids=set(),
            )
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "danger")
            return render_template(
                "admin/user_form.html", user=None, branches=branches, roles=roles,
                services=services, selected_service_ids=set(),
            )
        role = next((role for role in roles if role.name == role_value), None)
        if role is None:
            flash("Invalid role selected.", "danger")
            return render_template(
                "admin/user_form.html", user=None, branches=branches, roles=roles,
                services=services, selected_service_ids=set(),
            )
        try:
            selected_services = _selected_active_services(request.form)
            if role != UserRole.ADMIN and not selected_services:
                raise ValueError("Assign at least one service to this user.")
        except ValueError as error:
            flash(str(error), "danger")
            return render_template(
                "admin/user_form.html", user=None, branches=branches, roles=roles,
                services=services, selected_service_ids=set(),
            )
        user = User(
            employee_id=employee_id,
            full_name=full_name,
            email=email,
            role=role,
            branch_id=int(branch_id) if branch_id else None,
            assigned_services=selected_services,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash("User created successfully.", "success")
        return redirect(url_for("admin_view.users"))
    return render_template(
        "admin/user_form.html", user=None, branches=branches, roles=roles,
        services=services, selected_service_ids=set(),
    )


@admin_view_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@require_role(UserRole.ADMIN)
def user_edit(user_id):
    user = User.query.get_or_404(user_id)
    branches = Branch.query.order_by(Branch.name).all()
    roles = list(ASSIGNABLE_ROLES)
    services = ServiceType.query.filter_by(is_active=True).order_by(ServiceType.name).all()
    if request.method == "POST":
        user.employee_id = request.form.get("employee_id", user.employee_id).strip()
        user.full_name = request.form.get("full_name", user.full_name).strip()
        user.email = request.form.get("email", user.email).strip().lower()
        user.branch_id = int(request.form.get("branch_id")) if request.form.get("branch_id") else None
        user.is_active = bool(request.form.get("is_active"))
        role_value = request.form.get("role")
        if role_value:
            role = next((role for role in roles if role.name == role_value), None)
            if role is None:
                flash("Invalid role selected.", "danger")
                return render_template(
                    "admin/user_form.html", user=user, branches=branches, roles=roles,
                    services=services, selected_service_ids=_user_form_service_ids(user),
                )
            user.role = role
        try:
            selected_services = _selected_active_services(request.form)
            if user.role not in (UserRole.ADMIN, UserRole.BRANCH_MANAGER) and not selected_services:
                raise ValueError("Assign at least one service to this user.")
        except ValueError as error:
            flash(str(error), "danger")
            return render_template(
                "admin/user_form.html", user=user, branches=branches, roles=roles,
                services=services, selected_service_ids=_user_form_service_ids(user),
            )
        user.assigned_services = selected_services
        password = request.form.get("password", "")
        if password:
            user.set_password(password)
        db.session.commit()
        flash("User updated successfully.", "success")
        return redirect(url_for("admin_view.users"))
    return render_template(
        "admin/user_form.html", user=user, branches=branches, roles=roles,
        services=services, selected_service_ids=_user_form_service_ids(user),
    )


@admin_view_bp.route("/users/<int:user_id>/deactivate", methods=["POST"])
@login_required
@require_role(UserRole.ADMIN)
def user_deactivate(user_id):
    user = User.query.get_or_404(user_id)
    user.is_active = False
    db.session.commit()
    flash(f"User {user.full_name} has been deactivated.", "success")
    return redirect(url_for("admin_view.users"))


@admin_view_bp.route("/customers")
@login_required
@require_role(UserRole.ADMIN)
def customers():
    customers = Customer.query.order_by(Customer.created_at.desc()).all()
    return render_template("admin/customers.html", customers=customers)


@admin_view_bp.route("/customers/<int:customer_id>/summary", methods=["POST"])
@login_required
@require_role(UserRole.ADMIN)
@audit_action("update_customer_summary")
def update_customer_summary(customer_id):
    customer = Customer.query.get_or_404(customer_id)
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


@admin_view_bp.route("/customers/export")
@login_required
@require_role(UserRole.ADMIN)
@audit_action("export_customers")
def export_customers():
    customers = Customer.query.order_by(Customer.created_at.desc()).all()
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Customers"
    worksheet.append(["Customer ID", "Full Name", "Mobile", "Registered"])
    for cell in worksheet[1]:
        cell.font = Font(bold=True)

    for customer in customers:
        name = customer.full_name
        mobile = customer.mobile
        worksheet.append([
            customer.id,
            name,
            mobile,
            customer.created_at.isoformat() if customer.created_at else "",
        ])
        for column in (2, 3):
            worksheet.cell(row=worksheet.max_row, column=column).data_type = "s"

    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    worksheet.column_dimensions["A"].width = 14
    worksheet.column_dimensions["B"].width = 32
    worksheet.column_dimensions["C"].width = 20
    worksheet.column_dimensions["D"].width = 28

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="hospital_customers.xlsx",
    )


@admin_view_bp.route("/audit-logs")
@login_required
@require_role(UserRole.ADMIN)
def audit_logs_view():
    page = request.args.get("page", 1, type=int)
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).paginate(page=page, per_page=50)
    return render_template("admin/audit_logs.html", logs=logs)


@admin_view_bp.route("/analytics")
@login_required
@require_role(UserRole.ADMIN)
def analytics():
    today = date.today()
    branches = Branch.query.filter_by(is_active=True).all()
    
    # Daily stats for last 30 days
    daily_stats = []
    for i in range(29, -1, -1):
        d = today - timedelta(days=i)
        tokens = QueueToken.query.filter_by(queue_date=d).count()
        daily_stats.append({"date": d.isoformat(), "tokens": tokens})
    
    return render_template("admin/analytics.html", 
                         daily_stats=daily_stats,
                         branches=branches)
