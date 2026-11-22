"""Workspace audit, diagnostics and retention policies."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workspace_log_policies",
        sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("destination", sa.String(48), nullable=False),
        sa.Column("audit_days", sa.Integer, nullable=False),
        sa.Column("diagnostic_days", sa.Integer, nullable=False),
        sa.Column("member_view", sa.Boolean, nullable=False),
        sa.Column("member_export", sa.Boolean, nullable=False),
        sa.Column("member_manage", sa.Boolean, nullable=False),
    )
    op.create_table(
        "workspace_logs",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.clock_timestamp(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.Uuid),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("run_id", sa.Uuid),
        sa.Column("node_run_id", sa.Uuid),
        sa.Column("destination", sa.String(48), nullable=False),
        sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_workspace_logs_lookup", "workspace_logs", ["workspace_id", "kind", "at"])
    op.create_index("ix_workspace_logs_run_id", "workspace_logs", ["run_id"])


def downgrade():
    op.drop_table("workspace_logs")
    op.drop_table("workspace_log_policies")
