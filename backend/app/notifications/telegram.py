"""Telegram Bot API channel (sendMessage + sendDocument), stdlib only."""

import json
import re
import socket
import uuid
import urllib.error
import urllib.request

from .base import Attachment, Delivery, DeliveryError, Message, NotifierError

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

    def _request(self, method: str, data: bytes, content_type: str):
        request = urllib.request.Request(self._base + method, data=data, headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read() or b"{}")
            except (OSError, ValueError):
                payload = {}
            finally:
                error.close()
            raise _telegram_error(error.code, payload, self._token) from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, (ConnectionRefusedError, socket.gaierror)):
                raise DeliveryError("TELEGRAM_UNAVAILABLE", "Не удалось подключиться к Telegram.", outcome="retry") from None
            raise _unknown() from None
        except (OSError, ValueError):
            raise _unknown() from None
        if not isinstance(payload, dict) or type(payload.get("ok")) is not bool:
            raise _unknown()
        if not payload.get("ok", False):
            raise _telegram_error(payload.get("error_code"), payload, self._token)
        return payload.get("result")

    def lookup(self, method: str, **params):
        return self._request(method, json.dumps(params).encode(), "application/json")

    def _call(self, method: str, data: bytes, content_type: str) -> str | None:
        result = self._request(method, data, content_type)
        message_id = result.get("message_id") if isinstance(result, dict) else None
        return str(message_id) if type(message_id) is int else None

    def send_text(self, chat_id: str, text: str) -> str | None:
        text = text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT - 1] + "…"
        body = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True}).encode("utf-8")
        return self._call("sendMessage", body, "application/json")

    def send_attachment(self, chat_id: str, attachment: Attachment) -> str | None:
        boundary = uuid.uuid4().hex
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode(),
            (f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{attachment.filename}"\r\n'
             f"Content-Type: {attachment.mime_type}\r\n\r\n").encode(),
            attachment.content,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        return self._call("sendDocument", b"".join(parts), f"multipart/form-data; boundary={boundary}")

    def send(self, recipients: list[str], message: Message) -> list[Delivery]:
        deliveries = []
        for chat_id in recipients:
            try:
                self.send_text(chat_id, message.text)
                for attachment in message.attachments:
                    self.send_attachment(chat_id, attachment)
                deliveries.append(Delivery(chat_id, "sent"))
            except _RecipientRejected as error:
                deliveries.append(Delivery(chat_id, "failed", error.provider_reason or error.message))
        return deliveries


def _unknown() -> DeliveryError:
    return DeliveryError("TELEGRAM_OUTCOME_UNKNOWN", "Подтверждение отправки в Telegram не получено.", outcome="unknown")


class _RecipientRejected(DeliveryError):
    def __init__(self, code: int, reason: str | None):
        super().__init__("TELEGRAM_RECIPIENT_REJECTED", f"Telegram отклонил запрос. Код HTTP {code}.")
        self.provider_reason = reason


def _telegram_error(code: object, payload: object, token: str) -> DeliveryError:
    if code == 401:
        return DeliveryError("TELEGRAM_UNAUTHORIZED", "Telegram отклонил токен бота.")
    if code == 429:
        parameters = payload.get("parameters") if isinstance(payload, dict) else None
        delay = parameters.get("retry_after") if isinstance(parameters, dict) else None
        retry_after = delay if type(delay) is int and 0 <= delay <= 86400 else None
        return DeliveryError("TELEGRAM_RATE_LIMIT", "Telegram ограничил частоту отправки.", outcome="retry", retry_after=retry_after)
    if type(code) is int and 400 <= code < 500:
        detail = payload.get("description") if isinstance(payload, dict) else None
        reason = detail.replace(token, "***")[:255] if isinstance(detail, str) else None
        return _RecipientRejected(code, reason)
    if isinstance(payload, dict) and payload.get("ok") is False:
        return DeliveryError("TELEGRAM_UNAVAILABLE", "Telegram вернул временную ошибку.", outcome="retry")
    return _unknown()
