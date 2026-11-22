"""Durable per-recipient delivery and independent Telegram message/attachment phases."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Uuid, any_, bindparam, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session

from ..config import Settings
from ..db.encryption import require_cipher
from ..db.models import NodeRun, NotificationBatch, NotificationDelivery, Project, ScanRun
from .base import Attachment, DeliveryError, Message
from .destinations import verified_telegram_recipients
from .email import ADDRESS
from .telegram import CHAT_ID


def recipient_key(channel: str, address: str) -> str:
    address = address.strip()
    if channel == "slack":
        return address
    if channel == "telegram":
        return address.lower() if address.startswith("@") else str(int(address))
    local, _, domain = address.rpartition("@")
    return f"{local}@{domain.lower()}"


def allowed_recipients(settings: Settings, channel: str) -> frozenset[str] | None:
    if channel == "slack":
        return None
    values = settings.notify_email_recipients if channel == "email" else settings.notify_telegram_recipients
    if channel == "telegram" and not values:
        return None
    pattern = ADDRESS if channel == "email" else CHAT_ID
    return frozenset(recipient_key(channel, address) for address in values if pattern.fullmatch(address.strip()))


@dataclass(frozen=True)
class NotificationDraft:
    channel: str
    recipients: tuple[str, ...]
    allowed: frozenset[str] | None
    message: Message
    settings_workspace_id: UUID | None = None
    settings_version: int | None = None


@dataclass(frozen=True)
class ClaimedDelivery:
    id: UUID
    run_id: UUID
    node_run_id: UUID
    token: UUID
    channel: str
    recipient: str
    phase: str
    message: Message
    project_id: UUID | None = None
    credential_id: UUID | None = None
    owner_id: UUID | None = None
    workspace_id: UUID | None = None
    settings_workspace_id: UUID | None = None
    settings_version: int | None = None


@dataclass(frozen=True)
class RecoveredDelivery:
    id: UUID
    run_id: UUID
    node_run_id: UUID
    status: str


def enqueue_notification(db: Session, node: NodeRun, draft: NotificationDraft, owner_id: UUID) -> list[tuple[UUID, str]]:
    attachment = next(iter(draft.message.attachments), None)
    batch = NotificationBatch(
        node_run_id=node.id, channel=draft.channel, subject=draft.message.subject, body=draft.message.text,
        settings_workspace_id=draft.settings_workspace_id,
        settings_version=draft.settings_version,
        attachment_name=attachment.filename if attachment else None,
        attachment_content=attachment.content if attachment else None,
    )
    db.add(batch)
    db.flush()
    cipher = require_cipher(db.get_bind().dialect)
    recipients = tuple(dict.fromkeys(recipient_key(draft.channel, value) for value in draft.recipients))
    verified = verified_telegram_recipients(db, owner_id, recipients) if (
        draft.channel == "telegram" and draft.settings_workspace_id is None
    ) else None
    deliveries = []
    for recipient in recipients:
        delivery = NotificationDelivery(
            id=uuid4(), batch_id=batch.id, recipient=recipient, recipient_key=cipher.digest(f"delivery:{batch.id}", recipient),
            status="queued",
        )
        if verified is not None and recipient not in verified:
            delivery.status = "failed"
            delivery.error_code = "TELEGRAM_DESTINATION_UNVERIFIED"
            delivery.error_message = "Подтвердите этот Telegram-чат в профиле владельца проекта."
        elif draft.allowed is not None and recipient not in draft.allowed:
            delivery.status = "failed"
            delivery.error_code = "RECIPIENT_NOT_ALLOWED"
            delivery.error_message = "Получатель не входит в разрешённый список сервера."
        db.add(delivery)
        deliveries.append((delivery.id, delivery.status))
    return deliveries


def expire_deliveries(db: Session, now: datetime) -> list[RecoveredDelivery]:
    expired = db.execute(update(NotificationDelivery).where(
        NotificationDelivery.status == "sending", NotificationDelivery.lease_until <= now,
    ).values(
        status="unknown", lease_until=None, claim_token=None, updated_at=now,
        error_code="DELIVERY_OUTCOME_UNKNOWN",
        error_message="Процесс отправки завершился без подтверждения от провайдера.",
    ).returning(NotificationDelivery.id, NotificationDelivery.batch_id, NotificationDelivery.status)).all()
    failed_batches = (
        select(NotificationBatch.id).join(NodeRun, NotificationBatch.node_run_id == NodeRun.id)
        .join(ScanRun, NodeRun.run_id == ScanRun.id).where(ScanRun.status == "failed")
    )
    cancelled = db.execute(update(NotificationDelivery).where(
        NotificationDelivery.status == "queued", NotificationDelivery.batch_id.in_(failed_batches),
    ).values(
        status="cancelled", updated_at=now, error_code="REPORT_UNAVAILABLE",
        error_message="Запуск завершился без готового отчёта.",
    ).returning(NotificationDelivery.id, NotificationDelivery.batch_id, NotificationDelivery.status)).all()
    changed = expired + cancelled
    if not changed:
        return []
    batch_ids = bindparam("recovered_batch_ids", list({batch_id for _, batch_id, _ in changed}), type_=ARRAY(Uuid()))
    batches = {
        batch_id: (run_id, node_run_id)
        for batch_id, run_id, node_run_id in db.execute(
            select(NotificationBatch.id, NodeRun.run_id, NodeRun.id)
            .join(NodeRun, NotificationBatch.node_run_id == NodeRun.id)
            .where(NotificationBatch.id == any_(batch_ids))
        )
    }
    return [RecoveredDelivery(delivery_id, *batches[batch_id], status) for delivery_id, batch_id, status in changed]


def claim_delivery(db: Session, now: datetime, *, lease_seconds: float) -> ClaimedDelivery | None:
    result = db.execute(
        select(NotificationDelivery, NotificationBatch, NodeRun.run_id, ScanRun.project_id,
               ScanRun.graph_snapshot, NodeRun.node_id, Project.owner_id, Project.workspace_id)
        .join(NotificationBatch, NotificationDelivery.batch_id == NotificationBatch.id)
        .join(NodeRun, NotificationBatch.node_run_id == NodeRun.id)
        .join(ScanRun, NodeRun.run_id == ScanRun.id)
        .join(Project, Project.id == ScanRun.project_id)
        .where(
            NotificationDelivery.status == "queued", NotificationDelivery.next_attempt_at <= now,
            ScanRun.status.in_(("succeeded", "partial")), Project.owner_id.is_not(None),
        )
        .order_by(NotificationDelivery.next_attempt_at, NotificationDelivery.id).limit(1)
        .with_for_update(of=NotificationDelivery, skip_locked=True)
    ).first()
    if result is None:
        return None
    delivery, batch, run_id, project_id, graph, node_id, owner_id, workspace_id = result
    config = next((node.get("config", {}) for node in graph["nodes"] if node["id"] == node_id), {})
    credential_id = config.get("credential_id") if batch.settings_workspace_id is None else None
    token = uuid4()
    delivery.status = "sending"
    delivery.attempts += 1
    delivery.updated_at = now
    delivery.lease_until = now + timedelta(seconds=lease_seconds)
    delivery.claim_token = token
    attachment = ()
    if batch.attachment_name is not None and batch.attachment_content is not None:
        attachment = (Attachment(batch.attachment_name, batch.attachment_content, "application/json"),)
    return ClaimedDelivery(
        delivery.id, run_id, batch.node_run_id, token, batch.channel, delivery.recipient, delivery.phase,
        Message(batch.subject, batch.body, attachment),
        project_id, UUID(credential_id) if credential_id is not None else None, owner_id, workspace_id,
        batch.settings_workspace_id, batch.settings_version,
    )


def finish_delivery(
    db: Session,
    claim: ClaimedDelivery,
    *,
    provider_id: str | None = None,
    error: DeliveryError | None = None,
    max_attempts: int,
) -> str | None:
    delivery = db.scalar(select(NotificationDelivery).where(
        NotificationDelivery.id == claim.id, NotificationDelivery.status == "sending",
        NotificationDelivery.claim_token == claim.token,
    ).with_for_update())
    if delivery is None:
        return None
    now = datetime.now(UTC)
    delivery.claim_token = None
    delivery.lease_until = None
    delivery.updated_at = now
    if error:
        delivery.error_code = error.code
        delivery.error_message = error.message
        if error.outcome == "retry" and delivery.attempts < max_attempts:
            delay = max(15 * (2 ** (delivery.attempts - 1)), error.retry_after or 0)
            delivery.next_attempt_at = now + timedelta(seconds=delay)
            delivery.status = "queued"
        else:
            delivery.status = "unknown" if error.outcome == "unknown" else "failed"
        return delivery.status

    delivery.error_code = delivery.error_message = None
    if claim.phase == "attachment":
        delivery.attachment_id = provider_id
        delivery.status = "accepted"
    else:
        delivery.message_id = provider_id
        if claim.channel == "telegram" and claim.message.attachments:
            delivery.phase = "attachment"
            delivery.attempts = 0
            delivery.next_attempt_at = now
            delivery.status = "queued"
        else:
            delivery.status = "accepted"
    return delivery.status


def _mask(channel: str, recipient: str) -> str:
    if channel == "slack":
        return "Канал Slack"
    if channel == "email":
        local, _, domain = recipient.rpartition("@")
        return f"{local[:1]}***@{domain}"
    return f"…{recipient[-4:]}" if len(recipient) > 4 else "****"


def delivery_summary(db: Session, run_id: UUID) -> dict[str, dict]:
    rows = db.execute(
        select(NodeRun.node_id, NotificationBatch.channel, NotificationDelivery)
        .join(NotificationBatch, NodeRun.id == NotificationBatch.node_run_id)
        .join(NotificationDelivery, NotificationBatch.id == NotificationDelivery.batch_id)
        .where(NodeRun.run_id == run_id).order_by(NodeRun.node_id, NotificationDelivery.id)
    )
    summaries: dict[str, dict] = {}
    for node_id, channel, row in rows:
        item = summaries.setdefault(node_id, {"channel": channel, "status": "queued", "recipients": []})
        item["recipients"].append({
            "id": str(row.id), "recipient": _mask(channel, row.recipient), "status": row.status,
            "phase": row.phase, "attempts": row.attempts, "message_id": row.message_id,
            "attachment_id": row.attachment_id, "error_code": row.error_code, "error": row.error_message,
        })
    for item in summaries.values():
        states = {row["status"] for row in item["recipients"]}
        if "sending" in states:
            item["status"] = "sending"
        elif "queued" in states:
            item["status"] = "queued"
        elif len(states) == 1 and not (
            states & {"failed", "unknown"} and any(row["phase"] == "attachment" for row in item["recipients"])
        ):
            item["status"] = states.pop()
        else:
            item["status"] = "partial"
    return summaries
