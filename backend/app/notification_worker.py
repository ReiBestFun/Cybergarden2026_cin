"""Deliver reports queued by the workflow worker."""

import argparse
import logging
import signal
from datetime import UTC, datetime
from threading import Event

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .db.encryption import check_storage_key
from .db.session import make_engine
from .notifications.base import DeliveryError, NotifierError
from .notifications.destinations import verified_telegram_recipients
from .notifications.email import EmailChannel
from .notifications.queue import ClaimedDelivery, allowed_recipients, claim_delivery, expire_deliveries, finish_delivery
from .notifications.telegram import TelegramChannel

log = logging.getLogger(__name__)


def _send(job: ClaimedDelivery, settings: Settings) -> str | None:
    restricted = job.channel == "email" or settings.notify_telegram_recipients
    if restricted and job.recipient not in allowed_recipients(settings, job.channel):
        raise DeliveryError("RECIPIENT_NOT_ALLOWED", "Получатель не входит в разрешённый список сервера.")
    if job.channel == "email":
        channel = EmailChannel(
            settings.smtp_host, port=settings.smtp_port, security=settings.smtp_security,
            username=settings.smtp_username,
            password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
            sender=settings.smtp_from, timeout=settings.notify_timeout_seconds,
        )
        return channel.send_one(job.recipient, job.message, delivery_id=str(job.id))
    token = settings.telegram_bot_token.get_secret_value() if settings.telegram_bot_token else None
    channel = TelegramChannel(token, api_base=settings.telegram_api_base, timeout=settings.notify_timeout_seconds)
    if job.phase == "attachment":
        return channel.send_attachment(job.recipient, job.message.attachments[0])
    return channel.send_text(job.recipient, job.message.text)


def process_next(sessions: sessionmaker[Session], settings: Settings) -> bool:
    with sessions.begin() as db:
        now = datetime.now(UTC)
        expire_deliveries(db, now)
        job = claim_delivery(db, now, lease_seconds=max(60, settings.notify_timeout_seconds * 12))
        unverified = job is not None and job.channel == "telegram" and not verified_telegram_recipients(
            db, job.owner_id, [job.recipient],
        )
    if job is None:
        return False

    provider_id = None
    error = None
    try:
        if unverified:
            raise DeliveryError("TELEGRAM_DESTINATION_UNVERIFIED", "Подтвердите этот Telegram-чат в профиле владельца проекта.")
        provider_id = _send(job, settings)
    except DeliveryError as failure:
        error = failure
    except NotifierError as failure:
        error = DeliveryError(failure.code, failure.message)
    except Exception as failure:
        log.error("Delivery %s %s stopped with %s", job.id, job.phase, type(failure).__name__)
        error = DeliveryError("DELIVERY_OUTCOME_UNKNOWN", "Отправка завершилась без подтверждения.", outcome="unknown")
    with sessions.begin() as db:
        stored = finish_delivery(db, job, provider_id=provider_id, error=error, max_attempts=settings.notify_max_attempts)
    if not stored:
        log.warning("Delivery %s %s has already expired", job.id, job.phase)
    elif error:
        log.info("Delivery %s %s: %s", job.id, job.phase, error.code)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Отправляет отчёты из очереди PostgreSQL.")
    parser.add_argument("--once", action="store_true", help="Обработать не более одной попытки.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    engine = make_engine(settings.database_url, settings.data_encryption_key)
    with engine.connect() as connection:
        check_storage_key(connection)
    sessions = sessionmaker(engine)
    stop = Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        while not stop.is_set():
            try:
                processed = process_next(sessions, settings)
            except SQLAlchemyError:
                log.error("Could not access the notification queue")
                if args.once:
                    return 1
                processed = False
            if args.once:
                break
            if not processed:
                stop.wait(settings.notify_poll_seconds)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
