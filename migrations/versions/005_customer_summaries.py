"""Add review comments and condition summaries to customers."""

from alembic import op
import sqlalchemy as sa


revision = "005_customer_summaries"
down_revision = "004_appointments_and_new_roles"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "customers",
        sa.Column("review_comments", sa.Text(), nullable=True),
    )
    op.add_column(
        "customers",
        sa.Column("condition_summary", sa.String(length=500), nullable=True),
    )


def downgrade():
    op.drop_column("customers", "condition_summary")
    op.drop_column("customers", "review_comments")
