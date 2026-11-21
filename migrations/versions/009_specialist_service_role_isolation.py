"""Scope named specialty services to their matching doctor role."""

import json

from alembic import op
import sqlalchemy as sa


revision = "009_specialty_service_roles"
down_revision = "008_user_service_assignments"
branch_labels = None
depends_on = None


SERVICE_TYPES = sa.table(
    "service_types",
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("allowed_roles", sa.JSON),
)

SPECIALTY_ROLES = {"dentist", "cardiologists", "dermatologists"}


def _specialty_role(name):
    normalized = "".join(character for character in name.lower() if character.isalnum())
    if "dermat" in normalized:
        return "dermatologists"
    if "cardio" in normalized:
        return "cardiologists"
    if "dent" in normalized:
        return "dentist"
    return None


def _roles(value):
    if isinstance(value, str):
        value = json.loads(value)
    return list(value or [])


def upgrade():
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(SERVICE_TYPES.c.id, SERVICE_TYPES.c.name, SERVICE_TYPES.c.allowed_roles)
    ).fetchall()
    for service_id, name, stored_roles in rows:
        specialty_role = _specialty_role(name)
        if not specialty_role:
            continue
        allowed_roles = [
            role for role in _roles(stored_roles)
            if role not in SPECIALTY_ROLES and role != "specialist_doctor"
        ]
        allowed_roles.append(specialty_role)
        connection.execute(
            sa.update(SERVICE_TYPES)
            .where(SERVICE_TYPES.c.id == service_id)
            .values(allowed_roles=allowed_roles)
        )


def downgrade():
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(SERVICE_TYPES.c.id, SERVICE_TYPES.c.name, SERVICE_TYPES.c.allowed_roles)
    ).fetchall()
    for service_id, name, stored_roles in rows:
        if not _specialty_role(name):
            continue
        allowed_roles = [
            role for role in _roles(stored_roles)
            if role not in SPECIALTY_ROLES and role != "specialist_doctor"
        ]
        allowed_roles.append("specialist_doctor")
        connection.execute(
            sa.update(SERVICE_TYPES)
            .where(SERVICE_TYPES.c.id == service_id)
            .values(allowed_roles=allowed_roles)
        )
