"""Owner-scoped membership checks for saved Telegram destinations."""

import re
from collections.abc import Iterable
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.encryption import StorageCipher, require_cipher
from ..db.models import TelegramDestination


def link_token_hash(token: str) -> str:
    return sha256(token.encode("ascii")).hexdigest()


def numeric_chat_id(value: str) -> str:
    if not re.fullmatch(r"-?[0-9]{1,16}", value):
        raise ValueError("Укажите числовой chat_id Telegram.")
    number = int(value)
    if not number or abs(number) >= 2**52:
        raise ValueError("Укажите числовой chat_id Telegram.")
    return str(number)


def telegram_chat_key(cipher: StorageCipher, owner_id: UUID, chat_id: str) -> str:
    return cipher.digest(f"telegram-chat:{owner_id}", chat_id)


def verified_telegram_recipients(db: Session, owner_id: UUID | None, recipients: Iterable[str]) -> frozenset[str]:
    if owner_id is None:
        return frozenset()
    cipher = require_cipher(db.get_bind().dialect)
    keys = {telegram_chat_key(cipher, owner_id, recipient): recipient for recipient in recipients}
    if not keys:
        return frozenset()
    verified = db.scalars(select(TelegramDestination.chat_key).where(
        TelegramDestination.owner_id == owner_id, TelegramDestination.chat_key.in_(keys),
        TelegramDestination.verified_at.is_not(None),
    ))
    return frozenset(keys[key] for key in verified)
