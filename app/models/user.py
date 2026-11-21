from app.extensions import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
import enum

class UserRole(enum.Enum):
    ADMIN          = "admin"
    OPD            = "opd"
    PHARMACY       = "pharmacy"
    DENTIST        = "dentist"
    CARDIOLOGISTS  = "cardiologists"
    DERMATOLOGISTS = "dermatologists"

    # Kept for existing databases and routes; new accounts use the roles above.
    BRANCH_MANAGER = "branch_manager"
    LOBBY_OFFICER  = "lobby_officer"
    LOAN_OFFICER   = "loan_officer"
    SPECIALIST_DOCTOR = "specialist_doctor"


ASSIGNABLE_ROLES = (
    UserRole.ADMIN,
    UserRole.OPD,
    UserRole.PHARMACY,
    UserRole.DENTIST,
    UserRole.CARDIOLOGISTS,
    UserRole.DERMATOLOGISTS,
)
DOCTOR_ROLES = (
    UserRole.DENTIST,
    UserRole.CARDIOLOGISTS,
    UserRole.DERMATOLOGISTS,
)
SERVICE_ROLES = ASSIGNABLE_ROLES[1:]
SPECIALTY_ROLE_VALUES = {role.value for role in DOCTOR_ROLES}


def service_is_compatible_with_role(role, service):
    if role not in DOCTOR_ROLES:
        return True

    allowed_roles = set(service.allowed_roles or [])
    specialty_roles = allowed_roles.intersection(SPECIALTY_ROLE_VALUES)
    if specialty_roles:
        return role.value in specialty_roles
    return (
        role.value in allowed_roles
        or UserRole.SPECIALIST_DOCTOR.value in allowed_roles
    )

user_services = db.Table(
    "user_services",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id"), primary_key=True),
    db.Column(
        "service_type_id",
        db.Integer,
        db.ForeignKey("service_types.id"),
        primary_key=True,
    ),
)


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
    assigned_services = db.relationship(
        "ServiceType",
        secondary=user_services,
        back_populates="assigned_users",
    )
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
        if self.assigned_services:
            return [
                service.code for service in self.assigned_services
                if service.is_active
                and (
                    self.role not in DOCTOR_ROLES
                    or service_is_compatible_with_role(self.role, service)
                )
            ]
        if self.role not in DOCTOR_ROLES:
            return [
                service.code for service in self._active_services()
                if self.role.value in (service.allowed_roles or [])
                or self._legacy_service_role in (service.allowed_roles or [])
            ]
        return [
            service.code for service in self._active_services()
            if service_is_compatible_with_role(self.role, service)
        ]

    @property
    def _legacy_service_role(self):
        return {
            UserRole.OPD: UserRole.LOBBY_OFFICER.value,
            UserRole.PHARMACY: UserRole.LOAN_OFFICER.value,
            UserRole.DENTIST: UserRole.SPECIALIST_DOCTOR.value,
            UserRole.CARDIOLOGISTS: UserRole.SPECIALIST_DOCTOR.value,
            UserRole.DERMATOLOGISTS: UserRole.SPECIALIST_DOCTOR.value,
        }.get(self.role)

    @staticmethod
    def _active_services():
        from app.models.service_type import ServiceType
        return ServiceType.query.filter_by(is_active=True).all()
