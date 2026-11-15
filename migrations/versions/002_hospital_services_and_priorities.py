"""Add hospital service configuration and priority categories."""

from alembic import op
import sqlalchemy as sa


revision = "002_hospital_services"
down_revision = "001_initial"
branch_labels = None
depends_on = None


HOSPITAL_SERVICES = (
    ("G", "General OPD", "G", 10, '["lobby_officer"]'),
    ("S", "Specialist Consultation", "S", 15, '["lobby_officer","loan_officer"]'),
    ("L", "Laboratory", "L", 8, '["loan_officer"]'),
    ("R", "Radiology", "R", 12, '["loan_officer"]'),
    ("P", "Pharmacy", "P", 6, '["lobby_officer","loan_officer"]'),
    ("B", "Billing / Cashier", "B", 5, '["lobby_officer","loan_officer"]'),
)

OLD_PRIORITY_REASON = sa.Enum(
    "NONE", "ELDERLY", "PREGNANT", "DISABLED", name="priorityreason"
)
HOSPITAL_PRIORITY_REASON = sa.Enum(
    "NONE", "EMERGENCY", "URGENT", "ELDERLY", "PREGNANT", "DISABLED", "CHILD",
    name="priorityreason"
)

ENUM_COLUMNS = (
    ("users", "role", "userrole", ("admin", "branch_manager", "lobby_officer", "loan_officer"),
     ("ADMIN", "BRANCH_MANAGER", "LOBBY_OFFICER", "LOAN_OFFICER")),
    ("queue_tokens", "status", "tokenstatus",
     ("waiting", "called", "serving", "completed", "no_show", "skipped"),
     ("WAITING", "CALLED", "SERVING", "COMPLETED", "NO_SHOW", "SKIPPED")),
    ("sms_logs", "sms_type", "smstype", ("confirmation", "call_notification"),
     ("CONFIRMATION", "CALL_NOTIFICATION")),
    ("sms_logs", "status", "smsstatus", ("pending", "sent", "failed"),
     ("PENDING", "SENT", "FAILED")),
)


def _normalize_enum_column(table, column, enum_name, old_values, new_values):
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        connection.execute(sa.text(
            f"UPDATE {table} SET {column} = UPPER({column}) WHERE {column} IS NOT NULL"
        ))
        return

    transitional = tuple(dict.fromkeys((*old_values, *new_values)))
    op.alter_column(
        table,
        column,
        existing_type=sa.Enum(*old_values, name=enum_name),
        type_=sa.Enum(*transitional, name=enum_name),
    )
    connection.execute(sa.text(
        f"UPDATE {table} SET {column} = UPPER({column}) WHERE {column} IS NOT NULL"
    ))
    op.alter_column(
        table,
        column,
        existing_type=sa.Enum(*transitional, name=enum_name),
        type_=sa.Enum(*new_values, name=enum_name),
    )


def upgrade():
    op.add_column(
        "service_types",
        sa.Column("allowed_roles", sa.JSON(), nullable=True),
    )
    op.execute("UPDATE service_types SET allowed_roles = '[]' WHERE allowed_roles IS NULL")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("service_types") as batch:
            batch.alter_column(
                "allowed_roles",
                existing_type=sa.JSON(),
                nullable=False,
            )
    else:
        op.alter_column(
            "service_types",
            "allowed_roles",
            existing_type=sa.JSON(),
            nullable=False,
        )

    connection = op.get_bind()
    for code, name, prefix, avg_service_min, allowed_roles in HOSPITAL_SERVICES:
        exists = connection.execute(
            sa.text("SELECT id FROM service_types WHERE code = :code"),
            {"code": code},
        ).first()
        if exists:
            connection.execute(
                sa.text(
                    "UPDATE service_types SET name = :name, prefix = :prefix, "
                    "avg_service_min = :avg, allowed_roles = :roles, is_active = 1 "
                    "WHERE code = :code"
                ),
                {
                    "name": name, "prefix": prefix, "avg": avg_service_min,
                    "roles": allowed_roles, "code": code,
                },
            )
        else:
            connection.execute(
                sa.text(
                    "INSERT INTO service_types "
                    "(code, name, prefix, avg_service_min, is_active, allowed_roles) "
                    "VALUES (:code, :name, :prefix, :avg, 1, :roles)"
                ),
                {
                    "code": code, "name": name, "prefix": prefix,
                    "avg": avg_service_min, "roles": allowed_roles,
                },
            )

    connection.execute(
        sa.text(
            "UPDATE service_types SET is_active = 0 "
            "WHERE code IN ('AO', 'AC', 'FD', 'LN', 'OT')"
        )
    )

    for table, column, enum_name, old_values, new_values in ENUM_COLUMNS:
        _normalize_enum_column(table, column, enum_name, old_values, new_values)

    if connection.dialect.name == "sqlite":
        connection.execute(sa.text(
            "UPDATE queue_tokens SET priority_reason = UPPER(priority_reason) "
            "WHERE priority_reason IS NOT NULL"
        ))
        check_names = {
            constraint.get("name")
            for constraint in sa.inspect(connection).get_check_constraints("queue_tokens")
        }
        with op.batch_alter_table("queue_tokens", recreate="always") as batch:
            if "priorityreason" in check_names:
                batch.drop_constraint("priorityreason", type_="check")
            batch.alter_column(
                "priority_reason",
                existing_type=OLD_PRIORITY_REASON,
                type_=HOSPITAL_PRIORITY_REASON,
            )
    else:
        transitional_priority = tuple(dict.fromkeys(
            (*("none", "elderly", "pregnant", "disabled"),
             *HOSPITAL_PRIORITY_REASON.enums)
        ))
        op.alter_column(
            "queue_tokens",
            "priority_reason",
            existing_type=sa.Enum("none", "elderly", "pregnant", "disabled",
                                  name="priorityreason"),
            type_=sa.Enum(*transitional_priority, name="priorityreason"),
        )
        connection.execute(sa.text(
            "UPDATE queue_tokens SET priority_reason = UPPER(priority_reason) "
            "WHERE priority_reason IS NOT NULL"
        ))
        op.alter_column(
            "queue_tokens",
            "priority_reason",
            existing_type=sa.Enum(*transitional_priority, name="priorityreason"),
            type_=HOSPITAL_PRIORITY_REASON,
        )


def downgrade():
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE queue_tokens SET priority_reason = 'NONE' "
            "WHERE priority_reason IN ('EMERGENCY', 'URGENT', 'CHILD')"
        )
    )
    if connection.dialect.name == "sqlite":
        check_names = {
            constraint.get("name")
            for constraint in sa.inspect(connection).get_check_constraints("queue_tokens")
        }
        with op.batch_alter_table("queue_tokens", recreate="always") as batch:
            if "priorityreason" in check_names:
                batch.drop_constraint("priorityreason", type_="check")
            batch.alter_column(
                "priority_reason",
                existing_type=HOSPITAL_PRIORITY_REASON,
                type_=OLD_PRIORITY_REASON,
            )
    else:
        op.alter_column(
            "queue_tokens",
            "priority_reason",
            existing_type=HOSPITAL_PRIORITY_REASON,
            type_=OLD_PRIORITY_REASON,
        )

    connection.execute(
        sa.text(
            "UPDATE service_types SET is_active = 0 "
            "WHERE code IN ('G', 'S', 'L', 'R', 'P', 'B')"
        )
    )
    op.drop_column("service_types", "allowed_roles")
