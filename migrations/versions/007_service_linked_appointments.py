"""Link appointment requests to services instead of only staff roles."""

import json

from alembic import op
import sqlalchemy as sa


revision = "007_service_linked_appointments"
down_revision = "006_visit_condition_summaries"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("appointment_requests") as batch_op:
        batch_op.alter_column(
            "requested_role",
            existing_type=sa.String(length=24),
            nullable=True,
        )
        batch_op.add_column(sa.Column("service_type_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_appointment_requests_service_type_id",
            "service_types",
            ["service_type_id"],
            ["id"],
        )

    connection = op.get_bind()
    requests = connection.execute(sa.text(
        "SELECT id, requested_role FROM appointment_requests "
        "WHERE requested_role IS NOT NULL"
    )).fetchall()
    services = connection.execute(sa.text(
        "SELECT id, allowed_roles FROM service_types WHERE is_active = true"
    )).fetchall()
    legacy_roles = {
        "dentist": "specialist_doctor",
        "cardiologists": "specialist_doctor",
        "dermatologists": "specialist_doctor",
    }

    for request_id, role in requests:
        matching_service = None
        compatible_roles = {role, legacy_roles.get(role)}
        for service_id, allowed_roles in services:
            if isinstance(allowed_roles, str):
                allowed_roles = json.loads(allowed_roles)
            if compatible_roles.intersection(allowed_roles or []):
                matching_service = service_id
                break
        if matching_service is not None:
            connection.execute(
                sa.text(
                    "UPDATE appointment_requests SET service_type_id = :service_id "
                    "WHERE id = :request_id"
                ),
                {"service_id": matching_service, "request_id": request_id},
            )


def downgrade():
    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE appointment_requests SET requested_role = 'service' "
        "WHERE requested_role IS NULL"
    ))
    with op.batch_alter_table("appointment_requests") as batch_op:
        batch_op.drop_constraint(
            "fk_appointment_requests_service_type_id", type_="foreignkey"
        )
        batch_op.drop_column("service_type_id")
        batch_op.alter_column(
            "requested_role",
            existing_type=sa.String(length=24),
            nullable=False,
        )
