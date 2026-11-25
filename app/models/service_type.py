from app.extensions import db

class ServiceType(db.Model):
    __tablename__ = "service_types"
    id              = db.Column(db.Integer, primary_key=True)
    code            = db.Column(db.String(5), unique=True, nullable=False)
    name            = db.Column(db.String(80), nullable=False)
    # Optional parent department, e.g. "Dental" -> "Specialist Consultation".
    department      = db.Column(db.String(80), nullable=True)
    prefix          = db.Column(db.String(2), nullable=False)
    avg_service_min = db.Column(db.Integer, default=10)
    is_active       = db.Column(db.Boolean, default=True)
    allowed_roles   = db.Column(db.JSON, nullable=False, default=list)

    @property
    def department_name(self):
        """Where the patient goes: the department, else the service itself."""
        return self.department or self.name

    tokens = db.relationship("QueueToken", back_populates="service_type", lazy="dynamic")
    assigned_users = db.relationship(
        "User",
        secondary="user_services",
        back_populates="assigned_services",
    )
