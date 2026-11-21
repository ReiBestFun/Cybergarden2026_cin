"""Structured diagnostic events shared by the API and workers."""

import json
import logging
from datetime import UTC, datetime
from uuid import UUID


_logger = logging.getLogger("cyberflow.events")
_logger.setLevel(logging.INFO)
_logger.propagate = False
if not _logger.handlers:
    _logger.addHandler(logging.StreamHandler())


def log_event(
    component: str,
    action: str,
    result: str,
    *,
    level: int = logging.INFO,
    request_id: UUID | None = None,
    run_id: UUID | None = None,
    node_run_id: UUID | None = None,
    node_type: str | None = None,
    delivery_id: UUID | None = None,
    http_status: int | None = None,
    count: int | None = None,
    scanner: str | None = None,
    exit_code: int | None = None,
    phase: str | None = None,
) -> None:
    event: dict[str, str | int] = {
        "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "level": logging.getLevelName(level),
        "component": component,
        "action": action,
        "result": result,
    }
    identifiers = {"request_id": request_id, "run_id": run_id, "node_run_id": node_run_id, "delivery_id": delivery_id}
    fields = {"http_status": http_status, "count": count, "node_type": node_type,
              "scanner": scanner, "exit_code": exit_code, "phase": phase}
    event.update({key: str(value) for key, value in identifiers.items() if value is not None})
    event.update({key: value for key, value in fields.items() if value is not None})
    _logger.log(level, json.dumps(event, separators=(",", ":")))
