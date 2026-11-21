"""Join the delivery and credentials migrations and signal delivery updates."""

from alembic import op

revision = "0007"
down_revision = ("0006", "0005_delivery")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Databases which upgraded the original delivery branch may already be stamped 0005.
    op.execute("""
        CREATE TABLE IF NOT EXISTS credentials (
            id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects (id),
            name VARCHAR(120) NOT NULL,
            kind VARCHAR(24) NOT NULL,
            ciphertext TEXT NOT NULL,
            active BOOLEAN DEFAULT true NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_credentials_project ON credentials (project_id)")
    op.execute("""
        CREATE FUNCTION notify_workflow_delivery() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE run_uuid uuid;
        BEGIN
            SELECT node_runs.run_id INTO run_uuid FROM notification_batches
            JOIN node_runs ON notification_batches.node_run_id = node_runs.id
            WHERE notification_batches.id = NEW.batch_id;
            IF run_uuid IS NOT NULL THEN
                PERFORM pg_notify('cyberflow_run_' || replace(run_uuid::text, '-', ''), '');
            END IF;
            RETURN NULL;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER delivery_run_events AFTER UPDATE OF status ON notification_deliveries
        FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION notify_workflow_delivery()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER delivery_run_events ON notification_deliveries")
    op.execute("DROP FUNCTION notify_workflow_delivery()")
