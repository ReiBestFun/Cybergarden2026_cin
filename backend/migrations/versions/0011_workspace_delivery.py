"""Store encrypted workspace delivery policies and bind queued work to its workspace."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_delivery_settings",
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("channel", sa.String(16), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recipients", JSONB(none_as_null=True), nullable=False),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("secret", sa.Text(), nullable=True),
        sa.CheckConstraint("channel IN ('email', 'telegram')", name="ck_workspace_delivery_channel"),
    )
    op.add_column("notification_batches", sa.Column("settings_workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id")))


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM notification_batches WHERE settings_workspace_id IS NOT NULL)"
    )).scalar_one():
        raise RuntimeError("Восстановите резервную копию до 0011, чтобы сохранить маршрут командных уведомлений.")
    op.drop_column("notification_batches", "settings_workspace_id")
    op.drop_table("workspace_delivery_settings")
