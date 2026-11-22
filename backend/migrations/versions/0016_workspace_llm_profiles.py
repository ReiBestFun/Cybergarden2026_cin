"""Store explicitly enabled external model profiles per workspace."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_llm_profiles",
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("base_url", sa.String(255), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("workspace_llm_profiles")
