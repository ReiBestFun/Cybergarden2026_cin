"""Weekly, monthly and cron schedules.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("schedules", sa.Column("frequency", sa.String(length=16), server_default=sa.text("'daily'"), nullable=False))
    op.add_column("schedules", sa.Column("weekdays", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True))
    op.add_column("schedules", sa.Column("day_of_month", sa.Integer(), nullable=True))
    op.add_column("schedules", sa.Column("cron", sa.String(length=120), nullable=True))
    op.alter_column("schedules", "time", existing_type=sa.Time(), nullable=True)
    op.create_check_constraint(
        "ck_schedule_frequency", "schedules", "frequency IN ('daily', 'weekly', 'monthly', 'cron')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM schedules WHERE time IS NULL")
    op.drop_constraint("ck_schedule_frequency", "schedules", type_="check")
    op.alter_column("schedules", "time", existing_type=sa.Time(), nullable=False)
    op.drop_column("schedules", "cron")
    op.drop_column("schedules", "day_of_month")
    op.drop_column("schedules", "weekdays")
    op.drop_column("schedules", "frequency")
