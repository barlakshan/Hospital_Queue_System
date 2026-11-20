"""Assign services directly to staff users."""

import json

from alembic import op
import sqlalchemy as sa


revision = "008_user_service_assignments"
down_revision = "007_service_linked_appointments"
branch_labels = None
depends_on = None


LEGACY_ROLE = {
    "opd": "lobby_officer",
    "pharmacy": "loan_officer",
    "dentist": "specialist_doctor",
    "cardiologists": "specialist_doctor",
    "dermatologists": "specialist_doctor",
}


def upgrade():
    op.create_table(
        "user_services",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("service_type_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["service_type_id"], ["service_types.id"]),
        sa.PrimaryKeyConstraint("user_id", "service_type_id"),
    )

    connection = op.get_bind()
    users = connection.execute(sa.text(
        "SELECT id, role FROM users"
    )).fetchall()
    services = connection.execute(sa.text(
        "SELECT id, allowed_roles FROM service_types"
    )).fetchall()

    for user_id, stored_role in users:
        role = str(stored_role).lower()
        if role in {"admin", "branch_manager"}:
            continue
        compatible_roles = {role, LEGACY_ROLE.get(role)}
        for service_id, allowed_roles in services:
            if isinstance(allowed_roles, str):
                allowed_roles = json.loads(allowed_roles)
            if compatible_roles.intersection(allowed_roles or []):
                connection.execute(
                    sa.text(
                        "INSERT INTO user_services (user_id, service_type_id) "
                        "VALUES (:user_id, :service_id)"
                    ),
                    {"user_id": user_id, "service_id": service_id},
                )


def downgrade():
    op.drop_table("user_services")
