"""Add an optional department to each service.

The patient token page shows the department as the destination and the
service underneath it. Services without a department show their own name.
"""
from alembic import op
import sqlalchemy as sa


revision = "011_service_department"
down_revision = "010_integrity_and_security"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("service_types", sa.Column("department", sa.String(80), nullable=True))


def downgrade():
    with op.batch_alter_table("service_types") as batch:
        batch.drop_column("department")
