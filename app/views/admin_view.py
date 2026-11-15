from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.views import admin_view_bp
from app.models import (Branch, User, UserRole, QueueToken, TokenStatus,
                         Customer, AuditLog, SatisfactionRating, ServiceType)
from app.utils.decorators import require_role
from app.extensions import db
from sqlalchemy import func
from datetime import date, timedelta
import re


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
        "admin":          User.query.filter_by(is_active=True, role=UserRole.ADMIN).count(),
        "branch_manager": User.query.filter_by(is_active=True, role=UserRole.BRANCH_MANAGER).count(),
        "lobby_officer":  User.query.filter_by(is_active=True, role=UserRole.LOBBY_OFFICER).count(),
        "loan_officer":   User.query.filter_by(is_active=True, role=UserRole.LOAN_OFFICER).count(),
    }

    weekly_data, weekly_labels = [], []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        weekly_labels.append(d.strftime("%a"))
        weekly_data.append(QueueToken.query.filter_by(queue_date=d).count())

    recent_users    = User.query.filter_by(is_active=True).order_by(User.created_at.desc()).limit(8).all()
    audit_logs      = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(10).all()

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
    legacy_codes = ["AO", "AC", "FD", "LN", "OT"]
    services = (ServiceType.query
                .filter(~ServiceType.code.in_(legacy_codes))
                .order_by(ServiceType.name).all())
    role_labels = {
        UserRole.LOBBY_OFFICER.value: "Reception / OPD staff",
        UserRole.LOAN_OFFICER.value: "Department staff",
    }

    if request.method == "POST":
        try:
            new_code = request.form.get("new_code", "").strip().upper()
            new_name = request.form.get("new_name", "").strip()
            new_prefix = request.form.get("new_prefix", "").strip().upper()
            new_avg = request.form.get("new_avg", "").strip()
            new_roles = request.form.getlist("new_roles")
            has_new_service_data = any([new_code, new_name, new_prefix, new_avg, new_roles])
            if has_new_service_data:
                if not all([new_code, new_name, new_prefix, new_avg]):
                    raise ValueError("To add a service, provide its code, name, token prefix, and average time.")
                if not re.fullmatch(r"[A-Z0-9]{1,5}", new_code):
                    raise ValueError("Service code must be 1 to 5 letters or numbers.")
                if not re.fullmatch(r"[A-Z0-9]{1,2}", new_prefix):
                    raise ValueError("Token prefix must be one or two letters or numbers.")
                if ServiceType.query.filter_by(code=new_code).first():
                    raise ValueError("That service code is already in use.")
                new_avg = int(new_avg)
                if new_avg < 1 or new_avg > 240:
                    raise ValueError("Average service time must be between 1 and 240 minutes.")
                if any(role not in role_labels for role in new_roles):
                    raise ValueError("Select valid staff roles for the new service.")

            active_prefixes = []
            for service in services:
                service.name = request.form.get(f"name_{service.id}", "").strip()
                service.prefix = request.form.get(f"prefix_{service.id}", "").strip().upper()
                service.avg_service_min = int(request.form.get(f"avg_{service.id}", ""))
                service.allowed_roles = [
                    role.value for role in (UserRole.LOBBY_OFFICER, UserRole.LOAN_OFFICER)
                    if role.value in request.form.getlist(f"roles_{service.id}")
                ]
                service.is_active = request.form.get(f"active_{service.id}") == "1"
                if service.is_active and (not service.name or not service.prefix or len(service.prefix) > 2):
                    raise ValueError("Each active service needs a name and a prefix of one or two characters.")
                if service.avg_service_min < 1 or service.avg_service_min > 240:
                    raise ValueError("Average service time must be between 1 and 240 minutes.")
                if service.is_active:
                    active_prefixes.append(service.prefix)
            if has_new_service_data:
                active_prefixes.append(new_prefix)
            if len(active_prefixes) != len(set(active_prefixes)):
                raise ValueError("Each active service must have a unique token prefix.")
            if has_new_service_data:
                new_service = ServiceType(
                    code=new_code,
                    name=new_name,
                    prefix=new_prefix,
                    avg_service_min=new_avg,
                    allowed_roles=new_roles,
                )
                db.session.add(new_service)
                services.append(new_service)
            db.session.add(AuditLog(
                user_id=current_user.id,
                action="configure_services",
                payload={"service_codes": [service.code for service in services]},
                ip_address=request.remote_addr,
            ))
            db.session.commit()
        except (TypeError, ValueError) as error:
            db.session.rollback()
            flash(str(error), "danger")
            return render_template("admin/services.html", services=services, role_labels=role_labels)

        flash("Department and staff access settings saved.", "success")
        return redirect(url_for("admin_view.services"))

    return render_template("admin/services.html", services=services, role_labels=role_labels)


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
    roles = list(UserRole)
    if request.method == "POST":
        employee_id = request.form.get("employee_id", "").strip()
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role_value = request.form.get("role")
        branch_id = request.form.get("branch_id") or None
        if not employee_id or not full_name or not email or not password or not role_value:
            flash("Employee ID, name, email, password and role are required.", "danger")
            return render_template("admin/user_form.html", user=None, branches=branches, roles=roles)
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "danger")
            return render_template("admin/user_form.html", user=None, branches=branches, roles=roles)
        try:
            role = UserRole[role_value]
        except KeyError:
            flash("Invalid role selected.", "danger")
            return render_template("admin/user_form.html", user=None, branches=branches, roles=roles)
        user = User(
            employee_id=employee_id,
            full_name=full_name,
            email=email,
            role=role,
            branch_id=int(branch_id) if branch_id else None,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash("User created successfully.", "success")
        return redirect(url_for("admin_view.users"))
    return render_template("admin/user_form.html", user=None, branches=branches, roles=roles)


@admin_view_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@require_role(UserRole.ADMIN)
def user_edit(user_id):
    user = User.query.get_or_404(user_id)
    branches = Branch.query.order_by(Branch.name).all()
    roles = list(UserRole)
    if request.method == "POST":
        user.employee_id = request.form.get("employee_id", user.employee_id).strip()
        user.full_name = request.form.get("full_name", user.full_name).strip()
        user.email = request.form.get("email", user.email).strip().lower()
        user.branch_id = int(request.form.get("branch_id")) if request.form.get("branch_id") else None
        user.is_active = bool(request.form.get("is_active"))
        role_value = request.form.get("role")
        if role_value:
            try:
                user.role = UserRole[role_value]
            except KeyError:
                flash("Invalid role selected.", "danger")
                return render_template("admin/user_form.html", user=user, branches=branches, roles=roles)
        password = request.form.get("password", "")
        if password:
            user.set_password(password)
        db.session.commit()
        flash("User updated successfully.", "success")
        return redirect(url_for("admin_view.users"))
    return render_template("admin/user_form.html", user=user, branches=branches, roles=roles)


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
