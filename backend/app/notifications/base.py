from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True)
class Attachment:
    filename: str
    content: bytes
    mime_type: str


@dataclass(frozen=True)
class Message:
    subject: str
    text: str
    attachments: tuple[Attachment, ...] = field(default=())


@dataclass(frozen=True)
class Delivery:
    recipient: str
    status: Literal["sent", "failed"]
    error: str | None = None


class NotifierError(Exception):
    """The channel cannot be used at all (not configured, unreachable, bad credentials)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Channel(Protocol):
    name: str

    def send(self, recipients: list[str], message: Message) -> list[Delivery]: ...
