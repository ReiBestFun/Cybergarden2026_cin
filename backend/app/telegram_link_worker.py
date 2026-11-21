"""Consume /start links for the server bot, including on localhost."""

import logging
import re
import time
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .db.encryption import require_cipher
from .db.models import TelegramDestination, TelegramLink
from .db.session import make_engine
from .notifications.base import NotifierError
from .notifications.destinations import link_token_hash, telegram_chat_key
from .notifications.queue import allowed_recipients
from .notifications.telegram import TelegramChannel
from .technical_log import log_event



def consume_start(db: Session, message: dict, settings: Settings, bot_name: str) -> None:
    command = re.fullmatch(r"/start(?:@([A-Za-z0-9_]+))? ([A-Za-z0-9_-]{43})", message.get("text") or "")
    if not command or (command[1] and command[1].lower() != bot_name.lower()):
        return
    if message.get("forward_origin") or message.get("via_bot"):
        return
    chat = message.get("chat", {})
    sender = message.get("from", {})
    private = chat.get("type") == "private"
    if not private and chat.get("type") not in {"group", "supergroup"}:
        return
    if sender.get("is_bot") is not False or (private and sender.get("id") != chat.get("id")):
        return
    now = datetime.now(UTC)
    link = db.scalar(select(TelegramLink).where(
        TelegramLink.token_hash == link_token_hash(command[2]), TelegramLink.expires_at > now,
        TelegramLink.group_chat == (not private),
    ).with_for_update())
    if link is None:
        return
    chat_id = str(chat["id"])
    link.token_hash = None
    if settings.notify_telegram_recipients and chat_id not in allowed_recipients(settings, "telegram"):
        return
    key = telegram_chat_key(require_cipher(db.get_bind().dialect), link.owner_id, chat_id)
    label = ("@" + chat["username"] if chat.get("username") else
             chat.get("title") or chat.get("first_name") or "Telegram")
    statement = insert(TelegramDestination).values(owner_id=link.owner_id, chat_key=key, chat_id=chat_id,
        label=label[:120])
    # Only the profile's existing verification flow can confirm a newly discovered chat.
    link.destination_id = db.scalar(statement.on_conflict_do_update(
        constraint="uq_telegram_destination_owner_chat", set_={"chat_key": key},
    ).returning(TelegramDestination.id))


def main() -> None:
    settings = Settings()
    bot = TelegramChannel(settings.telegram_bot_token.get_secret_value() if settings.telegram_bot_token else None,
                          api_base=settings.telegram_api_base, timeout=25)
    sessions = sessionmaker(make_engine(settings.database_url, settings.data_encryption_key))
    offset = 0
    bot_name = None
    while True:
        try:
            if not bot_name:
                bot_name = bot.lookup("getMe")["username"]
            updates = bot.lookup("getUpdates", offset=offset, timeout=15, allowed_updates=["message"])
            for update in updates:
                with sessions.begin() as db:
                    consume_start(db, update.get("message", {}), settings, bot_name)
                offset = update["update_id"] + 1
        except (NotifierError, SQLAlchemyError, KeyError, TypeError):
            log_event("telegram_link_worker", "telegram.poll", "failed", level=logging.WARNING)
            time.sleep(5)


if __name__ == "__main__":
    main()
