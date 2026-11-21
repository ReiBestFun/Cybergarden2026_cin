"""Versioned authenticated encryption and keyed lookups for PostgreSQL fields."""

import base64
import binascii
import hmac
import json
import os
from datetime import time
from hashlib import sha256

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import LargeBinary, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

_PREFIX = b"cf1:"


class StorageCipher:
    def __init__(self, encoded_key: str):
        try:
            key = base64.b64decode(encoded_key, altchars=b"-_", validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("DATA_ENCRYPTION_KEY должен быть 32-байтовым ключом в URL-safe Base64.") from None
        if len(key) != 32:
            raise ValueError("DATA_ENCRYPTION_KEY должен быть 32-байтовым ключом в URL-safe Base64.")
        derived = HKDF(
            algorithm=hashes.SHA256(), length=64, salt=None, info=b"cyberflow/postgresql/v1",
        ).derive(key)
        self._aead = AESGCM(derived[:32])
        self._index_key = derived[32:]

    def digest(self, context: str, value: str) -> str:
        return hmac.new(self._index_key, context.encode() + b"\0" + value.encode(), sha256).hexdigest()

    def seal(self, context: str, value: bytes) -> bytes:
        nonce = os.urandom(12)
        return _PREFIX + nonce + self._aead.encrypt(nonce, value, _PREFIX + context.encode())

    def open(self, context: str, envelope: bytes) -> bytes:
        if not envelope.startswith(_PREFIX) or len(envelope) < len(_PREFIX) + 28:
            raise ValueError("Неверный формат зашифрованных данных CyberFlow.")
        nonce_start = len(_PREFIX)
        body_start = nonce_start + 12
        try:
            return self._aead.decrypt(
                envelope[nonce_start:body_start], envelope[body_start:], _PREFIX + context.encode(),
            )
        except InvalidTag:
            raise ValueError("Не удалось проверить зашифрованные данные CyberFlow. Проверьте ключ.") from None

    def seal_text(self, context: str, value: str) -> str:
        return base64.urlsafe_b64encode(self.seal(context, value.encode())).decode("ascii")

    def open_text(self, context: str, value: str) -> str:
        try:
            envelope = base64.b64decode(value, altchars=b"-_", validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Неверный формат зашифрованных данных CyberFlow.") from None
        return self.open(context, envelope).decode()


def require_cipher(dialect) -> StorageCipher:
    cipher = getattr(dialect, "cyberflow_cipher", None)
    if cipher is None:
        raise RuntimeError("Задайте DATA_ENCRYPTION_KEY для доступа к данным CyberFlow.")
    return cipher


def check_storage_key(connection) -> None:
    cipher = require_cipher(connection.dialect)
    expected = connection.scalar(text("SELECT key_check FROM storage_metadata WHERE id = 1"))
    if expected is None or not hmac.compare_digest(expected, cipher.digest("storage-check", "v1")):
        raise RuntimeError("DATA_ENCRYPTION_KEY не соответствует ключу базы CyberFlow.")


class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def __init__(self, context: str, *, preserve_mask: bool = False):
        super().__init__()
        self.context = context
        self.preserve_mask = preserve_mask

    def process_bind_param(self, value, dialect):
        if value is None or (self.preserve_mask and value == "***"):
            return value
        return require_cipher(dialect).seal_text(self.context, value)

    def process_result_value(self, value, dialect):
        if value is None or (self.preserve_mask and value == "***"):
            return value
        return require_cipher(dialect).open_text(self.context, value)


class EncryptedJSON(TypeDecorator):
    impl = JSONB(none_as_null=True)
    cache_ok = True

    def __init__(self, context: str):
        super().__init__()
        self.context = context

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return require_cipher(dialect).seal_text(self.context, text)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return json.loads(require_cipher(dialect).open_text(self.context, value))


class EncryptedTime(EncryptedText):
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return super().process_bind_param(value.isoformat() if value is not None else None, dialect)

    def process_result_value(self, value, dialect):
        plain = super().process_result_value(value, dialect)
        return time.fromisoformat(plain) if plain is not None else None


class EncryptedBinary(TypeDecorator):
    impl = LargeBinary
    cache_ok = True

    def __init__(self, context: str):
        super().__init__()
        self.context = context

    def process_bind_param(self, value, dialect):
        return None if value is None else require_cipher(dialect).seal(self.context, value)

    def process_result_value(self, value, dialect):
        return None if value is None else require_cipher(dialect).open(self.context, value)
