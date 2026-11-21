import json
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from .analyzers.models import ScanError, Warning
from .config import Settings
from .db.models import Credential
from .schemas.credentials import CredentialKind, CredentialValue


def _cipher(settings: Settings) -> Fernet:
    key = settings.credentials_encryption_key
    try:
        return Fernet(key.get_secret_value() if key else "")
    except (TypeError, ValueError):
        raise ScanError(Warning("CREDENTIALS_KEY_UNAVAILABLE", "Проверьте серверный ключ шифрования учётных данных.")) from None


def _identity(credential: Credential) -> list[str]:
    return [str(credential.id), str(credential.project_id), credential.kind]


def encrypt_credential(settings: Settings, credential: Credential, value: CredentialValue) -> str:
    if value.username is not None and credential.kind != "smtp":
        raise ScanError(Warning("CREDENTIAL_FIELDS_INVALID", "Логин можно указать для учётных данных SMTP."))
    if value.passphrase is not None and credential.kind != "ssh_key":
        raise ScanError(Warning("CREDENTIAL_FIELDS_INVALID", "Пароль ключа можно указать для учётных данных SSH."))
    payload = {
        "identity": _identity(credential),
        "value": {
            "secret": value.secret.get_secret_value(),
            "username": value.username.get_secret_value() if value.username is not None else None,
            "passphrase": value.passphrase.get_secret_value() if value.passphrase is not None else None,
        },
    }
    return _cipher(settings).encrypt(json.dumps(payload).encode()).decode("ascii")


def resolve_credential(
    db: Session, settings: Settings, project_id: UUID, credential_id: UUID, kind: CredentialKind,
) -> CredentialValue:
    credential = db.get(Credential, credential_id)
    if credential is None or credential.project_id != project_id or credential.kind != kind or not credential.active:
        raise ScanError(Warning("CREDENTIAL_UNAVAILABLE", "Выбранные учётные данные недоступны для этой ноды."))
    cipher = _cipher(settings)
    try:
        payload = json.loads(cipher.decrypt(credential.ciphertext))
        if payload["identity"] != _identity(credential):
            raise InvalidToken
        return CredentialValue.model_validate(payload["value"])
    except (InvalidToken, KeyError, TypeError, ValueError):
        raise ScanError(Warning("CREDENTIAL_DECRYPT_FAILED", "Не удалось расшифровать учётные данные.")) from None
