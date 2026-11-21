"""Delivery of run reports to notification channels (Telegram, e-mail).

Each channel implements `Channel.send(recipients, message)` and reports a status per
recipient, so one bad address never blocks delivery to the others. Adding a channel is a
new module plus one entry in `CHANNELS`.
"""

from .base import Attachment, Channel, Delivery, Message, NotifierError
from .content import build_message
from .email import EmailChannel
from .telegram import TelegramChannel

__all__ = [
    "Attachment", "Channel", "Delivery", "Message", "NotifierError",
    "EmailChannel", "TelegramChannel", "build_message",
]
