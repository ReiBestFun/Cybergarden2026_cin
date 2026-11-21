"""Durable per-recipient delivery and independent Telegram message/attachment phases."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import Settings
from ..db.models import NodeRun, NotificationBatch, NotificationDelivery, ScanRun
from .base import Attachment, DeliveryError, Message
from .email import ADDRESS
from .telegram import CHAT_ID


def _recipient_key(channel: str, address: str) -> str:
    address = address.strip()
    if channel == "telegram":
        return address.lower() if address.startswith("@") else str(int(address))
    local, _, domain = address.rpartition("@")
    return f"{local}@{domain.lower()}"


def allowed_recipients(settings: Settings, channel: str) -> frozenset[str]:
    values = settings.notify_email_recipients if channel == "email" else settings.notify_telegram_recipients
    pattern = ADDRESS if channel == "email" else CHAT_ID
    return frozenset(_recipient_key(channel, address) for address in values if pattern.fullmatch(address.strip()))


@dataclass(frozen=True)
class NotificationDraft:
    channel: str
    recipients: tuple[str, ...]
    allowed: frozenset[str]
    message: Message


@dataclass(frozen=True)
class ClaimedDelivery:
    id: UUID
    token: UUID
    channel: str
    recipient: str
    phase: str
    message: Message
    project_id: UUID | None = None
    credential_id: UUID | None = None


def enqueue_notification(db: Session, node: NodeRun, draft: NotificationDraft) -> None:
    attachment = next(iter(draft.message.attachments), None)
    batch = NotificationBatch(
        node_run_id=node.id, channel=draft.channel, subject=draft.message.subject, body=draft.message.text,
        attachment_name=attachment.filename if attachment else None,
        attachment_content=attachment.content if attachment else None,
    )
    db.add(batch)
    db.flush()
    for recipient in dict.fromkeys(_recipient_key(draft.channel, value) for value in draft.recipients):
        delivery = NotificationDelivery(batch_id=batch.id, recipient=recipient)
        if recipient not in draft.allowed:
            delivery.status = "failed"
            delivery.error_code = "RECIPIENT_NOT_ALLOWED"
            delivery.error_message = "Получатель не входит в разрешённый список сервера."
        db.add(delivery)


def expire_deliveries(db: Session, now: datetime) -> None:
    db.execute(update(NotificationDelivery).where(
        NotificationDelivery.status == "sending", NotificationDelivery.lease_until <= now,
    ).values(
        status="unknown", lease_until=None, claim_token=None, updated_at=now,
        error_code="DELIVERY_OUTCOME_UNKNOWN",
        error_message="Процесс отправки завершился без подтверждения от провайдера.",
    ))
    failed_batches = (
        select(NotificationBatch.id).join(NodeRun, NotificationBatch.node_run_id == NodeRun.id)
        .join(ScanRun, NodeRun.run_id == ScanRun.id).where(ScanRun.status == "failed")
    )
    db.execute(update(NotificationDelivery).where(
        NotificationDelivery.status == "queued", NotificationDelivery.batch_id.in_(failed_batches),
    ).values(
        status="cancelled", updated_at=now, error_code="REPORT_UNAVAILABLE",
        error_message="Запуск завершился без готового отчёта.",
    ))


def claim_delivery(db: Session, now: datetime, *, lease_seconds: float) -> ClaimedDelivery | None:
    result = db.execute(
        select(NotificationDelivery, NotificationBatch, ScanRun.project_id, ScanRun.graph_snapshot, NodeRun.node_id)
        .join(NotificationBatch, NotificationDelivery.batch_id == NotificationBatch.id)
        .join(NodeRun, NotificationBatch.node_run_id == NodeRun.id)
        .join(ScanRun, NodeRun.run_id == ScanRun.id)
        .where(
            NotificationDelivery.status == "queued", NotificationDelivery.next_attempt_at <= now,
            ScanRun.status.in_(("succeeded", "partial")),
        )
        .order_by(NotificationDelivery.next_attempt_at, NotificationDelivery.id).limit(1)
        .with_for_update(of=NotificationDelivery, skip_locked=True)
    ).first()
    if result is None:
        return None
    delivery, batch, project_id, graph, node_id = result
    config = next((node.get("config", {}) for node in graph["nodes"] if node["id"] == node_id), {})
    credential_id = config.get("credential_id")
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
        delivery.id, token, batch.channel, delivery.recipient, delivery.phase,
        Message(batch.subject, batch.body, attachment),
        project_id, UUID(credential_id) if credential_id is not None else None,
    )


def finish_delivery(
    db: Session,
    claim: ClaimedDelivery,
    *,
    provider_id: str | None = None,
    error: DeliveryError | None = None,
    max_attempts: int,
) -> bool:
    delivery = db.scalar(select(NotificationDelivery).where(
        NotificationDelivery.id == claim.id, NotificationDelivery.status == "sending",
        NotificationDelivery.claim_token == claim.token,
    ).with_for_update())
    if delivery is None:
        return False
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
        return True

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
    return True


def _mask(channel: str, recipient: str) -> str:
    if channel == "email":
        local, _, domain = recipient.rpartition("@")
        return f"{local[:1]}***@{domain}"
    return f"…{recipient[-4:]}" if len(recipient) > 4 else "****"


def delivery_summary(db: Session, run_id: UUID) -> dict[str, dict]:
    rows = db.execute(
        select(NodeRun.node_id, NotificationBatch.channel, NotificationDelivery)
        .join(NotificationBatch, NodeRun.id == NotificationBatch.node_run_id)
        .join(NotificationDelivery, NotificationBatch.id == NotificationDelivery.batch_id)
        .where(NodeRun.run_id == run_id).order_by(NodeRun.node_id, NotificationDelivery.recipient)
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
