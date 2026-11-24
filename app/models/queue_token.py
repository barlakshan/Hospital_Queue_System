import enum
import secrets

from app.extensions import db
from app.utils.timeutil import local_today, minutes_between, utcnow


class TokenStatus(enum.Enum):
    WAITING   = "waiting"
    CALLED    = "called"
    SERVING   = "serving"
    COMPLETED = "completed"
    NO_SHOW   = "no_show"
    SKIPPED   = "skipped"


OPEN_STATUSES = (TokenStatus.WAITING, TokenStatus.CALLED, TokenStatus.SERVING)
ACTIVE_STATUSES = (TokenStatus.CALLED, TokenStatus.SERVING)


class PriorityReason(enum.Enum):
    NONE     = "none"
    EMERGENCY = "emergency"
    URGENT   = "urgent"
    ELDERLY  = "elderly"
    PREGNANT = "pregnant"
    DISABLED = "disabled"
    CHILD    = "child"


def new_public_ref() -> str:
    return secrets.token_urlsafe(16)


class QueueToken(db.Model):
    __tablename__ = "queue_tokens"
    id                 = db.Column(db.Integer, primary_key=True)
    # Unguessable reference used in every public (patient-facing) URL.
    public_ref         = db.Column(db.String(32), nullable=False, default=new_public_ref)
    branch_id          = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=False)
    customer_id        = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    service_type_id    = db.Column(db.Integer, db.ForeignKey("service_types.id"), nullable=False)
    counter_id         = db.Column(db.Integer, db.ForeignKey("counters.id"), nullable=True)
    served_by          = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    token_number       = db.Column(db.String(10), nullable=False)
    # Name as given for this visit; the patient master record is never
    # changed by the public registration form.
    patient_name       = db.Column(db.String(120), nullable=True)
    custom_reason      = db.Column(db.Text, nullable=True)
    status             = db.Column(db.Enum(TokenStatus), nullable=False,
                                   default=TokenStatus.WAITING)
    is_priority        = db.Column(db.Boolean, nullable=False, default=False)
    priority_reason    = db.Column(db.Enum(PriorityReason), nullable=False,
                                   default=PriorityReason.NONE)
    # Priority the patient asked for on the public form; staff confirm it.
    priority_requested = db.Column(db.Enum(PriorityReason), nullable=True)
    estimated_wait_min = db.Column(db.Integer, default=0)
    condition_summary  = db.Column(db.String(500), nullable=True)
    issued_at          = db.Column(db.DateTime, default=utcnow)
    called_at          = db.Column(db.DateTime, nullable=True)
    served_at          = db.Column(db.DateTime, nullable=True)
    completed_at       = db.Column(db.DateTime, nullable=True)
    queue_date         = db.Column(db.Date, default=local_today)

    branch            = db.relationship("Branch", back_populates="tokens")
    customer          = db.relationship("Customer", back_populates="tokens")
    service_type      = db.relationship("ServiceType", back_populates="tokens")
    counter           = db.relationship("Counter", back_populates="tokens")
    served_by_officer = db.relationship("User", back_populates="tokens_served",
                                         foreign_keys=[served_by])
    sms_logs    = db.relationship("SmsLog", back_populates="token", lazy="dynamic")
    rating      = db.relationship("SatisfactionRating", back_populates="token", uselist=False)
    audit_logs  = db.relationship("AuditLog", back_populates="token", lazy="dynamic")

    __table_args__ = (
        db.UniqueConstraint("branch_id", "service_type_id", "token_number", "queue_date",
                            name="uq_token_per_service_per_day"),
        db.UniqueConstraint("public_ref", name="uq_queue_tokens_public_ref"),
        db.Index("ix_tokens_branch_date_status", "branch_id", "queue_date", "status"),
        db.Index("ix_tokens_customer", "customer_id"),
        db.Index("ix_tokens_served_by", "served_by"),
    )

    @property
    def display_name(self):
        """Name to show staff for this visit."""
        if self.patient_name:
            return self.patient_name
        return self.customer.full_name if self.customer else ""

    @property
    def actual_wait_minutes(self):
        return minutes_between(self.issued_at, self.called_at)

    @property
    def service_duration_minutes(self):
        # Consultations that were never explicitly "started" are measured
        # from the moment the patient was called.
        return minutes_between(self.served_at or self.called_at, self.completed_at)
