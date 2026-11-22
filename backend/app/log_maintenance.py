"""Copy opted-in logs to operator-mounted archives and expire both copies."""

import logging
import os
from contextlib import contextmanager, nullcontext, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .db.models import WorkspaceLog, WorkspaceLogPolicy
from .technical_log import log_event
from .workspace_logs import LogRecord, expires_at


@contextmanager
def _directory(path: str | Path, parent: int | None = None):
    fd = os.open(path, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _archive(alias: str, path: Path):
    # An unmounted volume can leave an empty mountpoint behind. Bind all operations
    # to the verified directory descriptor so a mount change cannot redirect them.
    with _directory(path) as root:
        marker = os.open(".cyberflow-destination", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)
        with os.fdopen(marker, "rb") as stream:
            expected = alias.encode("ascii")
            if stream.read(64) not in (expected, expected + b"\n", expected + b"\r\n"):
                raise OSError("Log archive identity mismatch")
        yield root


def _write_or_expire(root: int, row: WorkspaceLog, expired: bool) -> None:
    folder = str(row.workspace_id)
    if not expired:
        with suppress(FileExistsError):
            os.mkdir(folder, mode=0o700, dir_fd=root)
    try:
        workspace = os.open(folder, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)
    except FileNotFoundError:
        if expired:
            return
        raise
    try:
        name, pending = f"{row.id}.json", f"{row.id}.tmp"
        if expired:
            for file in (name, pending):
                with suppress(FileNotFoundError):
                    os.unlink(file, dir_fd=workspace)
        else:
            fd = os.open(pending, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=workspace)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(LogRecord.model_validate(row).model_dump_json())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, name, src_dir_fd=workspace, dst_dir_fd=workspace)
        os.fsync(workspace)
    finally:
        os.close(workspace)


def _process_batch(db: Session, alias: str, root: int | None, now: datetime) -> None:
    expired = expires_at() <= now
    needs_work = or_(expired, WorkspaceLog.archived.is_(False)) if root is not None else expired
    rows = db.execute(select(WorkspaceLog, expired.label("expired")).outerjoin(
        WorkspaceLogPolicy, WorkspaceLogPolicy.workspace_id == WorkspaceLog.workspace_id,
    ).where(WorkspaceLog.destination == alias, needs_work,
            or_(WorkspaceLog.archive_retry_at.is_(None), WorkspaceLog.archive_retry_at <= now))
        .order_by(WorkspaceLog.at, WorkspaceLog.id).limit(500)
        .with_for_update(of=WorkspaceLog, skip_locked=True))
    for row, is_expired in rows:
        if root is not None:
            try:
                _write_or_expire(root, row, is_expired)
            except OSError:
                row.archive_retry_at = now + timedelta(minutes=5)
                log_event("queue_worker", "logs.archive", "failed", level=logging.WARNING)
                continue
            row.archived = True
            row.archive_retry_at = None
        if is_expired:
            db.delete(row)


def maintain_logs(sessions: sessionmaker[Session], settings: Settings, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    for alias, path in {"postgresql": None, **settings.log_destinations}.items():
        archive = _archive(alias, path) if path is not None else nullcontext(None)
        try:
            with archive as root, sessions.begin() as db:
                _process_batch(db, alias, root, now)
        except OSError:
            log_event("queue_worker", "logs.archive", "unavailable", level=logging.WARNING)
