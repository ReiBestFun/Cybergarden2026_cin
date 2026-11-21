"""SMTP channel: one message, recipients hidden from each other (envelope-only, like Bcc)."""

import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .base import Delivery, Message, NotifierError

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
        if self._security == "starttls":
            client.starttls(context=ssl.create_default_context())
        return client

    def send(self, recipients: list[str], message: Message) -> list[Delivery]:
        email = EmailMessage()
        email["From"] = self._sender
        email["To"] = "undisclosed-recipients:;"
        email["Subject"] = message.subject
        email["Date"] = formatdate(localtime=True)
        email["Message-ID"] = make_msgid(domain=self._sender.rsplit("@", 1)[-1])
        email.set_content(message.text)
        for attachment in message.attachments:
            maintype, _, subtype = attachment.mime_type.partition("/")
            email.add_attachment(attachment.content, maintype=maintype, subtype=subtype, filename=attachment.filename)
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


def _reason(reply: tuple[int, bytes] | None) -> str:
    if not reply:
        return "Сервер отклонил адрес."
    code, text = reply
    return f"{code} {text.decode('utf-8', 'replace')}".strip()
