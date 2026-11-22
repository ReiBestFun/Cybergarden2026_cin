"""Add GitHub accounts, private resources and encrypted storage."""

import json

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects.postgresql import JSONB

from backend.app.db.encryption import check_storage_key, require_cipher

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_TEXT = sa.Text()
_JSON = JSONB(none_as_null=True)
_BINARY = sa.LargeBinary()
_FIELDS = {
    "workflow_templates": {"name": _TEXT, "description": _TEXT, "graph_json": _JSON, "schedule": _JSON},
    "projects": {"name": _TEXT, "repository_url": _TEXT, "default_ref": _TEXT},
    "workflows": {"name": _TEXT, "graph_json": _JSON},
    "schedules": {"time": _TEXT, "weekdays": _JSON, "day_of_month": _JSON, "cron": _TEXT, "timezone": _TEXT, "last_error": _JSON},
    "scan_runs": {"graph_snapshot": _JSON, "source_snapshot": _JSON, "commit_sha": _TEXT, "summary": _JSON, "report": _JSON, "error": _JSON},
    "node_runs": {"input_preview": _JSON, "output_preview": _JSON, "error": _JSON},
    "findings": {"title": _TEXT, "description": _TEXT, "remediation": _TEXT, "path": _TEXT, "evidence": _TEXT, "metadata": _JSON},
    "notification_batches": {"subject": _TEXT, "body": _TEXT, "attachment_name": _TEXT, "attachment_content": _BINARY},
    "notification_deliveries": {"recipient": _TEXT, "message_id": _TEXT, "attachment_id": _TEXT, "error_message": _TEXT},
}


def _encrypt_rows(connection, cipher, table_name, fields) -> None:
    columns = [sa.column("id", sa.Uuid()), *(sa.column(name, kind) for name, kind in fields.items())]
    is_delivery = table_name == "notification_deliveries"
    if is_delivery:
        columns.extend((sa.column("batch_id", sa.Uuid()), sa.column("recipient_key", sa.String(64))))
    table = sa.table(table_name, *columns)
    read = sa.select(table).order_by(table.c.id).limit(256)
    write_fields = [*fields, *(["recipient_key"] if is_delivery else [])]
    write = table.update().where(table.c.id == sa.bindparam("row_id")).values(
        {name: sa.bindparam(name) for name in write_fields},
    )

    def protect(name, kind, value):
        if value is None or (table_name == "findings" and name == "evidence" and value == "***"):
            return value
        field = f"{table_name}.{name}"
        if kind is _BINARY:
            return cipher.seal(field, value)
        if kind is _JSON:
            value = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return cipher.seal_text(field, value)

    after = None
    while rows := connection.execute(read if after is None else read.where(table.c.id > after)).mappings().all():
        updates = []
        for row in rows:
            values = {name: protect(name, kind, row[name]) for name, kind in fields.items()}
            if is_delivery:
                values["recipient_key"] = cipher.digest(f"delivery:{row['batch_id']}", row["recipient"])
            updates.append({"row_id": row["id"], **values})
        connection.execute(write, updates)
        after = rows[-1]["id"]


def _restore_run_events() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION notify_workflow_run() RETURNS trigger LANGUAGE plpgsql AS $$
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
        CREATE OR REPLACE TRIGGER scan_run_events AFTER UPDATE OF status ON scan_runs
        FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION notify_workflow_run()
    """)
    op.execute("""
        CREATE OR REPLACE TRIGGER node_run_events AFTER UPDATE OF status ON node_runs
        FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status) EXECUTE FUNCTION notify_workflow_run()
    """)


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("Миграция 0008 требует подключения к PostgreSQL и DATA_ENCRYPTION_KEY.")
    connection = op.get_bind()
    cipher = require_cipher(connection.dialect)
    # The accounts branch previously used 0006 for this encryption schema.
    if sa.inspect(connection).has_table("storage_metadata"):
        check_storage_key(connection)
        op.add_column("workflow_templates", sa.Column("schedule", _JSON, nullable=True))
        _restore_run_events()
        return

    storage = op.create_table(
        "storage_metadata",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("key_check", sa.String(64), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_storage_metadata_singleton"),
    )
    connection.execute(storage.insert().values(id=1, key_check=cipher.digest("storage-check", "v1")))
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("github_key", sa.String(64), nullable=False, unique=True),
        sa.Column("github_login", _TEXT, nullable=False),
        sa.Column("display_name", _TEXT, nullable=False),
        sa.Column("avatar_url", _TEXT),
        sa.Column("timezone", _TEXT, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "user_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])
    op.create_table(
        "oauth_attempts",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column("verifier", _TEXT, nullable=False),
        sa.Column("return_path", _TEXT, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_oauth_attempts_expires_at", "oauth_attempts", ["expires_at"])
    op.add_column("workflow_templates", sa.Column(
        "owner_id", sa.Uuid(), sa.ForeignKey("users.id", name="fk_templates_owner", ondelete="CASCADE"),
    ))
    op.add_column("workflow_templates", sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
    ))
    op.execute("UPDATE workflow_templates SET updated_at = created_at")
    op.alter_column("workflow_templates", "name", type_=_TEXT)
    op.create_index("ix_workflow_templates_owner_id", "workflow_templates", ["owner_id"])
    op.create_table(
        "telegram_destinations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_key", sa.String(64), nullable=False),
        sa.Column("chat_id", _TEXT, nullable=False),
        sa.Column("label", _TEXT, nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("challenge_hash", sa.String(64)),
        sa.Column("challenge_expires_at", sa.DateTime(timezone=True)),
        sa.Column("challenge_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("owner_id", "chat_key", name="uq_telegram_destination_owner_chat"),
    )
    op.create_index("ix_telegram_destinations_owner_id", "telegram_destinations", ["owner_id"])
    op.create_table(
        "telegram_challenge_limits",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_telegram_challenge_limits_next_at", "telegram_challenge_limits", ["next_at"])

    op.add_column("projects", sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", name="fk_projects_owner")))
    op.add_column("projects", sa.Column("repository_key", sa.String(64)))
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])
    op.create_unique_constraint("uq_project_owner_repository", "projects", ["owner_id", "repository_key"])
    op.create_check_constraint("ck_project_owner_key", "projects", "(owner_id IS NULL) = (repository_key IS NULL)")
    op.drop_constraint("uq_notification_recipient", "notification_deliveries", type_="unique")
    op.add_column("notification_deliveries", sa.Column("recipient_key", sa.String(64)))

    for table_name, field in (
        ("projects", "name"), ("projects", "default_ref"), ("workflows", "name"),
        ("schedules", "cron"), ("schedules", "timezone"), ("scan_runs", "commit_sha"),
        ("notification_batches", "attachment_name"), ("notification_deliveries", "recipient"),
        ("notification_deliveries", "message_id"), ("notification_deliveries", "attachment_id"),
    ):
        op.alter_column(table_name, field, type_=_TEXT)
    op.alter_column("schedules", "time", type_=_TEXT, postgresql_using='"time"::text')
    op.alter_column("schedules", "day_of_month", type_=_JSON, postgresql_using="to_jsonb(day_of_month)")
    for table_name, fields in _FIELDS.items():
        _encrypt_rows(connection, cipher, table_name, fields)
    op.alter_column("notification_deliveries", "recipient_key", nullable=False)
    op.create_unique_constraint("uq_notification_recipient", "notification_deliveries", ["batch_id", "recipient_key"])


def downgrade() -> None:
    raise RuntimeError("Для отката 0008 восстановите резервную копию базы до миграции.")
