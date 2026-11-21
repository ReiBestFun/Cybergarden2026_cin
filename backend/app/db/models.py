from datetime import datetime, time as local_time
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String,
    Text, Time, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120))
    repository_url: Mapped[str] = mapped_column(Text)
    default_ref: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Workflow(Base):
    __tablename__ = "workflows"
    __table_args__ = (Index("ix_workflows_project_updated", "project_id", "updated_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(120))
    graph_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


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
    time: Mapped[local_time | None] = mapped_column(Time)
    weekdays: Mapped[list[int] | None] = mapped_column(JSONB(none_as_null=True))
    day_of_month: Mapped[int | None] = mapped_column(Integer)
    cron: Mapped[str | None] = mapped_column(String(120))
    timezone: Mapped[str | None] = mapped_column(String(128))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))


class ScanRun(Base):
    __tablename__ = "scan_runs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'partial', 'failed')", name="ck_run_status"),
        CheckConstraint("trigger IN ('manual', 'schedule')", name="ck_run_trigger"),
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
    graph_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    schedule_id: Mapped[UUID | None] = mapped_column(ForeignKey("schedules.id"))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[UUID | None]
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))


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
    input_preview: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    output_preview: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))


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
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    remediation: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    evidence: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, str]] = mapped_column("metadata", JSONB)
