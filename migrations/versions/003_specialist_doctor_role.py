"""Add the specialist doctor role."""

import json

from alembic import op
import sqlalchemy as sa


revision = "003_specialist_doctor_role"
down_revision = "002_hospital_services"
branch_labels = None
depends_on = None


OLD_USER_ROLE = sa.Enum(
    "ADMIN", "BRANCH_MANAGER", "LOBBY_OFFICER", "LOAN_OFFICER",
    name="userrole",
)
NEW_USER_ROLE = sa.Enum(
    "ADMIN", "BRANCH_MANAGER", "LOBBY_OFFICER", "LOAN_OFFICER",
    "SPECIALIST_DOCTOR", name="userrole",
)
SERVICE_TYPES = sa.table(
    "service_types",
    sa.column("code", sa.String),
    sa.column("allowed_roles", sa.JSON),
)


def _update_specialist_service_role(add_role):
    connection = op.get_bind()
    roles = connection.execute(
        sa.select(SERVICE_TYPES.c.allowed_roles).where(SERVICE_TYPES.c.code == "S")
    ).scalar_one_or_none()
    if roles is None:
        return
    if isinstance(roles, str):
        roles = json.loads(roles)

    if add_role and "specialist_doctor" not in roles:
        roles.append("specialist_doctor")
    elif not add_role:
        roles = [role for role in roles if role != "specialist_doctor"]
    else:
        return

    connection.execute(
        sa.update(SERVICE_TYPES)
        .where(SERVICE_TYPES.c.code == "S")
        .values(allowed_roles=roles)
    )


def _update_user_role_type(add_role):
    connection = op.get_bind()
    dialect = connection.dialect.name

    if dialect == "sqlite":
        return
    if dialect == "postgresql":
        if add_role:
            op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'SPECIALIST_DOCTOR'")
            return
        specialist_count = connection.execute(
            sa.text("SELECT COUNT(*) FROM users WHERE role = 'SPECIALIST_DOCTOR'")
        ).scalar_one()
        if specialist_count:
            raise RuntimeError(
                "Cannot remove Specialist Doctor role while specialist doctor users exist."
            )
        op.execute("ALTER TYPE userrole RENAME TO userrole_old")
        op.execute(
            "CREATE TYPE userrole AS ENUM "
            "('ADMIN', 'BRANCH_MANAGER', 'LOBBY_OFFICER', 'LOAN_OFFICER')"
        )
        op.execute(
            "ALTER TABLE users ALTER COLUMN role TYPE userrole "
            "USING role::text::userrole"
        )
        op.execute("DROP TYPE userrole_old")
        return

    op.alter_column(
        "users",
        "role",
        existing_type=OLD_USER_ROLE if add_role else NEW_USER_ROLE,
        type_=NEW_USER_ROLE if add_role else OLD_USER_ROLE,
        existing_nullable=False,
        nullable=False,
    )


def upgrade():
    _update_user_role_type(add_role=True)
    _update_specialist_service_role(add_role=True)


def downgrade():
    _update_specialist_service_role(add_role=False)
    _update_user_role_type(add_role=False)
