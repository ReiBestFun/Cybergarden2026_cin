from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator

from .contracts import Input, Name, ReadModel

CredentialKind = Literal["telegram", "smtp", "github_token", "ssh_key", "llm", "webhook", "webhook_url"]


class CredentialValue(Input):
    secret: Annotated[SecretStr, Field(min_length=1, max_length=16384)]
    username: Annotated[SecretStr, Field(min_length=1, max_length=320)] | None = None
    passphrase: Annotated[SecretStr, Field(min_length=1, max_length=1024)] | None = None

    @field_validator("secret")
    @classmethod
    def nonblank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Введите значение секрета.")
        return value


class CredentialCreate(Input):
    name: Name
    kind: CredentialKind
    value: CredentialValue


class CredentialPatch(Input):
    name: Name | None = None
    value: CredentialValue | None = None
    active: bool | None = None


class CredentialRead(ReadModel):
    id: UUID
    project_id: UUID
    name: str
    kind: CredentialKind
    active: bool
