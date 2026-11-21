from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from ..integrations.repository import canonical_repository_url
from ..schemas.contracts import Error, Issue


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, node_id: str | None = None, field: str | None = None):
        super().__init__(message)
        self.status = status
        self.error = Error(code=code, message=message, node_id=node_id, field=field)


class Pagination(BaseModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


PageQuery = Annotated[Pagination, Query()]


def page[T](db: Session, statement: Select[tuple[T]], limit: int, offset: int) -> dict:
    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    items = db.scalars(statement.limit(limit).offset(offset)).all()
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def graph_error(issue: Issue) -> ApiError:
    return ApiError(422, issue.code, issue.message, node_id=issue.node_id, field=issue.field)


def normalize_repository(url: str) -> str:
    try:
        return canonical_repository_url(url)
    except ValueError:
        raise ApiError(422, "INVALID_REPOSITORY", "Укажите HTTPS-ссылку на GitHub.", field="repository_url") from None
