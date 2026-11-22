"""Consume queued workflow runs from PostgreSQL."""

import argparse
import logging
import signal
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from time import monotonic

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .analyzers.models import Warning
from .config import Settings
from .db.encryption import check_storage_key
from .db.session import make_engine
from .integrations.repository import Allowlist, repository_allowlist
from .technical_log import log_event
from .log_maintenance import maintain_logs
from .workflows.execution import GraphExecution
from .workflows.persistence import ClaimLost, RunProgress, log_failed_run
from .workflows.queue import claim_next, enqueue_due_schedules, expire_stale_runs

def _heartbeat(progress: RunProgress, stop: Event, interval: float) -> None:
    while not stop.wait(interval):
        try:
            if not progress.heartbeat():
                return
        except SQLAlchemyError:
            log_event("queue_worker", "run.heartbeat", "failed", level=logging.WARNING, run_id=progress.claim.id)


def tick_schedules(sessions: sessionmaker[Session]) -> int:
    with sessions.begin() as db:
        created = enqueue_due_schedules(db, datetime.now(UTC))
    if created:
        log_event("queue_worker", "schedule.enqueue", "succeeded", count=created)
    return created


def run_next(
    sessions: sessionmaker[Session], settings: Settings, allowed: Allowlist
) -> bool:
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.worker_stale_seconds)
    with sessions.begin() as db:
        expired = expire_stale_runs(db, cutoff)
    for failure in expired:
        log_event("queue_worker", "run.expire", "failed", level=logging.WARNING, run_id=failure.id)
        log_failed_run(failure)
    with sessions.begin() as db:
        claim = claim_next(db)
    if claim is None:
        return False
    log_event("queue_worker", "run.claim", "running", run_id=claim.id)
    progress = RunProgress(sessions, claim)
    stop = Event()
    heartbeat = Thread(
        target=_heartbeat, args=(progress, stop, min(5, settings.worker_stale_seconds / 3)), daemon=True,
    )
    heartbeat.start()
    try:
        GraphExecution(progress, settings, allowed).execute()
    except ClaimLost:
        log_event("queue_worker", "run.claim", "lost", level=logging.WARNING, run_id=claim.id)
    except Exception:
        log_event("queue_worker", "run.execute", "failed", level=logging.ERROR, run_id=claim.id)
        try:
            progress.fail(Warning("WORKFLOW_EXECUTION_FAILED", "Не удалось завершить выполнение графа."))
        except ClaimLost:
            log_event("queue_worker", "run.claim", "lost", level=logging.WARNING, run_id=claim.id)
    finally:
        stop.set()
        heartbeat.join()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Выполняет задания из очереди PostgreSQL.")
    parser.add_argument("--once", action="store_true", help="Обработать не более одного задания и завершиться.")
    args = parser.parse_args()
    settings = Settings()
    allowed = repository_allowlist(settings.allowed_repositories, settings.allow_any_repository)
    if allowed is None:
        log_event("queue_worker", "repository.allowlist", "disabled", level=logging.WARNING)
    engine = make_engine(settings.database_url, settings.data_encryption_key)
    with engine.connect() as connection:
        check_storage_key(connection)
    sessions = sessionmaker(engine)
    stop = Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    next_tick = 0.0
    try:
        while not stop.is_set():
            if monotonic() >= next_tick:
                try:
                    tick_schedules(sessions)
                except SQLAlchemyError:
                    log_event("queue_worker", "schedule.enqueue", "failed", level=logging.ERROR)
                try:
                    maintain_logs(sessions, settings)
                except SQLAlchemyError:
                    log_event("queue_worker", "logs.maintenance", "failed", level=logging.ERROR)
                next_tick = monotonic() + settings.scheduler_poll_seconds
            try:
                processed = run_next(sessions, settings, allowed)
            except SQLAlchemyError:
                log_event("queue_worker", "run.queue", "failed", level=logging.ERROR)
                if args.once:
                    return 1
                processed = False
            if args.once:
                break
            if not processed:
                stop.wait(settings.worker_poll_seconds)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception:
        log_event("queue_worker", "process.run", "failed", level=logging.ERROR)
        exit_code = 1
    raise SystemExit(exit_code)
