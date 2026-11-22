"""Record the originating workspace for new private template drafts."""

import sqlalchemy as sa
from alembic import op

revision = "0011_templates"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_templates", sa.Column(
        "draft_source_workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="SET NULL"),
    ))


def downgrade() -> None:
    op.drop_column("workflow_templates", "draft_source_workspace_id")
