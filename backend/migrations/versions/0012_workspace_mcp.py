"""Bind read-only MCP credentials to a workspace membership."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_mcp_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ("workspace_id", "user_id"),
            ("workspace_memberships.workspace_id", "workspace_memberships.user_id"),
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_workspace_mcp_tokens_membership", "workspace_mcp_tokens", ["workspace_id", "user_id"])


def downgrade() -> None:
    op.drop_table("workspace_mcp_tokens")
