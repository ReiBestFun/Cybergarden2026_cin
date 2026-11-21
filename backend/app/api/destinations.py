"""Save and verify a user's Telegram destinations with the server bot."""

from datetime import UTC, datetime, timedelta
from secrets import compare_digest, randbelow, token_urlsafe
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response
from pydantic import StringConstraints
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from ..db.encryption import require_cipher
from ..db.models import TelegramChallengeLimit, TelegramDestination, TelegramLink
from ..db.session import Database
from ..notifications.base import NotifierError
from ..notifications.destinations import link_token_hash, telegram_chat_key
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


class LinkRequest(Input):
    group_chat: bool = False


class PublicChat(Input):
    username: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^@[A-Za-z][A-Za-z0-9_]{3,31}$")]


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
    numeric_id = int(data.chat_id)
    if not numeric_id or abs(numeric_id) >= 2**52:
        raise ApiError(422, "INVALID_TELEGRAM_CHAT", "Укажите числовой chat_id Telegram.", field="chat_id")
    chat_id = str(numeric_id)
    key = telegram_chat_key(require_cipher(db.get_bind().dialect), user.id, chat_id)
    destination = db.scalar(insert(TelegramDestination).values(
        owner_id=user.id, chat_key=key, chat_id=chat_id, label=data.label,
    ).on_conflict_do_nothing(constraint="uq_telegram_destination_owner_chat").returning(TelegramDestination))
    if destination is None:
        raise ApiError(409, "DESTINATION_EXISTS", "Этот чат уже сохранён в профиле.")
    db.commit()
    return destination


def _lookup(request: Request, method: str, *, expected_type: type = dict, **params):
    settings = request.app.state.settings
    token = settings.telegram_bot_token
    if not token:
        raise ApiError(503, "TELEGRAM_NOT_CONFIGURED", "На сервере CyberFlow не настроен Telegram-бот.")
    try:
        result = TelegramChannel(token.get_secret_value(), api_base=settings.telegram_api_base,
                                 timeout=settings.notify_timeout_seconds).lookup(method, **params)
    except NotifierError as error:
        raise ApiError(502, error.code, error.message) from None
    if not isinstance(result, expected_type):
        raise ApiError(502, "TELEGRAM_INVALID_RESPONSE", "Telegram вернул неожиданный ответ.")
    return result


@router.post("/links", status_code=201)
def start_link(data: LinkRequest, db: Database, user: CurrentUser, request: Request, response: Response) -> dict:
    bot_name = _lookup(request, "getMe").get("username")
    if not bot_name:
        raise ApiError(502, "TELEGRAM_INVALID_RESPONSE", "Не удалось получить имя бота.")
    token = token_urlsafe(32)
    link_id = uuid4()
    expires = datetime.now(UTC) + _CODE_TTL
    values = dict(id=link_id, token_hash=link_token_hash(token), group_chat=data.group_chat,
                  expires_at=expires, destination_id=None)
    db.execute(insert(TelegramLink).values(owner_id=user.id, **values).on_conflict_do_update(
        index_elements=[TelegramLink.owner_id], set_=values))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    param = "startgroup" if data.group_chat else "start"
    return {"id": link_id, "url": f"https://t.me/{bot_name}?{param}={token}", "expires_at": expires}


@router.get("/links/{link_id}", response_model=DestinationRead | None)
def link_status(link_id: UUID, db: Database, user: CurrentUser, response: Response):
    response.headers["Cache-Control"] = "no-store"
    link = db.get(TelegramLink, user.id)
    if not link or link.id != link_id:
        raise ApiError(404, "TELEGRAM_LINK_MISSING", "Эта ссылка заменена. Создайте новую.")
    if link.destination_id:
        return _destination(link.destination_id, user.id, db)
    if link.expires_at <= datetime.now(UTC):
        raise ApiError(410, "TELEGRAM_LINK_EXPIRED", "Ссылка истекла. Создайте новую.")
    if not link.token_hash:
        raise ApiError(403, "TELEGRAM_LINK_UNAVAILABLE", "Чат удалён или недоступен для подключения.")
    return None


@router.post("/public", response_model=DestinationRead, status_code=201)
def add_public_chat(data: PublicChat, db: Database, user: CurrentUser, request: Request) -> TelegramDestination:
    personal_ids = [value for value in db.scalars(select(TelegramDestination.chat_id).where(
        TelegramDestination.owner_id == user.id, TelegramDestination.verified_at.is_not(None),
    )) if int(value) > 0]
    if not personal_ids:
        raise ApiError(409, "TELEGRAM_PERSONAL_REQUIRED", "Сначала подключите личный Telegram для проверки прав администратора.")
    chat = _lookup(request, "getChat", chat_id=data.username)
    if chat.get("type") not in {"supergroup", "channel"} or type(chat.get("id")) is not int:
        raise ApiError(422, "INVALID_TELEGRAM_CHAT", "Укажите публичный канал или супергруппу.")
    chat_id = str(chat["id"])
    settings = request.app.state.settings
    if settings.notify_telegram_recipients and chat_id not in allowed_recipients(settings, "telegram"):
        raise ApiError(403, "RECIPIENT_NOT_ALLOWED", "Этот Telegram-чат не входит в разрешённый список сервера.")
    bot_id = _lookup(request, "getMe")["id"]
    bot_role = _lookup(request, "getChatMember", chat_id=chat_id, user_id=bot_id)
    if chat["type"] == "channel":
        can_send = bot_role.get("status") == "administrator" and bot_role.get("can_post_messages")
    else:
        can_send = (bot_role.get("status") == "administrator"
                    or (bot_role.get("status") == "member" and chat.get("permissions", {}).get("can_send_messages", True))
                    or (bot_role.get("status") == "restricted" and bot_role.get("is_member") and bot_role.get("can_send_messages")))
    if not can_send:
        raise ApiError(409, "TELEGRAM_BOT_PERMISSIONS", "Добавьте бота в чат и разрешите ему отправлять сообщения.")
    admins = {item["user"]["id"] for item in _lookup(request, "getChatAdministrators", expected_type=list, chat_id=chat_id)}
    if not any(int(value) in admins for value in personal_ids):
        raise ApiError(403, "TELEGRAM_ADMIN_REQUIRED", "Подключённый аккаунт должен быть администратором этого чата.")
    key = telegram_chat_key(require_cipher(db.get_bind().dialect), user.id, chat_id)
    now = datetime.now(UTC)
    saved = db.scalar(insert(TelegramDestination).values(owner_id=user.id, chat_key=key, chat_id=chat_id,
        label=data.username, verified_at=now).on_conflict_do_update(constraint="uq_telegram_destination_owner_chat",
        set_={"verified_at": now}).returning(TelegramDestination))
    db.commit()
    return saved


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
    if settings.notify_telegram_recipients and destination.chat_id not in allowed_recipients(settings, "telegram"):
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
