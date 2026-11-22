"""Store immutable ZIP sources with their project."""

from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_archives",
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("content", sa.LargeBinary(), nullable=False),
    )


def downgrade():
    op.drop_table("project_archives")
