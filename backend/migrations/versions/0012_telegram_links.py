"""One-time Telegram account links."""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("telegram_links",
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(64), unique=True),
        sa.Column("group_chat", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("destination_id", sa.Uuid(), sa.ForeignKey("telegram_destinations.id", ondelete="SET NULL")),
    )


def downgrade() -> None:
    op.drop_table("telegram_links")
