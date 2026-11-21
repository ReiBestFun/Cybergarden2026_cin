"""Save and verify a user's Telegram destinations with the server bot."""

from datetime import UTC, datetime, timedelta
from secrets import compare_digest, randbelow
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import StringConstraints
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from ..db.encryption import require_cipher
from ..db.models import TelegramChallengeLimit, TelegramDestination
from ..db.session import Database
from ..notifications.base import NotifierError
from ..notifications.destinations import numeric_chat_id, telegram_chat_key
from ..notifications.queue import allowed_recipients
from ..notifications.telegram import TelegramChannel
from ..schemas.contracts import Input, Name, Page, ReadModel
from .auth import CurrentUser
from .common import ApiError, PageQuery, page

router = APIRouter(prefix="/telegram-destinations", tags=["telegram"])
_CODE_TTL = timedelta(minutes=10)
_MAX_ATTEMPTS = 5


class DestinationCreate(Input):
    label: Name
    chat_id: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^-?[0-9]{1,16}$")]


class DestinationPatch(Input):
    label: Name


class VerifyDestination(Input):
    code: Annotated[str, StringConstraints(pattern=r"^[0-9]{8}$")]


class DestinationRead(ReadModel):
    id: UUID
    label: str
    chat_id: str
    verified_at: datetime | None
    challenge_expires_at: datetime | None


def _destination(destination_id: UUID, owner_id: UUID, db: Database, *, lock: bool = False) -> TelegramDestination:
    query = select(TelegramDestination).where(TelegramDestination.id == destination_id, TelegramDestination.owner_id == owner_id)
    destination = db.scalar(query.with_for_update() if lock else query)
    if destination is None:
        raise ApiError(404, "DESTINATION_NOT_FOUND", "Telegram-чат не найден.")
    return destination


@router.get("", response_model=Page[DestinationRead])
def list_destinations(db: Database, user: CurrentUser, pagination: PageQuery) -> dict:
    query = select(TelegramDestination).where(TelegramDestination.owner_id == user.id).order_by(TelegramDestination.id)
    return page(db, query, pagination.limit, pagination.offset)


@router.post("", status_code=201, response_model=DestinationRead)
def create_destination(data: DestinationCreate, db: Database, user: CurrentUser) -> TelegramDestination:
    try:
        chat_id = numeric_chat_id(data.chat_id)
    except ValueError as error:
        raise ApiError(422, "INVALID_TELEGRAM_CHAT", str(error), field="chat_id") from None
    key = telegram_chat_key(require_cipher(db.get_bind().dialect), user.id, chat_id)
    destination = db.scalar(insert(TelegramDestination).values(
        owner_id=user.id, chat_key=key, chat_id=chat_id, label=data.label,
    ).on_conflict_do_nothing(constraint="uq_telegram_destination_owner_chat").returning(TelegramDestination))
    if destination is None:
        raise ApiError(409, "DESTINATION_EXISTS", "Этот чат уже сохранён в профиле.")
    db.commit()
    return destination


@router.patch("/{destination_id}", response_model=DestinationRead)
def rename_destination(destination_id: UUID, data: DestinationPatch, db: Database, user: CurrentUser) -> TelegramDestination:
    destination = _destination(destination_id, user.id, db, lock=True)
    destination.label = data.label
    db.commit()
    return destination


@router.delete("/{destination_id}", status_code=204)
def delete_destination(destination_id: UUID, db: Database, user: CurrentUser) -> Response:
    db.delete(_destination(destination_id, user.id, db, lock=True))
    db.commit()
    return Response(status_code=204)


def _reserve_delivery(db: Database, owner_id: UUID, chat_id: str, now: datetime) -> None:
    cipher = require_cipher(db.get_bind().dialect)
    limits = {
        cipher.digest("telegram-rate-user", str(owner_id)): timedelta(minutes=1),
        cipher.digest("telegram-rate-chat", chat_id): timedelta(minutes=5),
    }
    db.execute(delete(TelegramChallengeLimit).where(TelegramChallengeLimit.next_at <= now - timedelta(days=1)))
    for key, delay in sorted(limits.items()):
        statement = insert(TelegramChallengeLimit).values(key=key, next_at=now + delay)
        reserved = db.scalar(statement.on_conflict_do_update(
            index_elements=[TelegramChallengeLimit.key], set_={"next_at": statement.excluded.next_at},
            where=TelegramChallengeLimit.next_at <= now,
        ).returning(TelegramChallengeLimit.key))
        if reserved is None:
            raise ApiError(429, "TELEGRAM_CHALLENGE_LIMIT", "Код уже запрашивали недавно. Повторите через несколько минут.")


@router.post("/{destination_id}/challenge", response_model=DestinationRead)
def challenge_destination(destination_id: UUID, db: Database, user: CurrentUser, request: Request) -> TelegramDestination:
    destination = _destination(destination_id, user.id, db, lock=True)
    if destination.verified_at is not None:
        return destination
    settings = request.app.state.settings
    token = settings.telegram_bot_token.get_secret_value() if settings.telegram_bot_token else ""
    if not token:
        raise ApiError(503, "TELEGRAM_NOT_CONFIGURED", "На сервере CyberFlow не настроен Telegram-бот.")
    allowed = allowed_recipients(settings, "telegram")
    if allowed is not None and destination.chat_id not in allowed:
        raise ApiError(403, "RECIPIENT_NOT_ALLOWED", "Этот Telegram-чат не входит в разрешённый список сервера.")
    now = datetime.now(UTC)
    _reserve_delivery(db, user.id, destination.chat_id, now)
    code = f"{randbelow(100_000_000):08d}"
    cipher = require_cipher(db.get_bind().dialect)
    destination.challenge_hash = cipher.digest(f"telegram-code:{destination.id}", code)
    destination.challenge_expires_at = now + _CODE_TTL
    destination.challenge_attempts = 0
    db.commit()
    try:
        TelegramChannel(token, api_base=settings.telegram_api_base, timeout=settings.notify_timeout_seconds).send_text(
            destination.chat_id, f"Код подтверждения Telegram-чата в CyberFlow: {code}. Действует 10 минут.",
        )
    except NotifierError as error:
        raise ApiError(502, error.code, error.message) from None
    return destination


@router.post("/{destination_id}/verify", response_model=DestinationRead)
def verify_destination(destination_id: UUID, data: VerifyDestination, db: Database, user: CurrentUser) -> TelegramDestination:
    destination = _destination(destination_id, user.id, db, lock=True)
    if destination.verified_at is not None:
        return destination
    now = datetime.now(UTC)
    if not destination.challenge_hash or not destination.challenge_expires_at or destination.challenge_expires_at <= now:
        raise ApiError(409, "TELEGRAM_CHALLENGE_EXPIRED", "Запросите новый код подтверждения.")
    cipher = require_cipher(db.get_bind().dialect)
    if not compare_digest(destination.challenge_hash, cipher.digest(f"telegram-code:{destination.id}", data.code)):
        destination.challenge_attempts += 1
        if destination.challenge_attempts >= _MAX_ATTEMPTS:
            destination.challenge_hash = None
            destination.challenge_expires_at = None
        db.commit()
        raise ApiError(422, "TELEGRAM_CODE_INVALID", "Код подтверждения неверен.")
    destination.verified_at = now
    destination.challenge_hash = None
    destination.challenge_expires_at = None
    destination.challenge_attempts = 0
    db.commit()
    return destination
