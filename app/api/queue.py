import logging

from flask import current_app, flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.api import queue_bp
from app.extensions import db
from app.models import (
    DOCTOR_ROLES, STAFF_ROLES, AppointmentRequest, AppointmentStatus, Branch,
    Counter, Customer, PriorityReason, QueueToken, SatisfactionRating,
    ServiceType, TokenStatus, UserRole, legacy_service_role,
)
from app.services.notification_service import build_display_state, serving_payload
from app.services.queue_service import QueueService, TokenActionError
from app.utils.decorators import audit_action, require_role
from app.utils.timeutil import iso_local, local_day_start_utc, local_today, utcnow
from app.utils.validators import MOBILE_INPUT_RE, normalise_mobile

logger = logging.getLogger(__name__)

PRIORITY_CHOICES = "none, emergency, urgent, elderly, pregnant, disabled, or child"


@queue_bp.errorhandler(TokenActionError)
def handle_token_action_error(error):
    db.session.rollback()
    return jsonify({"error": str(error)}), error.status_code


def _json_dict():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _public_token_or_404(token_ref) -> QueueToken:
    return QueueToken.query.filter_by(public_ref=token_ref).first_or_404()


def _service_for_specialty(requested_role):
    active_services = ServiceType.query.filter_by(is_active=True).all()
    service = next(
        (candidate for candidate in active_services
         if requested_role.value in (candidate.allowed_roles or [])),
        None,
    )
    legacy_role = legacy_service_role(requested_role)
    if service is None and legacy_role:
        service = next(
            (candidate for candidate in active_services
             if legacy_role in (candidate.allowed_roles or [])
             and not any(role.value in (candidate.allowed_roles or [])
                         for role in DOCTOR_ROLES)),
            None,
        )
    return service


# ── public: appointment requests ──────────────────────────────────────────
@queue_bp.post("/appointments")
def request_appointment():
    data = _json_dict()
    if data is None:
        return jsonify({"error": "A JSON object is required"}), 400
    required = ("branch_id", "full_name", "mobile")
    missing = [field for field in required if not data.get(field)]
    if not data.get("service_type_id") and not data.get("requested_role"):
        missing.append("service_type_id")
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    try:
        branch_id = int(data["branch_id"])
    except (ValueError, TypeError):
        return jsonify({"error": "branch_id must be an integer"}), 400

    branch = Branch.query.filter_by(id=branch_id, is_active=True).first()
    if not branch:
        return jsonify({"error": "Select an active hospital location"}), 400

    requested_role = None
    if data.get("service_type_id"):
        try:
            service_type_id = int(data["service_type_id"])
        except (ValueError, TypeError):
            return jsonify({"error": "service_type_id must be an integer"}), 400
        service = ServiceType.query.filter_by(id=service_type_id, is_active=True).first()
        if not service:
            return jsonify({"error": "Select an active department or service"}), 400
    else:
        requested_role = next(
            (role for role in DOCTOR_ROLES
             if role.value == str(data["requested_role"]).lower()),
            None,
        )
        if requested_role is None:
            return jsonify({"error": "Select a valid doctor specialty"}), 400
        service = _service_for_specialty(requested_role)
        if not service:
            return jsonify({"error": "No active service is configured for this specialty"}), 400

    full_name = str(data["full_name"]).strip()
    if not 2 <= len(full_name) <= 120:
        return jsonify({"error": "Full name must be 2-120 characters"}), 400
    try:
        mobile = normalise_mobile(str(data["mobile"]))
    except ValueError:
        return jsonify({"error": "Enter a valid mobile number, e.g. 0771234567"}), 400

    # Duplicate protection: one open request per patient and service, and a
    # small daily cap per mobile number.
    duplicate = AppointmentRequest.query.filter(
        AppointmentRequest.mobile == mobile,
        AppointmentRequest.branch_id == branch.id,
        AppointmentRequest.service_type_id == service.id,
        AppointmentRequest.status.in_(AppointmentStatus.OPEN),
    ).first()
    if duplicate:
        return jsonify({
            "request_id": duplicate.id, "status": duplicate.status,
            "service_type_id": service.id, "service": service.name,
            "branch": branch.name, "existing": True,
        }), 200
    day_start = local_day_start_utc()
    today_count = AppointmentRequest.query.filter(
        AppointmentRequest.mobile == mobile,
        AppointmentRequest.created_at >= day_start,
    ).count()
    if today_count >= current_app.config.get("MAX_APPOINTMENTS_PER_MOBILE_PER_DAY", 3):
        return jsonify({"error": "Too many requests for this mobile number today"}), 429

    appointment_request = AppointmentRequest(
        branch=branch,
        full_name=full_name,
        mobile=mobile,
        customer=Customer.query.filter_by(mobile=mobile).first(),
        service_type=service,
        requested_role=requested_role.value if requested_role else None,
        status=AppointmentStatus.PENDING,
    )
    db.session.add(appointment_request)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        raise

    return jsonify({
        "request_id": appointment_request.id,
        "status": appointment_request.status,
        "service_type_id": service.id,
        "service": service.name,
        "branch": branch.name,
    }), 201


def _can_handle_appointment(user, appointment):
    if user.role == UserRole.ADMIN:
        return True
    if user.branch_id != appointment.branch_id:
        return False
    if user.role == UserRole.BRANCH_MANAGER:
        return True
    return (appointment.service_type is not None
            and appointment.service_type.code in user.allowed_service_codes)


@queue_bp.post("/appointments/<int:request_id>/status")
@login_required
@require_role(UserRole.ADMIN, *STAFF_ROLES)
@audit_action("update_appointment_status")
def update_appointment_status(request_id):
    """Staff move an appointment request through pending → scheduled/rejected → done."""
    wants_json = request.is_json
    data = (_json_dict() or {}) if wants_json else request.form
    new_status = str(data.get("status") or "").lower()

    def reply(message, code):
        if wants_json:
            return jsonify({"error" if code >= 400 else "message": message}), code
        flash(message, "danger" if code >= 400 else "success")
        return redirect(request.referrer or url_for("index"))

    if new_status not in AppointmentStatus.ALL:
        return reply("Invalid appointment status", 400)
    appointment = db.session.get(AppointmentRequest, request_id)
    if appointment is None:
        return reply("Appointment request not found", 404)
    if not _can_handle_appointment(current_user, appointment):
        return reply("You cannot handle this appointment request", 403)

    appointment.status = new_status
    appointment.handled_by = current_user.id
    appointment.handled_at = utcnow()
    db.session.commit()
    return reply(f"Appointment request marked {new_status}", 200)


# ── public: registration and token status ─────────────────────────────────
@queue_bp.get("/branch/<string:branch_code>/info")
def branch_info(branch_code):
    branch = Branch.query.filter_by(code=branch_code.upper(), is_active=True).first_or_404()
    waiting = QueueToken.query.filter(
        QueueToken.branch_id == branch.id,
        QueueToken.queue_date == local_today(),
        QueueToken.status == TokenStatus.WAITING,
    ).count()
    return jsonify({
        "id": branch.id, "name": branch.name, "city": branch.city,
        "phone": branch.phone, "waiting_now": waiting,
        "avg_wait_min": QueueService.branch_wait_estimate(branch.id),
    })


@queue_bp.post("/register")
def register():
    data = _json_dict()
    if data is None:
        return jsonify({"error": "A JSON object is required"}), 400
    required = ["branch_id", "full_name", "mobile", "service_type_id"]
    missing  = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    try:
        branch_id = int(data["branch_id"])
        service_type_id = int(data["service_type_id"])
    except (ValueError, TypeError):
        return jsonify({"error": "branch_id and service_type_id must be integers"}), 400

    full_name = str(data["full_name"]).strip()
    mobile = str(data["mobile"]).strip()
    if not 2 <= len(full_name) <= 120:
        return jsonify({"error": "Full name must be 2-120 characters"}), 400
    if not MOBILE_INPUT_RE.fullmatch(mobile):
        return jsonify({"error": "Valid mobile number is required"}), 400

    custom_reason = data.get("custom_reason")
    if custom_reason is not None:
        custom_reason = str(custom_reason).strip()[:500] or None

    try:
        priority_reason = PriorityReason[str(data.get("priority_reason") or "NONE").upper()]
    except KeyError:
        return jsonify({"error": f"Invalid priority reason. Choose {PRIORITY_CHOICES}."}), 400

    try:
        result = QueueService.register_customer(
            branch_id=branch_id,
            full_name=full_name,
            mobile=mobile,
            service_type_id=service_type_id,
            custom_reason=custom_reason,
            priority_reason=priority_reason,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        db.session.rollback()
        logger.exception("Registration failed")
        return jsonify({"error": "Registration failed. Please try again."}), 500

    token = result.token
    position = QueueService.get_position(token)
    return jsonify({
        "token_id":           token.id,
        "token_ref":          token.public_ref,
        "token_number":       token.token_number,
        "service":            token.service_type.name,
        "service_code":       token.service_type.code,
        "position":           position,
        "estimated_wait":     QueueService.estimate_wait(token, position),
        "is_priority":        token.is_priority,
        "priority_requested": token.priority_requested.value if token.priority_requested else None,
        "branch_name":        token.branch.name,
        "existing":           result.existing,
    }), 200 if result.existing else 201


@queue_bp.get("/token/<string:token_ref>/status")
def token_status(token_ref):
    token = _public_token_or_404(token_ref)
    position = QueueService.get_position(token) if token.status == TokenStatus.WAITING else 0
    now_serving = (QueueToken.query
                   .filter_by(branch_id=token.branch_id,
                              service_type_id=token.service_type_id,
                              queue_date=token.queue_date)
                   .filter(QueueToken.status.in_([TokenStatus.CALLED, TokenStatus.SERVING]))
                   .order_by(QueueToken.called_at.desc())
                   .first())
    return jsonify({
        "token_number":   token.token_number,
        "status":         token.status.value,
        "position":       position,
        "estimated_wait": QueueService.estimate_wait(token, position),
        "now_serving":    now_serving.token_number if now_serving else None,
        "counter":        token.counter.label if token.counter else None,
        "officer":        token.served_by_officer.full_name if token.served_by_officer else None,
        "called_at":      iso_local(token.called_at),
    })


@queue_bp.post("/token/<string:token_ref>/rate")
def rate_service(token_ref):
    data = _json_dict() or {}

    try:
        rating_val = int(data.get("rating", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "rating must be an integer"}), 400

    if not (1 <= rating_val <= 5):
        return jsonify({"error": "Rating must be between 1 and 5"}), 400

    token = _public_token_or_404(token_ref)
    if token.status != TokenStatus.COMPLETED:
        return jsonify({"error": "Only completed visits can be rated"}), 409
    if token.rating:
        return jsonify({"error": "Token already rated"}), 409

    comment = str(data.get("comment") or "").strip()[:500]
    try:
        db.session.add(SatisfactionRating(token_id=token.id, rating=rating_val, comment=comment))
        db.session.commit()
        return jsonify({"message": "Rating submitted. Thank you!"})
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Rating failed for token %s", token.id)
        return jsonify({"error": "Failed to submit rating"}), 500


# ── staff actions ─────────────────────────────────────────────────────────
@queue_bp.post("/call-next")
@login_required
@require_role(*STAFF_ROLES)
@audit_action("call_next")
def call_next():
    data       = _json_dict() or {}
    counter_id = data.get("counter_id")

    if counter_id is not None:
        try:
            counter_id = int(counter_id)
        except (ValueError, TypeError):
            return jsonify({"error": "counter_id must be an integer"}), 400
        counter = db.session.get(Counter, counter_id)
        if not counter or counter.branch_id != current_user.branch_id:
            return jsonify({"error": "Invalid counter"}), 400

    try:
        token = QueueService.call_next(current_user, counter_id)
    except TokenActionError:
        raise
    except Exception:
        db.session.rollback()
        logger.exception("call_next failed")
        return jsonify({"error": "Unable to call the next patient"}), 500
    if not token:
        return jsonify({"message": "No customers waiting"}), 204
    return jsonify(serving_payload(token))


@queue_bp.get("/current")
@login_required
@require_role(*STAFF_ROLES)
def current_token():
    """The patient this staff member is serving, so a reloaded console can resume."""
    token = QueueService.open_token_for(current_user)
    return jsonify(serving_payload(token) if token else None)


@queue_bp.post("/token/<int:token_id>/start")
@login_required
@require_role(*STAFF_ROLES)
@audit_action("start_service")
def start_service(token_id):
    token = QueueService.start_service(token_id, current_user)
    return jsonify({"message": "Consultation started", "token": token.token_number})


@queue_bp.post("/token/<int:token_id>/complete")
@login_required
@require_role(*STAFF_ROLES)
@audit_action("complete_service")
def complete_service(token_id):
    payload = _json_dict() or {}
    condition_summary = payload.get("condition_summary")
    if condition_summary is not None:
        if not isinstance(condition_summary, str):
            return jsonify({"error": "Condition summary must be text."}), 400
        condition_summary = condition_summary.strip()
        if len(condition_summary) > 500:
            return jsonify({"error": "Condition summary must be 500 characters or fewer."}), 400
    token = QueueService.complete_service(token_id, current_user, condition_summary or None)
    return jsonify({"message": "Service completed", "token": token.token_number})


@queue_bp.post("/token/<int:token_id>/no-show")
@login_required
@require_role(*STAFF_ROLES)
@audit_action("mark_no_show")
def mark_no_show(token_id):
    token = QueueService.mark_no_show(token_id, current_user)
    return jsonify({"message": "Marked as no-show", "token": token.token_number})


@queue_bp.post("/token/<int:token_id>/priority")
@login_required
@require_role(*STAFF_ROLES)
@audit_action("set_priority")
def upgrade_priority(token_id):
    data   = _json_dict() or {}
    reason = str(data.get("priority_reason") or "").upper()
    try:
        priority_reason = PriorityReason[reason]
    except KeyError:
        return jsonify({"error": f"Invalid priority reason. Choose {PRIORITY_CHOICES}."}), 400

    token = QueueService.upgrade_priority(token_id, priority_reason, current_user)
    message = "Priority removed" if priority_reason == PriorityReason.NONE else "Priority set"
    return jsonify({"message": message, "token": token.token_number})


@queue_bp.get("/waiting")
@login_required
@require_role(*STAFF_ROLES)
def get_waiting():
    service_ids = [s.id for s in ServiceType.query.filter(
        ServiceType.code.in_(current_user.allowed_service_codes)).all()]
    tokens = (QueueToken.query
              .filter_by(branch_id=current_user.branch_id,
                         status=TokenStatus.WAITING,
                         queue_date=local_today())
              .filter(QueueToken.service_type_id.in_(service_ids))
              .order_by(QueueToken.is_priority.desc(), QueueToken.id.asc())
              .all())
    return jsonify([{
        "id":                 t.id,
        "token_number":       t.token_number,
        "customer_name":      t.display_name,
        "service":            t.service_type.name,
        "service_code":       t.service_type.code,
        "is_priority":        t.is_priority,
        "priority_reason":    t.priority_reason.value,
        "priority_requested": t.priority_requested.value if t.priority_requested else None,
        "estimated_wait":     QueueService.estimate_wait(t),
        "issued_at":          iso_local(t.issued_at),
        "custom_reason":      t.custom_reason,
    } for t in tokens])


@queue_bp.get("/display/<string:branch_code>")
def display_state(branch_code):
    branch = Branch.query.filter_by(code=branch_code.upper(), is_active=True).first_or_404()
    return jsonify(build_display_state(branch))
