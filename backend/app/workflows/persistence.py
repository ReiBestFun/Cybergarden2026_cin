from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from ..analyzers.models import Finding, Warning
from ..db.models import FindingRow, NodeRun, Project, ScanRun
from ..notifications.queue import NotificationDraft, enqueue_notification
from ..schemas.contracts import NodeStatus, RunStatus
from .queue import ClaimedRun, fail_run


class ClaimLost(Exception):
    pass


def error_payload(warning: Warning | None) -> dict | None:
    return {key: value for key, value in asdict(warning).items() if value is not None} if warning else None


class RunProgress:
    def __init__(self, sessions: sessionmaker[Session], claim: ClaimedRun):
        self.sessions = sessions
        self.claim = claim

    def _owned(self):
        return (
            ScanRun.id == self.claim.id,
            ScanRun.status == "running",
            ScanRun.claim_token == self.claim.token,
        )

    def _lock(self, db: Session) -> ScanRun:
        run = db.scalar(select(ScanRun).where(*self._owned()).with_for_update())
        if run is None:
            raise ClaimLost
        return run

    def _node(self, db: Session, node_id: str) -> NodeRun:
        return db.scalars(select(NodeRun).where(
            NodeRun.run_id == self.claim.id, NodeRun.node_id == node_id,
        )).one()

    def heartbeat(self) -> bool:
        with self.sessions.begin() as db:
            result = db.execute(update(ScanRun).where(*self._owned()).values(heartbeat_at=datetime.now(UTC)))
            return result.rowcount == 1

    def start_node(self, node_id: str, inputs: dict) -> None:
        with self.sessions.begin() as db:
            run = self._lock(db)
            node = self._node(db, node_id)
            now = datetime.now(UTC)
            run.heartbeat_at = now
            node.status = "running"
            node.started_at = now
            node.input_preview = inputs

    def finish_node(
        self,
        node_id: str,
        status: NodeStatus,
        *,
        ports: list[str],
        preview: dict | None = None,
        warning: Warning | None = None,
        findings: tuple[Finding, ...] = (),
        sha: str | None = None,
        notification: NotificationDraft | None = None,
    ) -> None:
        with self.sessions.begin() as db:
            run = self._lock(db)
            node = self._node(db, node_id)
            now = datetime.now(UTC)
            run.heartbeat_at = now
            if sha is not None:
                run.commit_sha = sha
            node.status = status
            node.active_output_ports = ports
            node.output_preview = preview
            node.error = error_payload(warning)
            node.finished_at = now
            if notification is not None:
                owner_id = db.scalar(select(Project.owner_id).where(Project.id == run.project_id))
                if owner_id is None:
                    raise ClaimLost
                enqueue_notification(db, node, notification, owner_id)
            for finding in findings:
                details = dict(finding.metadata)
                if finding.verification is not None:
                    details["verification"] = asdict(finding.verification)
                db.add(FindingRow(
                    run_id=run.id, node_run_id=node.id, rule_id=finding.rule_id, category=finding.category,
                    severity=finding.severity, title=finding.title, description=finding.description,
                    remediation=finding.remediation, path=finding.path, line=finding.line,
                    end_line=finding.end_line, evidence=finding.evidence, details=details,
                ))

    def finish_run(
        self, status: RunStatus, summary: dict, report: dict, warning: Warning | None = None
    ) -> None:
        with self.sessions.begin() as db:
            run = self._lock(db)
            run.status = status
            run.summary = summary
            run.report = report
            run.error = error_payload(warning)
            run.finished_at = datetime.now(UTC)
            run.claim_token = None

    def fail(self, warning: Warning) -> None:
        with self.sessions.begin() as db:
            fail_run(db, self._lock(db), warning.code, warning.message)
