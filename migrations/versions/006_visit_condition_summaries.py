"""Add a brief condition summary to completed queue visits."""

from alembic import op
import sqlalchemy as sa


revision = "006_visit_condition_summaries"
down_revision = "005_customer_summaries"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "queue_tokens",
        sa.Column("condition_summary", sa.String(length=500), nullable=True),
    )


def downgrade():
    op.drop_column("queue_tokens", "condition_summary")
