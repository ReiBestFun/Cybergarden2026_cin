from hashlib import sha256
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..analyzers.models import ScanError
from ..db.models import ProjectArchive
from ..db.session import Database
from ..integrations.archives import MAX_ARCHIVE_BYTES, archive_directory
from .auth import CurrentUser
from .common import ApiError
from .projects import get_project

router = APIRouter(tags=["projects"])


def _store_archive(db: Session, project_id: UUID, user_id: UUID, content: bytes) -> str:
    try:
        with archive_directory(content):
            pass
    except ScanError as error:
        raise ApiError(422, error.warning.code, error.warning.message) from None
    digest = sha256(content).hexdigest()
    get_project(project_id, db, user_id, lock=True, owner_required=True)
    db.execute(insert(ProjectArchive).values(project_id=project_id, sha256=digest, content=content)
               .on_conflict_do_nothing(index_elements=["project_id", "sha256"]))
    db.commit()
    return digest


@router.post("/projects/{project_id}/archives", status_code=201)
async def upload_archive(project_id: UUID, request: Request, db: Database, user: CurrentUser) -> dict[str, str]:
    user_id = user.id
    await run_in_threadpool(get_project, project_id, db, user_id, owner_required=True)
    await run_in_threadpool(db.rollback)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/zip":
        raise ApiError(415, "ZIP_REQUIRED", "Выберите файл в формате ZIP.")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_ARCHIVE_BYTES:
            raise ApiError(413, "ARCHIVE_TOO_LARGE", "ZIP должен быть не больше 8 МиБ.")
        data.extend(chunk)
    digest = await run_in_threadpool(_store_archive, db, project_id, user_id, bytes(data))
    return {"sha256": digest}
