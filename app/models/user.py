from app.extensions import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
import enum

class UserRole(enum.Enum):
    ADMIN          = "admin"
    BRANCH_MANAGER = "branch_manager"
    LOBBY_OFFICER  = "lobby_officer"
    LOAN_OFFICER   = "loan_officer"
    SPECIALIST_DOCTOR = "specialist_doctor"

class User(UserMixin, db.Model):
    __tablename__ = "users"
    id            = db.Column(db.Integer, primary_key=True)
    branch_id     = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=True)
    employee_id   = db.Column(db.String(20), unique=True, nullable=False)
    full_name     = db.Column(db.String(120), nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role          = db.Column(db.Enum(UserRole), nullable=False)
    is_active     = db.Column(db.Boolean, default=True)
    last_login    = db.Column(db.DateTime)
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    branch        = db.relationship("Branch", back_populates="users")
    tokens_served = db.relationship("QueueToken", back_populates="served_by_officer",
                                    foreign_keys="QueueToken.served_by", lazy="dynamic")
    audit_logs    = db.relationship("AuditLog", back_populates="user", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def allowed_service_codes(self):
        if self.role in (UserRole.ADMIN, UserRole.BRANCH_MANAGER):
            return [service.code for service in self._active_services()]
        return [
            service.code for service in self._active_services()
            if self.role.value in (service.allowed_roles or [])
        ]

    @staticmethod
    def _active_services():
        from app.models.service_type import ServiceType
        return ServiceType.query.filter_by(is_active=True).all()
