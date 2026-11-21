"""Telegram Bot API channel (sendMessage + sendDocument), stdlib only."""

import json
import re
import uuid
import urllib.error
import urllib.request

from .base import Delivery, Message, NotifierError

_MAX_TEXT = 4096
CHAT_ID = re.compile(r"^(?:-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$")


class TelegramChannel:
    name = "telegram"

    def __init__(self, token: str | None, *, api_base: str = "https://api.telegram.org", timeout: float = 15):
        if not token:
            raise NotifierError("TELEGRAM_NOT_CONFIGURED", "Telegram не настроен: задайте TELEGRAM_BOT_TOKEN на сервере.")
        self._token = token
        self._base = f"{api_base.rstrip('/')}/bot{token}/"
        self._timeout = timeout

    def _clean(self, text: str) -> str:
        # Never let the bot token leak into stored errors or logs.
        return text.replace(self._token, "***")

    def _call(self, method: str, data: bytes, content_type: str) -> None:
        request = urllib.request.Request(self._base + method, data=data, headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read() or b"{}")
            except ValueError:
                payload = {}
            description = self._clean(str(payload.get("description") or f"HTTP {error.code}"))
            if error.code == 401:
                raise NotifierError("TELEGRAM_UNAUTHORIZED", "Telegram отклонил токен бота.") from None
            raise _RecipientError(description) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise NotifierError("TELEGRAM_UNAVAILABLE", self._clean(f"Telegram недоступен: {getattr(error, 'reason', error)}")) from None
        if not payload.get("ok", False):
            raise _RecipientError(self._clean(str(payload.get("description") or "Telegram вернул ошибку.")))

    def _send_one(self, chat_id: str, message: Message) -> None:
        text = message.text if len(message.text) <= _MAX_TEXT else message.text[:_MAX_TEXT - 1] + "…"
        body = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True}).encode("utf-8")
        self._call("sendMessage", body, "application/json")
        for attachment in message.attachments:
            boundary = uuid.uuid4().hex
            parts = [
                f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode(),
                (f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{attachment.filename}"\r\n'
                 f"Content-Type: {attachment.mime_type}\r\n\r\n").encode(),
                attachment.content,
                f"\r\n--{boundary}--\r\n".encode(),
            ]
            self._call("sendDocument", b"".join(parts), f"multipart/form-data; boundary={boundary}")

    def send(self, recipients: list[str], message: Message) -> list[Delivery]:
        deliveries = []
        for chat_id in recipients:
            try:
                self._send_one(chat_id, message)
                deliveries.append(Delivery(chat_id, "sent"))
            except _RecipientError as error:
                deliveries.append(Delivery(chat_id, "failed", str(error)))
        return deliveries


class _RecipientError(Exception):
    """Delivery to one chat failed (chat not found, bot blocked, ...); others may still succeed."""
