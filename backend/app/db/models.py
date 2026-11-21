from datetime import datetime, time as local_time
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..analyzers.models import FindingVerification, VerificationStatus
from .encryption import EncryptedBinary, EncryptedJSON, EncryptedText, EncryptedTime


class Base(DeclarativeBase):
    pass


class StorageMetadata(Base):
    __tablename__ = "storage_metadata"
    __table_args__ = (CheckConstraint("id = 1", name="ck_storage_metadata_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    key_check: Mapped[str] = mapped_column(String(64))


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    github_key: Mapped[str] = mapped_column(String(64), unique=True)
    github_login: Mapped[str] = mapped_column(EncryptedText("users.github_login"))
    display_name: Mapped[str] = mapped_column(EncryptedText("users.display_name"))
    avatar_url: Mapped[str | None] = mapped_column(EncryptedText("users.avatar_url"))
    timezone: Mapped[str] = mapped_column(EncryptedText("users.timezone"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSession(Base):
    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class OAuthAttempt(Base):
    __tablename__ = "oauth_attempts"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    verifier: Mapped[str] = mapped_column(EncryptedText("oauth_attempts.verifier"))
    return_path: Mapped[str] = mapped_column(EncryptedText("oauth_attempts.return_path"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint("personal_user_id IS NULL OR personal_user_id = id", name="ck_workspace_personal_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(EncryptedText("workspaces.name"))
    personal_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), unique=True)

    @property
    def personal(self) -> bool:
        return self.personal_user_id is not None


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (CheckConstraint("role IN ('owner', 'member')", name="ck_workspace_membership_role"),)

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(16))


class WorkspaceInvitation(Base):
    __tablename__ = "workspace_invitations"
    __table_args__ = (CheckConstraint("role IN ('owner', 'member')", name="ck_workspace_invitation_role"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    issuer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("workspace_id", "repository_key", name="uq_project_workspace_repository"),
        CheckConstraint("(owner_id IS NULL) = (repository_key IS NULL)", name="ck_project_owner_key"),
        CheckConstraint("(workspace_id IS NULL) = (owner_id IS NULL)", name="ck_project_workspace_owner"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[UUID | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    repository_key: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(EncryptedText("projects.name"))
    repository_url: Mapped[str] = mapped_column(EncryptedText("projects.repository_url"))
    default_ref: Mapped[str] = mapped_column(EncryptedText("projects.default_ref"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkflowTemplate(Base):
    __tablename__ = "workflow_templates"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    draft_source_workspace_id: Mapped[UUID | None] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(EncryptedText("workflow_templates.name"))
    description: Mapped[str] = mapped_column(EncryptedText("workflow_templates.description"))
    graph_json: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON("workflow_templates.graph_json"))
    schedule: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON("workflow_templates.schedule"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def can_edit(self) -> bool:
        return self.owner_id is not None


class TelegramDestination(Base):
    __tablename__ = "telegram_destinations"
    __table_args__ = (UniqueConstraint("owner_id", "chat_key", name="uq_telegram_destination_owner_chat"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chat_key: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[str] = mapped_column(EncryptedText("telegram_destinations.chat_id"))
    label: Mapped[str] = mapped_column(EncryptedText("telegram_destinations.label"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    challenge_hash: Mapped[str | None] = mapped_column(String(64))
    challenge_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    challenge_attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class TelegramLink(Base):
    __tablename__ = "telegram_links"

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[UUID] = mapped_column(unique=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    group_chat: Mapped[bool] = mapped_column(Boolean)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    destination_id: Mapped[UUID | None] = mapped_column(ForeignKey("telegram_destinations.id", ondelete="SET NULL"))


class TelegramChallengeLimit(Base):
    __tablename__ = "telegram_challenge_limits"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Workflow(Base):
    __tablename__ = "workflows"
    __table_args__ = (Index("ix_workflows_project_updated", "project_id", "updated_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(EncryptedText("workflows.name"))
    graph_json: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON("workflows.graph_json"))
    ci_token_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Credential(Base):
    __tablename__ = "credentials"
    __table_args__ = (Index("ix_credentials_project", "project_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(24))
    ciphertext: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        Index("ix_schedules_due", "next_run_at", postgresql_where=text("enabled")),
        CheckConstraint("frequency IN ('daily', 'weekly', 'monthly', 'cron')", name="ck_schedule_frequency"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(ForeignKey("workflows.id"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    frequency: Mapped[str] = mapped_column(String(16), server_default=text("'daily'"))
    time: Mapped[local_time | None] = mapped_column(EncryptedTime("schedules.time"))
    weekdays: Mapped[list[int] | None] = mapped_column(EncryptedJSON("schedules.weekdays"))
    day_of_month: Mapped[int | None] = mapped_column(EncryptedJSON("schedules.day_of_month"))
    cron: Mapped[str | None] = mapped_column(EncryptedText("schedules.cron"))
    timezone: Mapped[str | None] = mapped_column(EncryptedText("schedules.timezone"))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON("schedules.last_error"))


class ScanRun(Base):
    __tablename__ = "scan_runs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'partial', 'failed')", name="ck_run_status"),
        CheckConstraint("trigger IN ('manual', 'schedule', 'ci')", name="ck_run_trigger"),
        UniqueConstraint("workflow_id", "ci_request_id", name="uq_run_ci_request"),
        UniqueConstraint("schedule_id", "scheduled_for", name="uq_run_schedule_slot"),
        Index("ix_scan_runs_queue", "status", "created_at"),
        Index("ix_scan_runs_project_history", "project_id", "created_at"),
        Index("ix_scan_runs_workflow", "workflow_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    workflow_id: Mapped[UUID] = mapped_column(ForeignKey("workflows.id"))
    trigger: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'queued'"))
    graph_snapshot: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON("scan_runs.graph_snapshot"))
    source_snapshot: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON("scan_runs.source_snapshot"))
    commit_sha: Mapped[str | None] = mapped_column(EncryptedText("scan_runs.commit_sha"))
    ci_request_id: Mapped[str | None] = mapped_column(String(100))
    schedule_id: Mapped[UUID | None] = mapped_column(ForeignKey("schedules.id"))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[UUID | None]
    summary: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON("scan_runs.summary"))
    report: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON("scan_runs.report"))
    error: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON("scan_runs.error"))


class NodeRun(Base):
    __tablename__ = "node_runs"
    __table_args__ = (
        UniqueConstraint("run_id", "node_id", name="uq_node_run_node"),
        CheckConstraint("status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')", name="ck_node_run_status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("scan_runs.id"))
    node_id: Mapped[str] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_output_ports: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    input_preview: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON("node_runs.input_preview"))
    output_preview: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON("node_runs.output_preview"))
    error: Mapped[dict[str, Any] | None] = mapped_column(EncryptedJSON("node_runs.error"))


class FindingRow(Base):
    __tablename__ = "findings"
    __table_args__ = (
        CheckConstraint("category IN ('secrets', 'sca', 'sast')", name="ck_finding_category"),
        CheckConstraint("severity IN ('info', 'low', 'medium', 'high', 'critical')", name="ck_finding_severity"),
        CheckConstraint("category <> 'secrets' OR evidence = '***'", name="ck_finding_mask"),
        CheckConstraint("line IS NULL OR line >= 1", name="ck_finding_line"),
        CheckConstraint("end_line IS NULL OR (line IS NOT NULL AND end_line >= line)", name="ck_finding_end_line"),
        Index("ix_findings_run_category_severity", "run_id", "category", "severity"),
        Index("ix_findings_node_run", "node_run_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("scan_runs.id"))
    node_run_id: Mapped[UUID] = mapped_column(ForeignKey("node_runs.id"))
    rule_id: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(EncryptedText("findings.title"))
    description: Mapped[str] = mapped_column(EncryptedText("findings.description"))
    remediation: Mapped[str] = mapped_column(EncryptedText("findings.remediation"))
    path: Mapped[str] = mapped_column(EncryptedText("findings.path"))
    line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    evidence: Mapped[str] = mapped_column(EncryptedText("findings.evidence", preserve_mask=True))
    details: Mapped[dict[str, str | dict[str, str]]] = mapped_column("metadata", EncryptedJSON("findings.metadata"))

    @property
    def finding_metadata(self) -> dict[str, str]:
        return {key: value for key, value in self.details.items() if key != "verification" and isinstance(value, str)}

    @property
    def verification(self) -> FindingVerification | None:
        data = self.details.get("verification")
        if not isinstance(data, dict):
            return None
        return FindingVerification(cast(VerificationStatus, data["status"]), data["code"], data["message"])


class NotificationBatch(Base):
    __tablename__ = "notification_batches"
    __table_args__ = (
        CheckConstraint("channel IN ('email', 'telegram')", name="ck_notification_channel"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    node_run_id: Mapped[UUID] = mapped_column(ForeignKey("node_runs.id"), unique=True)
    channel: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(EncryptedText("notification_batches.subject"))
    body: Mapped[str] = mapped_column(EncryptedText("notification_batches.body"))
    attachment_name: Mapped[str | None] = mapped_column(EncryptedText("notification_batches.attachment_name"))
    attachment_content: Mapped[bytes | None] = mapped_column(EncryptedBinary("notification_batches.attachment_content"))


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("batch_id", "recipient_key", name="uq_notification_recipient"),
        CheckConstraint(
            "status IN ('queued', 'sending', 'accepted', 'failed', 'unknown', 'cancelled')",
            name="ck_notification_status",
        ),
        CheckConstraint("phase IN ('message', 'attachment')", name="ck_notification_phase"),
        Index("ix_notification_queue", "status", "next_attempt_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("notification_batches.id"))
    recipient: Mapped[str] = mapped_column(EncryptedText("notification_deliveries.recipient"))
    recipient_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'queued'"))
    phase: Mapped[str] = mapped_column(String(16), server_default=text("'message'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[UUID | None]
    message_id: Mapped[str | None] = mapped_column(EncryptedText("notification_deliveries.message_id"))
    attachment_id: Mapped[str | None] = mapped_column(EncryptedText("notification_deliveries.attachment_id"))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(EncryptedText("notification_deliveries.error_message"))
