"""Store CI runs and their idempotency keys."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scan_runs", sa.Column("ci_request_id", sa.String(100), nullable=True))
    op.create_unique_constraint("uq_run_ci_request", "scan_runs", ["workflow_id", "ci_request_id"])
    op.drop_constraint("ck_run_trigger", "scan_runs", type_="check")
    op.create_check_constraint("ck_run_trigger", "scan_runs", "trigger IN ('manual', 'schedule', 'ci')")


def downgrade() -> None:
    op.execute("UPDATE scan_runs SET trigger = 'manual' WHERE trigger = 'ci'")
    op.drop_constraint("ck_run_trigger", "scan_runs", type_="check")
    op.create_check_constraint("ck_run_trigger", "scan_runs", "trigger IN ('manual', 'schedule')")
    op.drop_constraint("uq_run_ci_request", "scan_runs", type_="unique")
    op.drop_column("scan_runs", "ci_request_id")
