"""Store workflow templates and signal committed run changes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("graph_json", JSONB(), nullable=False),
        sa.Column("schedule", JSONB(none_as_null=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("""
        CREATE FUNCTION notify_workflow_run() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE run_uuid uuid;
        BEGIN
            IF TG_TABLE_NAME = 'scan_runs' THEN
                run_uuid := NEW.id;
            ELSE
                run_uuid := NEW.run_id;
            END IF;
            PERFORM pg_notify('cyberflow_run_' || replace(run_uuid::text, '-', ''), '');
            RETURN NULL;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER scan_run_events AFTER UPDATE OF status ON scan_runs
        FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION notify_workflow_run()
    """)
    op.execute("""
        CREATE TRIGGER node_run_events AFTER UPDATE OF status ON node_runs
        FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION notify_workflow_run()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER node_run_events ON node_runs")
    op.execute("DROP TRIGGER scan_run_events ON scan_runs")
    op.execute("DROP FUNCTION notify_workflow_run()")
    op.drop_table("workflow_templates")
