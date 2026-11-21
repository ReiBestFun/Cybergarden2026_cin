"""SMTP channel: one message, recipients hidden from each other (envelope-only, like Bcc)."""

import re
import smtplib
import ssl
from contextlib import suppress
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .base import Delivery, DeliveryError, Message, NotifierError

ADDRESS = re.compile(r"^[^@\s<>,;\"]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$")


class EmailChannel:
    name = "email"

    def __init__(
        self, host: str | None, *, port: int = 587, security: str = "starttls", username: str | None = None,
        password: str | None = None, sender: str | None = None, timeout: float = 15,
    ):
        if not host or not sender:
            raise NotifierError("SMTP_NOT_CONFIGURED", "Почта не настроена: задайте SMTP_HOST и SMTP_FROM на сервере.")
        self._host, self._port, self._security = host, port, security
        self._username, self._password, self._sender, self._timeout = username, password, sender, timeout

    def _connect(self) -> smtplib.SMTP:
        if self._security == "ssl":
            return smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout, context=ssl.create_default_context())
        client = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        try:
            if self._security == "starttls":
                client.starttls(context=ssl.create_default_context())
        except Exception:
            client.close()
            raise
        return client

    def _message(self, message: Message, message_id: str) -> EmailMessage:
        email = EmailMessage()
        email["From"] = self._sender
        email["To"] = "undisclosed-recipients:;"
        email["Subject"] = message.subject
        email["Date"] = formatdate(localtime=True)
        email["Message-ID"] = message_id
        email.set_content(message.text)
        for attachment in message.attachments:
            maintype, _, subtype = attachment.mime_type.partition("/")
            email.add_attachment(attachment.content, maintype=maintype, subtype=subtype, filename=attachment.filename)
        return email

    def send(self, recipients: list[str], message: Message) -> list[Delivery]:
        email = self._message(message, make_msgid(domain=self._sender.rsplit("@", 1)[-1]))
        try:
            with self._connect() as client:
                if self._username:
                    client.login(self._username, self._password or "")
                refused = client.send_message(email, from_addr=self._sender, to_addrs=recipients)
        except smtplib.SMTPAuthenticationError:
            raise NotifierError("SMTP_UNAUTHORIZED", "Почтовый сервер отклонил логин или пароль.") from None
        except smtplib.SMTPRecipientsRefused as error:
            return [Delivery(address, "failed", _reason(error.recipients.get(address))) for address in recipients]
        except (smtplib.SMTPException, OSError) as error:
            raise NotifierError("SMTP_UNAVAILABLE", f"Не удалось отправить письмо: {type(error).__name__}.") from None
        return [
            Delivery(address, "failed", _reason(refused[address])) if address in refused else Delivery(address, "sent")
            for address in recipients
        ]

    def send_one(self, recipient: str, message: Message, *, delivery_id: str) -> str:
        """Submit one delivery and distinguish refusals from uncertain outcomes."""
        if self._security == "none" and self._username:
            raise DeliveryError("SMTP_TLS_REQUIRED", "Для SMTP с авторизацией требуется TLS.")
        message_id = f"<{delivery_id}@{self._sender.rsplit('@', 1)[-1]}>"
        try:
            email = self._message(message, message_id)
        except (ValueError, TypeError):
            raise DeliveryError("SMTP_INVALID_MESSAGE", "Не удалось сформировать письмо. Проверьте отправителя и тему.") from None
        client = None
        try:
            client = self._connect()
            if self._username:
                client.login(self._username, self._password or "")
        except smtplib.SMTPAuthenticationError as error:
            if client is not None:
                client.close()
            raise _smtp_rejection(error.smtp_code, "SMTP_UNAUTHORIZED", "SMTP отклонил авторизацию.") from None
        except (smtplib.SMTPNotSupportedError, ssl.SSLCertVerificationError):
            if client is not None:
                client.close()
            raise DeliveryError("SMTP_TLS_FAILED", "Не удалось установить требуемое защищённое соединение SMTP.") from None
        except smtplib.SMTPResponseException as error:
            if client is not None:
                client.close()
            raise _smtp_rejection(error.smtp_code, "SMTP_CONNECT_FAILED", "SMTP отклонил подключение.") from None
        except (OSError, smtplib.SMTPException):
            if client is not None:
                client.close()
            raise DeliveryError("SMTP_CONNECT_FAILED", "Не удалось подключиться к SMTP.", outcome="retry") from None
        try:
            refused = client.send_message(email, from_addr=self._sender, to_addrs=[recipient])
            if recipient in refused:
                raise _smtp_rejection(refused[recipient][0], "SMTP_RECIPIENT_REJECTED", "SMTP отклонил получателя.")
        except smtplib.SMTPRecipientsRefused as error:
            reply = error.recipients.get(recipient)
            raise _smtp_rejection(reply[0] if reply else 550, "SMTP_RECIPIENT_REJECTED", "SMTP отклонил получателя.") from None
        except smtplib.SMTPResponseException as error:
            raise _smtp_rejection(error.smtp_code, "SMTP_SUBMISSION_REJECTED", "SMTP отклонил письмо.") from None
        except (OSError, smtplib.SMTPException):
            raise DeliveryError("SMTP_OUTCOME_UNKNOWN", "Подтверждение приёма письма не получено.", outcome="unknown") from None
        finally:
            # A disconnect during QUIT must not change the result of the DATA response.
            with suppress(OSError):
                client.close()
        return message_id


def _smtp_rejection(code: int, error_code: str, message: str) -> DeliveryError:
    return DeliveryError(error_code, f"{message} Код SMTP {code}.", outcome="retry" if 400 <= code < 500 else "failed")


def _reason(reply: tuple[int, bytes] | None) -> str:
    if not reply:
        return "Сервер отклонил адрес."
    return f"SMTP {reply[0]}: сервер отклонил адрес."
