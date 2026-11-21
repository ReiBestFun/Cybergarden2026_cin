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
from .db.session import make_engine
from .integrations.repository import Allowlist, repository_allowlist
from .workflows.execution import GraphExecution
from .workflows.persistence import ClaimLost, RunProgress
from .workflows.queue import claim_next, enqueue_due_schedules, expire_stale_runs

log = logging.getLogger(__name__)


def _heartbeat(progress: RunProgress, stop: Event, interval: float) -> None:
    while not stop.wait(interval):
        try:
            if not progress.heartbeat():
                return
        except SQLAlchemyError:
            log.warning("Could not renew claim for run %s", progress.claim.id)


def tick_schedules(sessions: sessionmaker[Session]) -> int:
    with sessions.begin() as db:
        created = enqueue_due_schedules(db, datetime.now(UTC))
    if created:
        log.info("Enqueued %d scheduled runs", created)
    return created


def run_next(
    sessions: sessionmaker[Session], settings: Settings, allowed: Allowlist
) -> bool:
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.worker_stale_seconds)
    with sessions.begin() as db:
        expired = expire_stale_runs(db, cutoff)
    if expired:
        log.warning("Marked %d abandoned runs as failed", expired)
    with sessions.begin() as db:
        claim = claim_next(db)
    if claim is None:
        return False
    log.info("Claimed run %s", claim.id)
    progress = RunProgress(sessions, claim)
    stop = Event()
    heartbeat = Thread(
        target=_heartbeat, args=(progress, stop, min(5, settings.worker_stale_seconds / 3)), daemon=True,
    )
    heartbeat.start()
    try:
        GraphExecution(progress, settings, allowed).execute()
    except ClaimLost:
        log.warning("Run %s no longer belongs to this worker", claim.id)
    except Exception as error:
        log.error("Run %s stopped with %s", claim.id, type(error).__name__)
        try:
            progress.fail(Warning("WORKFLOW_EXECUTION_FAILED", "Не удалось завершить выполнение графа."))
        except ClaimLost:
            log.warning("Run %s was already finalized", claim.id)
    finally:
        stop.set()
        heartbeat.join()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Выполняет задания из очереди PostgreSQL.")
    parser.add_argument("--once", action="store_true", help="Обработать не более одного задания и завершиться.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    allowed = repository_allowlist(settings.allowed_repositories, settings.allow_any_repository)
    if allowed is None:
        log.warning("ALLOW_ANY_REPOSITORY is on: any public GitHub repository can be scanned")
    engine = make_engine(settings.database_url)
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
                    log.error("Could not enqueue scheduled runs")
                next_tick = monotonic() + settings.scheduler_poll_seconds
            try:
                processed = run_next(sessions, settings, allowed)
            except SQLAlchemyError:
                log.error("Could not access the PostgreSQL queue")
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
    raise SystemExit(main())
