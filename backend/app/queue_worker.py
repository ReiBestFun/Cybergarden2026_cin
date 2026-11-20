"""Consume queued workflow runs from PostgreSQL."""

import argparse
import logging
import signal
from datetime import UTC, datetime, timedelta
from threading import Event, Thread

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .analyzers.models import Warning
from .config import Settings
from .db.session import make_engine
from .integrations.repository import allowed_repository_urls
from .workflows.execution import GraphExecution
from .workflows.persistence import ClaimLost, RunProgress
from .workflows.queue import claim_next, expire_stale_runs

log = logging.getLogger(__name__)


def _heartbeat(progress: RunProgress, stop: Event, interval: float) -> None:
    while not stop.wait(interval):
        try:
            if not progress.heartbeat():
                return
        except SQLAlchemyError:
            log.warning("Could not renew claim for run %s", progress.claim.id)


def run_next(
    sessions: sessionmaker[Session], settings: Settings, allowed: frozenset[str]
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
    allowed = allowed_repository_urls(settings.allowed_repositories)
    engine = make_engine(settings.database_url)
    sessions = sessionmaker(engine)
    stop = Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    try:
        while not stop.is_set():
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
