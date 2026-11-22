"""Add Slack to workspace delivery and bind queued reports to a webhook version."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def _channels(values: str) -> None:
    for table, constraint in (
        ("workspace_delivery_settings", "ck_workspace_delivery_channel"),
        ("notification_batches", "ck_notification_channel"),
    ):
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, f"channel IN ({values})")


def upgrade() -> None:
    op.add_column("notification_batches", sa.Column("settings_version", sa.Integer(), nullable=True))
    _channels("'email', 'telegram', 'slack'")
    op.create_check_constraint(
        "ck_slack_notification_scope", "notification_batches",
        "channel <> 'slack' OR (settings_workspace_id IS NOT NULL AND settings_version IS NOT NULL)",
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM workspace_delivery_settings WHERE channel = 'slack') "
        "OR EXISTS (SELECT 1 FROM notification_batches WHERE channel = 'slack')"
    )).scalar_one():
        raise RuntimeError("Для отката с настроенным Slack восстановите базу до миграции 0015.")
    op.drop_constraint("ck_slack_notification_scope", "notification_batches", type_="check")
    _channels("'email', 'telegram'")
    op.drop_column("notification_batches", "settings_version")
