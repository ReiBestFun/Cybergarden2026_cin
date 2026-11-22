import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field, StringConstraints
from sqlalchemy import select

from ..db.models import WorkspaceDeliverySetting
from ..db.session import Database
from ..workspace_logs import audit_action
from ..notifications.destinations import numeric_chat_id, verified_telegram_recipients
from ..notifications.email import ADDRESS
from ..notifications.queue import allowed_recipients, recipient_key
from ..notifications.slack import SLACK_WEBHOOK
from ..schemas.contracts import Input, ReadModel
from ..schemas.credentials import CredentialValue
from .auth import CurrentUser
from .common import ApiError
from .workspaces import require_team_owner

router = APIRouter(prefix="/workspaces/{workspace_id}/delivery", tags=["workspace delivery"])
Channel = Literal["email", "telegram", "slack"]


class DeliverySettingsPatch(Input):
    version: int = Field(ge=0)
    recipients: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=254)]] = Field(max_length=50)
    credentials: CredentialValue | None = None


class ChannelRead(ReadModel):
    version: int
    recipients: list[str]
    has_credentials: bool


class DeliverySettingsRead(ReadModel):
    email: ChannelRead | None
    telegram: ChannelRead | None
    slack: ChannelRead | None


def _read(row: WorkspaceDeliverySetting) -> ChannelRead:
    return ChannelRead(version=row.version, recipients=row.recipients, has_credentials=row.secret is not None)


@router.get("", response_model=DeliverySettingsRead)
def read_delivery_settings(workspace_id: UUID, user: CurrentUser, db: Database) -> DeliverySettingsRead:
    require_team_owner(db, workspace_id, user.id, lock=False)
    rows = db.scalars(select(WorkspaceDeliverySetting).where(WorkspaceDeliverySetting.workspace_id == workspace_id))
    result = {row.channel: _read(row) for row in rows}
    return DeliverySettingsRead(email=result.get("email"), telegram=result.get("telegram"), slack=result.get("slack"))


@router.patch("/{channel}", response_model=ChannelRead)
def update_delivery_settings(
    workspace_id: UUID, channel: Channel, data: DeliverySettingsPatch, request: Request, user: CurrentUser, db: Database,
) -> ChannelRead:
    audit_action(db, workspace_id, user.id, "delivery.configure")
    require_team_owner(db, workspace_id, user.id)
    row = db.get(WorkspaceDeliverySetting, (workspace_id, channel))
    version = row.version if row else 0
    if data.version != version:
        raise ApiError(409, "DELIVERY_SETTINGS_CHANGED", "Настройки изменились. Обновите форму перед повторным сохранением.")
    if channel == "email":
        if any(not ADDRESS.fullmatch(value) for value in data.recipients):
            raise ApiError(422, "INVALID_RECIPIENT", "Укажите корректные адреса почты.", field="recipients")
        recipients = list(dict.fromkeys(recipient_key(channel, value) for value in data.recipients))
    elif channel == "telegram":
        try:
            recipients = list(dict.fromkeys(numeric_chat_id(value) for value in data.recipients))
        except ValueError as error:
            raise ApiError(422, "INVALID_RECIPIENT", str(error), field="recipients") from None
    else:
        if data.recipients:
            raise ApiError(422, "INVALID_RECIPIENT", "Получатель Slack определяется webhook.", field="recipients")
        recipients = []
    allowed = allowed_recipients(request.app.state.settings, channel)
    if allowed is not None and set(recipients) - allowed:
        raise ApiError(422, "RECIPIENT_NOT_ALLOWED", "Получатель не входит в разрешённый список сервера.", field="recipients")

    if channel == "telegram":
        added = set(recipients) - set(row.recipients if row else ())
        if added and added != verified_telegram_recipients(db, user.id, added):
            raise ApiError(422, "TELEGRAM_DESTINATION_UNVERIFIED", "Сначала подтвердите новый чат в своём профиле.", field="recipients")
    previous_secret = row.secret if row else None
    if row is None:
        row = WorkspaceDeliverySetting(workspace_id=workspace_id, channel=channel)
        db.add(row)
    row.recipients = recipients
    if "credentials" in data.model_fields_set:
        credentials = data.credentials
        if credentials is None:
            row.username = row.secret = None
        else:
            username = credentials.username.get_secret_value() if credentials.username else None
            secret = credentials.secret.get_secret_value()
            if channel == "email":
                valid = bool(username and username.strip())
            elif channel == "telegram":
                valid = username is None and re.fullmatch(r"[0-9]{1,20}:[A-Za-z0-9_-]{1,128}", secret) is not None
            else:
                valid = username is None and SLACK_WEBHOOK.fullmatch(secret) is not None
            if not valid or credentials.passphrase is not None:
                raise ApiError(422, "INVALID_DELIVERY_CREDENTIALS", "Проверьте реквизиты выбранного канала.", field="credentials")
            row.username, row.secret = username, secret
    if channel != "slack" or version == 0 or row.secret != previous_secret:
        row.version = version + 1
    db.commit()
    return _read(row)
