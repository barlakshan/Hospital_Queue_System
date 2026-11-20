from datetime import datetime, timezone

from app.extensions import db


class AppointmentRequest(db.Model):
    __tablename__ = "appointment_requests"

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    mobile = db.Column(db.String(20), nullable=False)
    service_type_id = db.Column(db.Integer, db.ForeignKey("service_types.id"))
    requested_role = db.Column(db.String(24))
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    branch = db.relationship("Branch")
    service_type = db.relationship("ServiceType")
