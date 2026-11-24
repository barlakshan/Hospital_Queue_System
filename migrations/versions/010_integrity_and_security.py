"""Integrity and security fixes from the 2026-11 audit.

* queue_tokens: public_ref (unguessable public URL id), patient_name (name
  given for the visit), priority_requested; status / is_priority /
  priority_reason NOT NULL; drop the low-value ix_tokens_priority index.
* appointment_requests: status lifecycle, handled_by / handled_at, customer_id.
* users: must_change_password, last_seen; SQLite role column wide enough for
  every role.
* branches.is_active NOT NULL.
* customers: drop ix_customers_mobile (duplicates the unique index).
* service_types.allowed_roles: add the current role names next to legacy ones.
"""
import json
import secrets

from alembic import op
import sqlalchemy as sa


revision = "010_integrity_and_security"
down_revision = "009_specialty_service_roles"
branch_labels = None
depends_on = None


PRIORITY_REASON = sa.Enum(
    "NONE", "EMERGENCY", "URGENT", "ELDERLY", "PREGNANT", "DISABLED", "CHILD",
    name="priorityreason",
)
TOKEN_STATUS = sa.Enum(
    "WAITING", "CALLED", "SERVING", "COMPLETED", "NO_SHOW", "SKIPPED", name="tokenstatus",
)
USER_ROLE = sa.Enum(
    "ADMIN", "OPD", "PHARMACY", "DENTIST", "CARDIOLOGISTS", "DERMATOLOGISTS",
    "BRANCH_MANAGER", "LOBBY_OFFICER", "LOAN_OFFICER", "SPECIALIST_DOCTOR",
    name="userrole",
)
OLD_SQLITE_USER_ROLE = sa.String(14)

# Legacy service role -> current role names granting the same access.
LEGACY_TO_CURRENT = {
    "lobby_officer": ["opd"],
    "loan_officer": ["pharmacy"],
    "specialist_doctor": ["dentist", "cardiologists", "dermatologists"],
}
SPECIALTIES = {"dentist", "cardiologists", "dermatologists"}


def _index_names(table):
    return {ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes(table)}


def _backfill(bind):
    tokens = sa.table(
        "queue_tokens",
        sa.column("id", sa.Integer), sa.column("public_ref", sa.String),
        sa.column("status", sa.String), sa.column("is_priority", sa.Boolean),
        sa.column("priority_reason", sa.String),
    )
    ids = [row[0] for row in bind.execute(
        sa.select(tokens.c.id).where(tokens.c.public_ref.is_(None)))]
    for token_id in ids:
        bind.execute(tokens.update().where(tokens.c.id == token_id)
                     .values(public_ref=secrets.token_urlsafe(16)))

    bind.execute(sa.text(
        "UPDATE queue_tokens SET patient_name = "
        "(SELECT full_name FROM customers WHERE customers.id = queue_tokens.customer_id) "
        "WHERE patient_name IS NULL"
    ))
    bind.execute(tokens.update().where(tokens.c.status.is_(None)).values(status="WAITING"))
    bind.execute(tokens.update().where(tokens.c.is_priority.is_(None)).values(is_priority=False))
    bind.execute(tokens.update().where(tokens.c.priority_reason.is_(None))
                 .values(priority_reason="NONE"))

    branches = sa.table("branches", sa.column("is_active", sa.Boolean))
    bind.execute(branches.update().where(branches.c.is_active.is_(None)).values(is_active=True))


def _add_current_role_names(bind):
    services = sa.table("service_types", sa.column("id", sa.Integer),
                        sa.column("allowed_roles", sa.JSON))
    for service_id, roles in bind.execute(sa.select(services.c.id, services.c.allowed_roles)):
        if isinstance(roles, str):
            roles = json.loads(roles or "[]")
        roles = list(roles or [])
        updated = list(roles)
        for legacy, current in LEGACY_TO_CURRENT.items():
            if legacy not in roles:
                continue
            # A service already scoped to particular specialties stays scoped.
            if legacy == "specialist_doctor" and SPECIALTIES.intersection(roles):
                continue
            updated.extend(role for role in current if role not in updated)
        if updated != roles:
            bind.execute(services.update().where(services.c.id == service_id)
                         .values(allowed_roles=updated))


def upgrade():
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    with op.batch_alter_table("queue_tokens") as batch:
        batch.add_column(sa.Column("public_ref", sa.String(32), nullable=True))
        batch.add_column(sa.Column("patient_name", sa.String(120), nullable=True))
        batch.add_column(sa.Column("priority_requested", PRIORITY_REASON, nullable=True))
    with op.batch_alter_table("appointment_requests") as batch:
        batch.add_column(sa.Column("customer_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("status", sa.String(16), nullable=False,
                                   server_default="pending"))
        batch.add_column(sa.Column("handled_by", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("handled_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key("fk_appointment_requests_customer_id",
                                 "customers", ["customer_id"], ["id"])
        batch.create_foreign_key("fk_appointment_requests_handled_by",
                                 "users", ["handled_by"], ["id"])
        batch.create_index("ix_appointments_branch_status", ["branch_id", "status"])
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("must_change_password", sa.Boolean(), nullable=False,
                                   server_default="0"))
        batch.add_column(sa.Column("last_seen", sa.DateTime(), nullable=True))
        if is_sqlite:
            batch.alter_column("role", existing_type=OLD_SQLITE_USER_ROLE, type_=USER_ROLE,
                               existing_nullable=False)

    _backfill(bind)
    _add_current_role_names(bind)

    with op.batch_alter_table("queue_tokens") as batch:
        batch.alter_column("public_ref", existing_type=sa.String(32), nullable=False)
        batch.create_unique_constraint("uq_queue_tokens_public_ref", ["public_ref"])
        batch.alter_column("status", existing_type=TOKEN_STATUS, nullable=False)
        batch.alter_column("is_priority", existing_type=sa.Boolean(), nullable=False)
        batch.alter_column("priority_reason", existing_type=PRIORITY_REASON, nullable=False)
        if "ix_tokens_priority" in _index_names("queue_tokens"):
            batch.drop_index("ix_tokens_priority")
    with op.batch_alter_table("branches") as batch:
        batch.alter_column("is_active", existing_type=sa.Boolean(), nullable=False)
    if "ix_customers_mobile" in _index_names("customers"):
        op.drop_index("ix_customers_mobile", table_name="customers")


def downgrade():
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    op.create_index("ix_customers_mobile", "customers", ["mobile"])
    with op.batch_alter_table("branches") as batch:
        batch.alter_column("is_active", existing_type=sa.Boolean(), nullable=True)
    with op.batch_alter_table("queue_tokens") as batch:
        batch.create_index("ix_tokens_priority", ["is_priority"])
        batch.alter_column("priority_reason", existing_type=PRIORITY_REASON, nullable=True)
        batch.alter_column("is_priority", existing_type=sa.Boolean(), nullable=True)
        batch.alter_column("status", existing_type=TOKEN_STATUS, nullable=True)
        batch.drop_constraint("uq_queue_tokens_public_ref", type_="unique")
        batch.drop_column("priority_requested")
        batch.drop_column("patient_name")
        batch.drop_column("public_ref")
    with op.batch_alter_table("users") as batch:
        if is_sqlite:
            batch.alter_column("role", existing_type=USER_ROLE, type_=OLD_SQLITE_USER_ROLE,
                               existing_nullable=False)
        batch.drop_column("last_seen")
        batch.drop_column("must_change_password")
    with op.batch_alter_table("appointment_requests") as batch:
        batch.drop_index("ix_appointments_branch_status")
        batch.drop_constraint("fk_appointment_requests_handled_by", type_="foreignkey")
        batch.drop_constraint("fk_appointment_requests_customer_id", type_="foreignkey")
        batch.drop_column("handled_at")
        batch.drop_column("handled_by")
        batch.drop_column("status")
        batch.drop_column("customer_id")
    # The role names added to service_types.allowed_roles are left in place:
    # they are harmless for the older code, which also recognises them.
