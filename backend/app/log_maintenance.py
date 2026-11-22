"""Copy opted-in logs to operator-mounted archives and expire both copies."""

import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .db.models import WorkspaceLog, WorkspaceLogPolicy
from .technical_log import log_event
from .workspace_logs import LogPolicy, LogRecord


def _archive_path(root: Path, row: WorkspaceLog) -> Path:
    # The mount itself must exist. Only UUIDs from PostgreSQL become path components.
    if not root.is_dir():
        raise OSError("Log mount unavailable")
    return root / str(row.workspace_id) / (str(row.id) + ".json")


def maintain_logs(sessions: sessionmaker[Session], settings: Settings, now: datetime | None = None) -> None:
    defaults = LogPolicy()
    days = case((WorkspaceLog.kind == "audit", func.coalesce(WorkspaceLogPolicy.audit_days, defaults.audit_days)),
                else_=func.coalesce(WorkspaceLogPolicy.diagnostic_days, defaults.diagnostic_days))
    expired = WorkspaceLog.at + days * text("interval '1 day'") <= (now or datetime.now(UTC))
    for alias, root in {"postgresql": None, **settings.log_destinations}.items():
        with sessions.begin() as db:
            rows = db.execute(select(WorkspaceLog, expired.label("expired")).outerjoin(
                WorkspaceLogPolicy, WorkspaceLogPolicy.workspace_id == WorkspaceLog.workspace_id,
            ).where(WorkspaceLog.destination == alias,
                    or_(expired, WorkspaceLog.archived.is_(False) if root is not None else False))
                .order_by(WorkspaceLog.at, WorkspaceLog.id).limit(500)
                .with_for_update(of=WorkspaceLog, skip_locked=True))
            for row, is_expired in rows:
                try:
                    if root is not None:
                        path = _archive_path(root, row)
                        if is_expired:
                            path.unlink(missing_ok=True)
                        else:
                            path.parent.mkdir(mode=0o700, exist_ok=True)
                            # The row lock serializes writes to this name across workers.
                            pending = path.with_suffix(".tmp")
                            try:
                                pending.write_text(LogRecord.model_validate(row).model_dump_json(), encoding="utf-8")
                                pending.replace(path)
                            finally:
                                pending.unlink(missing_ok=True)
                            row.archived = True
                    if is_expired:
                        db.delete(row)
                except OSError:
                    log_event("queue_worker", "logs.archive", "failed", level=logging.WARNING)
                    break
