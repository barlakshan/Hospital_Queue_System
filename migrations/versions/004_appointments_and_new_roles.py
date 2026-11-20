"""Add appointment requests and new staff roles."""

from alembic import op
import sqlalchemy as sa


revision = "004_appointments_and_new_roles"
down_revision = "003_specialist_doctor_role"
branch_labels = None
depends_on = None


USER_ROLE = sa.Enum(
    "ADMIN", "BRANCH_MANAGER", "LOBBY_OFFICER", "LOAN_OFFICER",
    "SPECIALIST_DOCTOR", "OPD", "PHARMACY", "DENTIST",
    "CARDIOLOGISTS", "DERMATOLOGISTS", name="userrole",
)
NEW_ROLES = ("OPD", "PHARMACY", "DENTIST", "CARDIOLOGISTS", "DERMATOLOGISTS")
LEGACY_ROLES = ("ADMIN", "BRANCH_MANAGER", "LOBBY_OFFICER", "LOAN_OFFICER", "SPECIALIST_DOCTOR")


def upgrade():
    connection = op.get_bind()
    dialect = connection.dialect.name

    if dialect == "postgresql":
        for role in NEW_ROLES:
            op.execute(f"ALTER TYPE userrole ADD VALUE IF NOT EXISTS '{role}'")
    elif dialect != "sqlite":
        op.alter_column(
            "users",
            "role",
            existing_type=sa.Enum(
                "ADMIN", "BRANCH_MANAGER", "LOBBY_OFFICER", "LOAN_OFFICER",
                "SPECIALIST_DOCTOR", name="userrole",
            ),
            type_=USER_ROLE,
            existing_nullable=False,
            nullable=False,
        )

    op.create_table(
        "appointment_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("branch_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("mobile", sa.String(length=20), nullable=False),
        sa.Column("requested_role", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    connection = op.get_bind()
    dialect = connection.dialect.name

    if dialect in {"postgresql", "mysql"}:
        role_list = ", ".join(f"'{role}'" for role in NEW_ROLES)
        new_role_count = connection.execute(
            sa.text(f"SELECT COUNT(*) FROM users WHERE role IN ({role_list})")
        ).scalar_one()
        if new_role_count:
            raise RuntimeError(
                "Cannot remove new staff roles while users with those roles exist."
            )

    if dialect == "postgresql":
        op.execute("ALTER TYPE userrole RENAME TO userrole_with_new_roles")
        roles = ", ".join(f"'{role}'" for role in LEGACY_ROLES)
        op.execute(f"CREATE TYPE userrole AS ENUM ({roles})")
        op.execute(
            "ALTER TABLE users ALTER COLUMN role TYPE userrole "
            "USING role::text::userrole"
        )
        op.execute("DROP TYPE userrole_with_new_roles")
    elif dialect != "sqlite":
        op.alter_column(
            "users",
            "role",
            existing_type=USER_ROLE,
            type_=sa.Enum(*LEGACY_ROLES, name="userrole"),
            existing_nullable=False,
            nullable=False,
        )

    op.drop_table("appointment_requests")
    if dialect == "sqlite":
        return
