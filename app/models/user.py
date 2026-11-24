from app.extensions import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.utils.timeutil import utcnow
import enum


class UserRole(enum.Enum):
    ADMIN          = "admin"
    OPD            = "opd"
    PHARMACY       = "pharmacy"
    DENTIST        = "dentist"
    CARDIOLOGISTS  = "cardiologists"
    DERMATOLOGISTS = "dermatologists"
    BRANCH_MANAGER = "branch_manager"   # shown as "Location Manager"

    # Kept for existing databases; new accounts use the roles above.
    LOBBY_OFFICER  = "lobby_officer"
    LOAN_OFFICER   = "loan_officer"
    SPECIALIST_DOCTOR = "specialist_doctor"


ROLE_LABELS = {
    UserRole.ADMIN: "Admin",
    UserRole.OPD: "OPD",
    UserRole.PHARMACY: "Pharmacy",
    UserRole.DENTIST: "Dentist",
    UserRole.CARDIOLOGISTS: "Cardiologists",
    UserRole.DERMATOLOGISTS: "Dermatologists",
    UserRole.BRANCH_MANAGER: "Location Manager",
    UserRole.LOBBY_OFFICER: "Lobby Officer (legacy)",
    UserRole.LOAN_OFFICER: "Loan Officer (legacy)",
    UserRole.SPECIALIST_DOCTOR: "Specialist Doctor (legacy)",
}

DOCTOR_ROLES = (
    UserRole.DENTIST,
    UserRole.CARDIOLOGISTS,
    UserRole.DERMATOLOGISTS,
)
# Roles that serve patients from a service queue.
SERVICE_ROLES = (
    UserRole.OPD,
    UserRole.PHARMACY,
    *DOCTOR_ROLES,
)
# Roles an admin can give to a new or edited account.
ASSIGNABLE_ROLES = (
    UserRole.ADMIN,
    *SERVICE_ROLES,
    UserRole.BRANCH_MANAGER,
)
# Everyone who may use the staff console and queue actions.
STAFF_ROLES = (
    *SERVICE_ROLES,
    UserRole.LOBBY_OFFICER,
    UserRole.LOAN_OFFICER,
    UserRole.SPECIALIST_DOCTOR,
    UserRole.BRANCH_MANAGER,
)
# Roles that must belong to a hospital location.
BRANCH_REQUIRED_ROLES = tuple(role for role in STAFF_ROLES)

SPECIALTY_ROLE_VALUES = {role.value for role in DOCTOR_ROLES}

# New role -> legacy role name still found in old service configuration.
_LEGACY_SERVICE_ROLE = {
    UserRole.OPD: UserRole.LOBBY_OFFICER.value,
    UserRole.PHARMACY: UserRole.LOAN_OFFICER.value,
    UserRole.DENTIST: UserRole.SPECIALIST_DOCTOR.value,
    UserRole.CARDIOLOGISTS: UserRole.SPECIALIST_DOCTOR.value,
    UserRole.DERMATOLOGISTS: UserRole.SPECIALIST_DOCTOR.value,
}


def legacy_service_role(role):
    """The pre-hospital role name that grants the same service access."""
    return _LEGACY_SERVICE_ROLE.get(role)


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
    # Set when an admin chooses the password; cleared when the user changes it.
    must_change_password = db.Column(db.Boolean, nullable=False, default=False,
                                     server_default="0")
    last_login    = db.Column(db.DateTime)
    last_seen     = db.Column(db.DateTime)
    created_at    = db.Column(db.DateTime, default=utcnow)
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
    def role_label(self):
        return ROLE_LABELS.get(self.role, self.role.value.replace("_", " ").title())

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
            legacy = legacy_service_role(self.role)
            return [
                service.code for service in self._active_services()
                if self.role.value in (service.allowed_roles or [])
                or legacy in (service.allowed_roles or [])
            ]
        return [
            service.code for service in self._active_services()
            if service_is_compatible_with_role(self.role, service)
        ]

    @staticmethod
    def _active_services():
        from app.models.service_type import ServiceType
        return ServiceType.query.filter_by(is_active=True).all()
