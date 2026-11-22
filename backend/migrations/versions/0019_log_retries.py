"""Version log policies and track archive retries and diagnostic delivery IDs."""

from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workspace_log_policies", sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("workspace_logs", sa.Column("archive_retry_at", sa.DateTime(timezone=True)))
    op.add_column("workspace_logs", sa.Column("delivery_id", sa.Uuid()))


def downgrade():
    op.drop_column("workspace_logs", "delivery_id")
    op.drop_column("workspace_logs", "archive_retry_at")
    op.drop_column("workspace_log_policies", "version")
