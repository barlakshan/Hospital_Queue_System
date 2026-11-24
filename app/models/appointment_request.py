from app.extensions import db
from app.utils.timeutil import utcnow


class AppointmentStatus:
    PENDING = "pending"
    SCHEDULED = "scheduled"
    REJECTED = "rejected"
    DONE = "done"

    ALL = (PENDING, SCHEDULED, REJECTED, DONE)
    OPEN = (PENDING, SCHEDULED)


class AppointmentRequest(db.Model):
    __tablename__ = "appointment_requests"

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=True)
    full_name = db.Column(db.String(120), nullable=False)
    mobile = db.Column(db.String(20), nullable=False)
    service_type_id = db.Column(db.Integer, db.ForeignKey("service_types.id"))
    requested_role = db.Column(db.String(24))
    status = db.Column(db.String(16), nullable=False, default=AppointmentStatus.PENDING,
                       server_default=AppointmentStatus.PENDING)
    handled_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    handled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    branch = db.relationship("Branch")
    service_type = db.relationship("ServiceType")
    customer = db.relationship("Customer")
    handler = db.relationship("User", foreign_keys=[handled_by])

    __table_args__ = (
        db.Index("ix_appointments_branch_status", "branch_id", "status"),
    )
