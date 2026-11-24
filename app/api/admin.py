from flask import request, jsonify, current_app
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from app.api import admin_bp
from app.models import Branch, User, UserRole, AuditLog, Counter, ServiceType
from app.extensions import db
from app.services.audit_service import AuditService
from app.services import user_service
from app.services.user_service import UserInputError
from app.utils.decorators import require_role, audit_action
from app.utils.timeutil import iso_local
from app.utils.validators import validate_branch_code


def _json_object():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


@admin_bp.get("/branches")
@login_required
@require_role(UserRole.ADMIN)
def list_branches():
    branches = Branch.query.order_by(Branch.name).all()
    return jsonify([{
        "id": b.id, "name": b.name, "code": b.code,
        "city": b.city, "phone": b.phone, "is_active": b.is_active,
        "address": b.address,
    } for b in branches])


@admin_bp.post("/branches")
@login_required
@require_role(UserRole.ADMIN)
@audit_action("create_branch")
def create_branch():
    data = _json_object()
    if data is None:
        return jsonify({"error": "A JSON object is required"}), 400
    if not data.get("name") or not data.get("code"):
        return jsonify({"error": "Name and code are required"}), 400
    code = str(data["code"]).strip().upper()
    # The code is used in URLs and as a QR filename: restrict it strictly.
    error = validate_branch_code(code)
    if error:
        return jsonify({"error": error}), 400
    if Branch.query.filter_by(code=code).first():
        return jsonify({"error": "Branch code already exists"}), 409
    branch = Branch(
        name=str(data["name"])[:120], code=code,
        address=data.get("address"), city=data.get("city"), phone=data.get("phone"),
    )
    db.session.add(branch)
    db.session.commit()
    try:
        from app.utils.qr_generator import generate_branch_qr
        generate_branch_qr(branch)
    except Exception:
        current_app.logger.exception("QR generation failed for branch %s", branch.id)
    return jsonify({"id": branch.id, "code": branch.code, "name": branch.name}), 201


@admin_bp.put("/branches/<int:branch_id>")
@login_required
@require_role(UserRole.ADMIN)
@audit_action("update_branch")
def update_branch(branch_id):
    branch = db.get_or_404(Branch, branch_id)
    data   = _json_object()
    if data is None:
        return jsonify({"error": "A JSON object is required"}), 400
    for f in ["name", "address", "city", "phone"]:
        if f in data:
            if data[f] is not None and not isinstance(data[f], str):
                return jsonify({"error": f"{f} must be a string"}), 400
            setattr(branch, f, data[f])
    if "is_active" in data:
        if not isinstance(data["is_active"], bool):
            return jsonify({"error": "is_active must be a boolean"}), 400
        branch.is_active = data["is_active"]
    db.session.commit()
    return jsonify({"message": "Branch updated"})


@admin_bp.get("/branches/<int:branch_id>")
@login_required
@require_role(UserRole.ADMIN)
def get_branch(branch_id):
    branch = db.get_or_404(Branch, branch_id)
    return jsonify({
        "id": branch.id,
        "name": branch.name,
        "code": branch.code,
        "address": branch.address,
        "city": branch.city,
        "phone": branch.phone,
        "is_active": branch.is_active,
    })


@admin_bp.delete("/branches/<int:branch_id>")
@login_required
@require_role(UserRole.ADMIN)
@audit_action("deactivate_branch")
def deactivate_branch(branch_id):
    branch = db.get_or_404(Branch, branch_id)
    branch.is_active = False
    db.session.commit()
    return jsonify({"message": f"Branch {branch.name} deactivated"})


@admin_bp.get("/users")
@login_required
@require_role(UserRole.ADMIN)
def list_users():
    users = User.query.filter_by(is_active=True).order_by(User.full_name).all()
    return jsonify([{
        "id": u.id, "employee_id": u.employee_id, "name": u.full_name,
        "email": u.email, "role": u.role.value,
        "branch_id": u.branch_id,
        "branch": u.branch.name if u.branch else None,
        "services": [{
            "id": service.id,
            "name": service.name,
            "code": service.code,
        } for service in ServiceType.query.filter(
            ServiceType.code.in_(u.allowed_service_codes)
        ).order_by(ServiceType.name).all()],
        "last_login": iso_local(u.last_login),
    } for u in users])


@admin_bp.post("/users")
@login_required
@require_role(UserRole.ADMIN)
@audit_action("create_user")
def create_user():
    data = _json_object()
    if data is None:
        return jsonify({"error": "A JSON object is required"}), 400
    required = ["employee_id", "full_name", "email", "password", "role"]
    missing  = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing: {', '.join(missing)}"}), 400
    if any(not isinstance(data[f], str) for f in required):
        return jsonify({"error": "Fields must be strings"}), 400
    email = data["email"].strip().lower()
    employee_id = data["employee_id"].strip()
    try:
        user_service.check_password(data["password"])
        user_service.check_unique(email, employee_id)
        role = user_service.resolve_role(data["role"])
        branch_id = user_service.resolve_branch_id(data.get("branch_id"), role)
        services = user_service.resolve_services(data.get("service_ids", []), role)
    except UserInputError as error:
        status = 409 if "already registered" in str(error) else 400
        return jsonify({"error": str(error)}), status
    u = User(
        employee_id=employee_id, full_name=data["full_name"].strip(),
        email=email, role=role, branch_id=branch_id,
        assigned_services=services,
        must_change_password=bool(data.get("must_change_password", False)),
    )
    u.set_password(data["password"])
    db.session.add(u)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Invalid branch or duplicate user"}), 409
    AuditService.log(current_user.id, "user_created_detail",
                     {"user_id": u.id, "after": user_service.snapshot(u)})
    return jsonify({"id": u.id, "name": u.full_name}), 201


@admin_bp.put("/users/<int:user_id>")
@login_required
@require_role(UserRole.ADMIN)
@audit_action("update_user")
def update_user(user_id):
    user = db.get_or_404(User, user_id)
    data = _json_object()
    if data is None:
        return jsonify({"error": "A JSON object is required"}), 400
    if user.id == current_user.id and (
        data.get("is_active") is False
        or ("role" in data and str(data["role"]).upper() != user.role.name)
    ):
        return jsonify({"error": "You cannot deactivate or demote your own account"}), 400
    if "is_active" in data and not isinstance(data["is_active"], bool):
        return jsonify({"error": "is_active must be a boolean"}), 400

    before = user_service.snapshot(user)
    try:
        if "email" in data:
            email = str(data["email"]).strip().lower()
            if not email:
                raise UserInputError("Email is required.")
            user_service.check_unique(email, user.employee_id, exclude_id=user.id)
            user.email = email
        role = user_service.resolve_role(data["role"]) if "role" in data else user.role
        branch_raw = data["branch_id"] if "branch_id" in data else user.branch_id
        user.branch_id = user_service.resolve_branch_id(branch_raw, role)
        user.role = role
        if "service_ids" in data:
            user.assigned_services = user_service.resolve_services(data["service_ids"], role)
        else:
            user_service.resolve_services([s.id for s in user.assigned_services], role)
        if data.get("password"):
            user_service.check_password(data["password"])
            user.set_password(data["password"])
            user.must_change_password = bool(data.get("must_change_password", False))
    except UserInputError as error:
        db.session.rollback()
        status = 409 if "already registered" in str(error) else 400
        return jsonify({"error": str(error)}), status
    if "full_name" in data and str(data["full_name"]).strip():
        user.full_name = str(data["full_name"]).strip()[:120]
    if "is_active" in data:
        user.is_active = data["is_active"]
    db.session.commit()
    changes = AuditService.diff(before, user_service.snapshot(user))
    if data.get("password"):
        changes["password"] = "reset"
    AuditService.log(current_user.id, "user_updated_detail",
                     {"user_id": user.id, "changes": changes})
    return jsonify({"message": "User updated"})


@admin_bp.delete("/users/<int:user_id>")
@login_required
@require_role(UserRole.ADMIN)
@audit_action("deactivate_user")
def deactivate_user(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        return jsonify({"error": "You cannot deactivate your own account"}), 400
    user.is_active = False
    db.session.commit()
    return jsonify({"message": f"User {user.full_name} deactivated"})


@admin_bp.get("/audit-logs")
@login_required
@require_role(UserRole.ADMIN)
def audit_logs():
    page  = request.args.get("page", 1, type=int)
    per   = min(max(request.args.get("per_page", 50, type=int), 1), 200)
    logs  = (AuditLog.query
             .order_by(AuditLog.created_at.desc())
             .paginate(page=page, per_page=per, error_out=False))
    return jsonify({
        "items": [{
            "id":         l.id,
            "action":     l.action,
            "user":       l.user.full_name if l.user else "System",
            "ip":         l.ip_address,
            "payload":    l.payload,
            "created_at": iso_local(l.created_at),
        } for l in logs.items],
        "total": logs.total, "pages": logs.pages, "page": page,
    })


@admin_bp.get("/qr/<int:branch_id>")
@login_required
@require_role(UserRole.ADMIN, UserRole.BRANCH_MANAGER)
def generate_qr_code(branch_id):
    """Generate QR code for a branch."""
    branch = db.get_or_404(Branch, branch_id)
    if current_user.role != UserRole.ADMIN and current_user.branch_id != branch.id:
        return jsonify({"error": "Access denied"}), 403
    try:
        from app.utils.qr_generator import generate_branch_qr
        generate_branch_qr(branch)
        return jsonify({"qr_code_path": f"/static/qrcodes/{branch.code}.png",
                        "branch_code": branch.code}), 200
    except Exception:
        current_app.logger.exception("QR generation failed for branch %s", branch.id)
        return jsonify({"error": "QR generation failed"}), 500


@admin_bp.get("/customers")
@login_required
@require_role(UserRole.ADMIN)
def list_customers():
    from app.models import Customer, QueueToken
    from sqlalchemy import func
    page = request.args.get("page", 1, type=int)
    rows = (db.session.query(Customer, func.count(QueueToken.id).label("visits"))
            .outerjoin(QueueToken, QueueToken.customer_id == Customer.id)
            .group_by(Customer.id)
            .order_by(func.count(QueueToken.id).desc())
            .paginate(page=page, per_page=50, error_out=False))
    return jsonify({
        "items": [{
            "id":         c.id, "full_name": c.full_name,
            "mobile":     c.mobile, "visits": v,
            "registered": iso_local(c.created_at),
        } for c, v in rows.items],
        "total": rows.total, "pages": rows.pages, "page": page,
    })


@admin_bp.post("/qr/<int:branch_id>")
@login_required
@require_role(UserRole.ADMIN, UserRole.BRANCH_MANAGER)
def regen_qr(branch_id):
    branch = db.get_or_404(Branch, branch_id)
    if current_user.role != UserRole.ADMIN and current_user.branch_id != branch.id:
        return jsonify({"error": "Access denied"}), 403
    from app.utils.qr_generator import generate_branch_qr
    generate_branch_qr(branch)
    return jsonify({"message": "QR regenerated",
                    "path": f"/static/qrcodes/{branch.code}.png"})


@admin_bp.post("/counters")
@login_required
@require_role(UserRole.ADMIN, UserRole.BRANCH_MANAGER)
@audit_action("create_counter")
def create_counter():
    data = _json_object()
    if data is None or not isinstance(data.get("branch_id"), int) \
            or not isinstance(data.get("label"), str) or not data["label"].strip():
        return jsonify({"error": "branch_id (int) and label are required"}), 400
    # Branch managers may only manage counters in their own branch.
    if current_user.role != UserRole.ADMIN and data["branch_id"] != current_user.branch_id:
        return jsonify({"error": "Access denied"}), 403
    if not db.session.get(Branch, data["branch_id"]):
        return jsonify({"error": "Unknown branch"}), 400
    if data.get("user_id") is not None:
        staff = db.session.get(User, data["user_id"]) if isinstance(data["user_id"], int) else None
        if not staff or staff.branch_id != data["branch_id"]:
            return jsonify({"error": "user_id must be staff of the same branch"}), 400
    c = Counter(
        branch_id=data["branch_id"], label=data["label"].strip()[:20],
        service_scope=data.get("service_scope"), user_id=data.get("user_id"),
    )
    db.session.add(c)
    db.session.commit()
    return jsonify({"id": c.id, "label": c.label}), 201
