"""Deliver reports queued by the workflow worker."""

import argparse
import logging
import signal
from datetime import UTC, datetime
from threading import Event

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .analyzers.models import ScanError
from .config import Settings
from .credentials import resolve_credential
from .db.encryption import check_storage_key
from .db.models import ScanRun, WorkspaceDeliverySetting
from .db.session import make_engine
from .notifications.base import DeliveryError, NotifierError
from .notifications.destinations import verified_telegram_recipients
from .notifications.email import EmailChannel
from .notifications.queue import ClaimedDelivery, allowed_recipients, claim_delivery, expire_deliveries, finish_delivery
from .notifications.slack import send_slack_summary
from .notifications.telegram import TelegramChannel
from .schemas.credentials import CredentialValue
from .technical_log import log_event
from .workspace_logs import diagnostic

def _send(job: ClaimedDelivery, settings: Settings, credential: CredentialValue | None) -> str | None:
    if job.channel == "slack":
        if credential is None:
            raise DeliveryError("SLACK_NOT_CONFIGURED", "Подключите Slack в настройках команды.")
        send_slack_summary(credential.secret.get_secret_value(), job.message.text, timeout=settings.notify_timeout_seconds)
        return None
    if job.channel == "email":
        username = credential.username.get_secret_value() if credential and credential.username else settings.smtp_username
        password = credential.secret if credential else settings.smtp_password
        if credential and not username:
            raise DeliveryError("SMTP_USERNAME_REQUIRED", "Укажите логин для выбранных учётных данных SMTP.")
        channel = EmailChannel(
            settings.smtp_host, port=settings.smtp_port, security=settings.smtp_security,
            username=username, password=password.get_secret_value() if password else None,
            sender=settings.smtp_from, timeout=settings.notify_timeout_seconds,
        )
        return channel.send_one(job.recipient, job.message, delivery_id=str(job.id))
    token = credential.secret if credential else settings.telegram_bot_token
    channel = TelegramChannel(token.get_secret_value() if token else None, api_base=settings.telegram_api_base, timeout=settings.notify_timeout_seconds)
    if job.phase == "attachment":
        return channel.send_attachment(job.recipient, job.message.attachments[0])
    return channel.send_text(job.recipient, job.message.text)


def process_next(sessions: sessionmaker[Session], settings: Settings) -> bool:
    with sessions.begin() as db:
        now = datetime.now(UTC)
        recovered = expire_deliveries(db, now)
        job = claim_delivery(db, now, lease_seconds=max(60, settings.notify_timeout_seconds * 12))
        unverified = job is not None and job.settings_workspace_id is None and job.channel == "telegram" and not verified_telegram_recipients(
            db, job.owner_id, [job.recipient],
        )
    for delivery in recovered:
        log_event("notification_worker", "delivery.recover", delivery.status, level=logging.WARNING,
                  run_id=delivery.run_id, node_run_id=delivery.node_run_id, delivery_id=delivery.id)
    if job is None:
        return False
    log_event("notification_worker", "delivery.claim", "sending", run_id=job.run_id, node_run_id=job.node_run_id,
              delivery_id=job.id, phase=job.phase)

    provider_id = None
    error = None
    try:
        if job.owner_id is None:
            raise DeliveryError("PROJECT_UNOWNED", "Назначьте владельца проекта перед отправкой отчётов.")
        if unverified:
            raise DeliveryError("TELEGRAM_DESTINATION_UNVERIFIED", "Подтвердите этот Telegram-чат в профиле владельца проекта.")
        allowed = allowed_recipients(settings, job.channel)
        if allowed is not None and job.recipient not in allowed:
            raise DeliveryError("RECIPIENT_NOT_ALLOWED", "Получатель не входит в разрешённый список сервера.")
        credential = None
        if job.settings_workspace_id is not None:
            if job.settings_workspace_id != job.workspace_id:
                raise DeliveryError("WORKSPACE_DELIVERY_CHANGED", "Проект больше не принадлежит пространству этого уведомления.")
            with sessions() as db:
                policy = db.get(WorkspaceDeliverySetting, (job.settings_workspace_id, job.channel))
                if job.channel == "slack":
                    if policy is None or policy.version != job.settings_version or policy.secret is None:
                        raise DeliveryError("SLACK_DESTINATION_CHANGED", "Webhook Slack изменился после постановки в очередь.")
                elif policy is None or job.recipient not in policy.recipients:
                    raise DeliveryError("WORKSPACE_RECIPIENT_REMOVED", "Получатель удалён из настроек команды.")
                if policy.secret is not None:
                    credential = CredentialValue(secret=policy.secret, username=policy.username)
        elif job.credential_id is not None and job.project_id is not None:
            with sessions() as db:
                credential = resolve_credential(db, settings, job.project_id, job.credential_id,
                                                "smtp" if job.channel == "email" else "telegram")
        provider_id = _send(job, settings, credential)
    except DeliveryError as failure:
        error = failure
    except NotifierError as failure:
        error = DeliveryError(failure.code, failure.message)
    except ScanError as failure:
        error = DeliveryError(failure.warning.code, failure.warning.message)
    except Exception:
        error = DeliveryError("DELIVERY_OUTCOME_UNKNOWN", "Отправка завершилась без подтверждения.", outcome="unknown")
    with sessions.begin() as db:
        status = finish_delivery(db, job, provider_id=provider_id, error=error, max_attempts=settings.notify_max_attempts)
        if status is not None:
            diagnostic(db, db.get(ScanRun, job.run_id), "delivery.finish", status, job.node_run_id)
    level = logging.WARNING if status is None or error is not None else logging.INFO
    log_event("notification_worker", "delivery.finish", status or "claim_lost", level=level,
              run_id=job.run_id, node_run_id=job.node_run_id, delivery_id=job.id, phase=job.phase)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Отправляет отчёты из очереди PostgreSQL.")
    parser.add_argument("--once", action="store_true", help="Обработать не более одной попытки.")
    args = parser.parse_args()
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
                log_event("notification_worker", "delivery.queue", "failed", level=logging.ERROR)
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
    try:
        exit_code = main()
    except Exception:
        log_event("notification_worker", "process.run", "failed", level=logging.ERROR)
        exit_code = 1
    raise SystemExit(exit_code)
