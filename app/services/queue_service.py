import logging
from datetime import timedelta

from flask import current_app
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    ACTIVE_STATUSES, OPEN_STATUSES, Branch, Customer, PriorityReason,
    QueueToken, ServiceType, TokenStatus, UserRole,
)
from app.utils.timeutil import local_today, utcnow
from app.utils.validators import mask_mobile, normalise_mobile

logger = logging.getLogger(__name__)

_MAX_NUMBER_ATTEMPTS = 5


class TokenActionError(Exception):
    """Raised when a staff action on a token is not permitted."""

    def __init__(self, message, status_code=403):
        super().__init__(message)
        self.status_code = status_code


class RegistrationResult:
    """A token plus whether it already existed (idempotent re-registration)."""

    def __init__(self, token, existing=False):
        self.token = token
        self.existing = existing


def _push_update(branch_id):
    try:
        from app.services.notification_service import push_queue_update
        push_queue_update(branch_id)
    except Exception:
        logger.exception("Failed to push queue update for branch %s", branch_id)


class QueueService:

    # ── lookups ────────────────────────────────────────────────────────────
    @staticmethod
    def load_token_for_staff(token_id: int, user, allowed_states) -> QueueToken:
        """Fetch a token, enforcing branch, service and state rules.

        Staff may only act on tokens of their own branch and, unless they are
        a branch manager, only for services assigned to them.
        """
        token = db.session.get(QueueToken, token_id)
        if token is None:
            raise TokenActionError("Token not found", 404)
        if user.branch_id is None or token.branch_id != user.branch_id:
            raise TokenActionError("This token belongs to another hospital location")
        if (user.role != UserRole.BRANCH_MANAGER
                and token.service_type.code not in user.allowed_service_codes):
            raise TokenActionError("This token is outside your assigned services")
        if token.status not in allowed_states:
            raise TokenActionError(
                f"Token is {token.status.value} and cannot be changed this way", 409
            )
        return token

    @staticmethod
    def open_token_for(user):
        """The token this staff member has called and not yet finished."""
        return (QueueToken.query
                .filter(QueueToken.served_by == user.id,
                        QueueToken.queue_date == local_today(),
                        QueueToken.status.in_(ACTIVE_STATUSES))
                .order_by(QueueToken.called_at.desc())
                .first())

    # ── numbering ──────────────────────────────────────────────────────────
    @staticmethod
    def generate_token_number(branch_id: int, service_type_id: int) -> str:
        """Next token number for a service on today's queue.

        Uses the highest sequence already issued with the *current* prefix,
        so a prefix change mid-day restarts at 001 for the new prefix instead
        of colliding with an existing number. Concurrent callers can still
        compute the same number; register_customer() retries on the unique
        constraint.
        """
        service = db.session.get(ServiceType, service_type_id)
        if not service:
            raise ValueError(f"Service type {service_type_id} not found")

        numbers = (db.session.query(QueueToken.token_number)
                   .filter(QueueToken.branch_id == branch_id,
                           QueueToken.service_type_id == service_type_id,
                           QueueToken.queue_date == local_today(),
                           QueueToken.token_number.like(f"{service.prefix}%"))
                   .all())
        seq = 0
        for (number,) in numbers:
            suffix = number[len(service.prefix):]
            if suffix.isdigit():
                seq = max(seq, int(suffix))
        return f"{service.prefix}{seq + 1:03d}"

    # ── waiting estimates ──────────────────────────────────────────────────
    @staticmethod
    def get_position(token: QueueToken) -> int:
        """How many people are ahead using priority-first, FIFO ordering."""
        query = (QueueToken.query
                 .filter_by(branch_id=token.branch_id,
                            service_type_id=token.service_type_id,
                            queue_date=token.queue_date,
                            status=TokenStatus.WAITING))

        if token.is_priority:
            query = query.filter(QueueToken.is_priority.is_(True),
                                 QueueToken.id < token.id)
        else:
            query = query.filter(or_(
                QueueToken.is_priority.is_(True),
                (QueueToken.is_priority.is_(False)) & (QueueToken.id < token.id),
            ))

        return query.count()

    @staticmethod
    def staff_on_duty(branch_id: int, service_type_id: int) -> int:
        """Distinct staff who called a patient for this service in the last hour."""
        since = (utcnow() - timedelta(hours=1)).replace(tzinfo=None)
        count = (db.session.query(func.count(func.distinct(QueueToken.served_by)))
                 .filter(QueueToken.branch_id == branch_id,
                         QueueToken.service_type_id == service_type_id,
                         QueueToken.served_by.isnot(None),
                         QueueToken.called_at >= since)
                 .scalar()) or 0
        return max(1, count)

    @staticmethod
    def estimate_wait(token: QueueToken, position: int = None) -> int:
        """The single wait estimate used by every screen and the SMS."""
        if token.status != TokenStatus.WAITING:
            return 0
        if position is None:
            position = QueueService.get_position(token)
        per_patient = token.service_type.avg_service_min or 10
        staff = QueueService.staff_on_duty(token.branch_id, token.service_type_id)
        return -(-position * per_patient // staff)  # ceiling division

    @staticmethod
    def branch_wait_estimate(branch_id: int) -> int:
        """Average estimated wait for a new patient across active services."""
        rows = (db.session.query(QueueToken.service_type_id, func.count(QueueToken.id))
                .filter(QueueToken.branch_id == branch_id,
                        QueueToken.queue_date == local_today(),
                        QueueToken.status == TokenStatus.WAITING)
                .group_by(QueueToken.service_type_id).all())
        if not rows:
            return 0
        estimates = []
        for service_type_id, waiting in rows:
            service = db.session.get(ServiceType, service_type_id)
            staff = QueueService.staff_on_duty(branch_id, service_type_id)
            estimates.append(-(-waiting * (service.avg_service_min or 10) // staff))
        return round(sum(estimates) / len(estimates))

    @staticmethod
    def get_now_serving(branch_id: int) -> list:
        """Tokens currently called or being served, most recent call first."""
        return (QueueToken.query
                .filter(QueueToken.branch_id == branch_id,
                        QueueToken.status.in_(ACTIVE_STATUSES),
                        QueueToken.queue_date == local_today())
                .order_by(QueueToken.called_at.desc())
                .all())

    # ── registration ───────────────────────────────────────────────────────
    @staticmethod
    def register_customer(branch_id: int, full_name: str, mobile: str,
                          service_type_id: int, custom_reason: str = None,
                          priority_reason: PriorityReason = PriorityReason.NONE,
                          ) -> RegistrationResult:
        from app.services.sms_service import SmsService

        try:
            mobile = normalise_mobile(mobile)
        except ValueError:
            raise ValueError("Enter a valid mobile number, e.g. 0771234567")
        full_name = full_name.strip()

        branch = db.session.get(Branch, branch_id)
        if not branch or not branch.is_active:
            raise ValueError("Select an active hospital location")
        service = db.session.get(ServiceType, service_type_id)
        if not service or not service.is_active:
            raise ValueError("Select an active department or service")

        today = local_today()
        self_declared = current_app.config.get("ALLOW_SELF_DECLARED_PRIORITY", False)
        wants_priority = priority_reason != PriorityReason.NONE

        for attempt in range(_MAX_NUMBER_ATTEMPTS):
            customer = Customer.query.filter_by(mobile=mobile).first()
            if customer:
                # Idempotent: the same patient re-submitting for the same
                # service gets the token they already hold.
                existing = (QueueToken.query
                            .filter(QueueToken.customer_id == customer.id,
                                    QueueToken.service_type_id == service_type_id,
                                    QueueToken.branch_id == branch_id,
                                    QueueToken.queue_date == today,
                                    QueueToken.status.in_(OPEN_STATUSES))
                            .first())
                if existing:
                    return RegistrationResult(existing, existing=True)
                issued_today = QueueToken.query.filter_by(
                    customer_id=customer.id, queue_date=today).count()
                limit = current_app.config.get("MAX_TOKENS_PER_MOBILE_PER_DAY", 3)
                if issued_today >= limit:
                    raise ValueError(
                        f"This mobile number already has {issued_today} tokens today. "
                        "Please ask at the reception desk."
                    )
            else:
                # The master record keeps the first name given; the name for
                # this visit is stored on the token.
                customer = Customer(full_name=full_name, mobile=mobile)
                db.session.add(customer)

            token = QueueToken(
                branch_id=branch_id,
                customer=customer,
                service_type_id=service_type_id,
                token_number=QueueService.generate_token_number(branch_id, service_type_id),
                patient_name=full_name,
                custom_reason=custom_reason,
                is_priority=wants_priority and self_declared,
                priority_reason=priority_reason if self_declared else PriorityReason.NONE,
                priority_requested=priority_reason if wants_priority else None,
                queue_date=today,
            )
            db.session.add(token)
            try:
                db.session.flush()
            except IntegrityError:
                # Another registration took this number (or created this
                # patient) at the same moment: start again with fresh data.
                db.session.rollback()
                logger.info("Token number collision on attempt %s; retrying", attempt + 1)
                continue
            token.estimated_wait_min = QueueService.estimate_wait(token)
            db.session.commit()
            break
        else:
            raise ValueError("The queue is busy right now. Please try again.")

        try:
            SmsService.send_confirmation(token)
        except Exception:
            logger.exception("SMS confirmation failed for token %s", token.id)

        _push_update(branch_id)
        logger.info("Patient registered: token=%s mobile=%s",
                    token.token_number, mask_mobile(mobile))
        return RegistrationResult(token)

    # ── staff actions ──────────────────────────────────────────────────────
    @staticmethod
    def call_next(officer_user, counter_id: int) -> QueueToken:
        """
        Call the next eligible patient for this officer's services.
        Priority patients always come first, then FIFO.
        """
        branch_id = officer_user.branch_id
        if branch_id is None:
            raise TokenActionError("Your account is not assigned to a hospital location")

        open_token = QueueService.open_token_for(officer_user)
        if open_token:
            raise TokenActionError(
                f"Complete or mark no-show for token {open_token.token_number} first", 409
            )

        service_ids = [s.id for s in ServiceType.query.filter(
            ServiceType.code.in_(officer_user.allowed_service_codes)).all()]

        token = (QueueToken.query
                 .filter(QueueToken.branch_id == branch_id,
                         QueueToken.status == TokenStatus.WAITING,
                         QueueToken.queue_date == local_today(),
                         QueueToken.service_type_id.in_(service_ids))
                 .order_by(QueueToken.is_priority.desc(), QueueToken.id.asc())
                 .with_for_update(skip_locked=True)  # two officers must not get the same token
                 .first())

        if not token:
            return None

        token.status     = TokenStatus.CALLED
        token.counter_id = counter_id
        token.served_by  = officer_user.id
        token.called_at  = utcnow()
        db.session.commit()

        try:
            from app.services.sms_service import SmsService
            SmsService.send_call_notification(token)
        except Exception:
            logger.exception("SMS call notification failed for token %s", token.id)

        _push_update(branch_id)
        try:
            from app.extensions import socketio
            from app.services.notification_service import serving_payload
            # Other consoles of the same staff member (second tab/device).
            socketio.emit("token_called", serving_payload(token),
                          room=f"user_{officer_user.id}")
        except Exception:
            logger.exception("Failed to emit token_called")
        return token

    @staticmethod
    def start_service(token_id: int, officer_user) -> QueueToken:
        token = QueueService.load_token_for_staff(
            token_id, officer_user, {TokenStatus.CALLED})
        token.status   = TokenStatus.SERVING
        token.served_at = utcnow()
        db.session.commit()
        _push_update(token.branch_id)
        return token

    @staticmethod
    def complete_service(token_id: int, officer_user, condition_summary: str = None) -> QueueToken:
        token = QueueService.load_token_for_staff(
            token_id, officer_user, {TokenStatus.CALLED, TokenStatus.SERVING})
        token.status       = TokenStatus.COMPLETED
        token.completed_at = utcnow()
        if condition_summary:
            # Per-visit record only; the patient-level summary is curated by
            # staff and is never overwritten by a single visit.
            token.condition_summary = condition_summary
        db.session.commit()
        _push_update(token.branch_id)
        return token

    @staticmethod
    def mark_no_show(token_id: int, officer_user) -> QueueToken:
        token = QueueService.load_token_for_staff(
            token_id, officer_user,
            {TokenStatus.WAITING, TokenStatus.CALLED, TokenStatus.SERVING})
        token.status       = TokenStatus.NO_SHOW
        token.completed_at = utcnow()
        db.session.commit()
        _push_update(token.branch_id)
        return token

    @staticmethod
    def upgrade_priority(token_id: int, reason: PriorityReason, officer_user) -> QueueToken:
        """Confirm (or, with NONE, remove) priority for a waiting patient."""
        token = QueueService.load_token_for_staff(
            token_id, officer_user, {TokenStatus.WAITING})
        token.is_priority     = reason != PriorityReason.NONE
        token.priority_reason = reason
        db.session.commit()
        _push_update(token.branch_id)
        return token
