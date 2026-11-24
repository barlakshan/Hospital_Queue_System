"""Validation and change tracking for staff accounts.

Used by the admin JSON API, the admin HTML pages and the manager page so
the rules can't drift apart.
"""
from app.extensions import db
from app.models import (
    ASSIGNABLE_ROLES, BRANCH_REQUIRED_ROLES, DOCTOR_ROLES, Branch, ServiceType,
    User, UserRole, service_is_compatible_with_role,
)
from app.utils.validators import validate_password

# Roles that don't serve a queue and so need no service assignment.
_NO_SERVICE_ROLES = (UserRole.ADMIN, UserRole.BRANCH_MANAGER)


class UserInputError(ValueError):
    pass


def resolve_role(value, allowed=ASSIGNABLE_ROLES) -> UserRole:
    name = str(value or "").strip().upper()
    role = next((role for role in allowed if role.name == name), None)
    if role is None:
        raise UserInputError("Select a valid role.")
    return role


def resolve_branch_id(raw, role):
    """Parse an optional branch id; staff roles must belong to a location."""
    branch_id = None
    if raw not in (None, ""):
        try:
            branch_id = int(raw)
        except (TypeError, ValueError):
            raise UserInputError("Select a valid hospital location.")
        if isinstance(raw, bool) or not db.session.get(Branch, branch_id):
            raise UserInputError("Select a valid hospital location.")
    if branch_id is None and role in BRANCH_REQUIRED_ROLES:
        raise UserInputError("Select a hospital location for this staff member.")
    return branch_id


def resolve_services(service_ids, role):
    """Active services for the given ids, checked against the role."""
    if not isinstance(service_ids, (list, tuple, set)):
        raise UserInputError("service_ids must be a list.")
    try:
        ids = {int(value) for value in service_ids}
    except (TypeError, ValueError):
        raise UserInputError("Select valid services.")
    services = ServiceType.query.filter(
        ServiceType.id.in_(ids), ServiceType.is_active.is_(True),
    ).all() if ids else []
    if len(services) != len(ids):
        raise UserInputError("Select only active services.")
    if role not in _NO_SERVICE_ROLES and not services:
        raise UserInputError("Assign at least one service to this user.")
    if role in DOCTOR_ROLES and any(
        not service_is_compatible_with_role(role, service) for service in services
    ):
        raise UserInputError("Select services configured for this staff role.")
    return services


def check_password(password):
    error = validate_password(password)
    if error:
        raise UserInputError(error + ".")


def check_unique(email, employee_id, exclude_id=None):
    query = User.query.filter((User.email == email) | (User.employee_id == employee_id))
    if exclude_id is not None:
        query = query.filter(User.id != exclude_id)
    if query.first():
        raise UserInputError("Email or employee ID already registered.")


def snapshot(user) -> dict:
    """The audited fields of an account, for before/after comparison."""
    return {
        "employee_id": user.employee_id,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role.value if user.role else None,
        "branch_id": user.branch_id,
        "is_active": bool(user.is_active),
        "services": sorted(service.code for service in user.assigned_services),
    }
