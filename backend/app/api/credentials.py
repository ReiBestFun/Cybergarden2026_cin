from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from sqlalchemy import select

from ..analyzers.models import ScanError
from ..credentials import encrypt_credential
from ..db.models import Credential
from ..db.session import Database
from ..workspace_logs import audit_action
from ..schemas.contracts import Page
from ..schemas.credentials import CredentialCreate, CredentialPatch, CredentialRead, CredentialValue
from .auth import CurrentUser
from .common import ApiError, PageQuery, page
from .projects import get_project
from .workspaces import require_workspace_owner

router = APIRouter(prefix="/projects/{project_id}/credentials", tags=["credentials"])


def _encrypt(request: Request, credential: Credential, value: CredentialValue) -> str:
    try:
        return encrypt_credential(request.app.state.settings, credential, value)
    except ScanError as error:
        warning = error.warning
        status = 503 if warning.code == "CREDENTIALS_KEY_UNAVAILABLE" else 422
        raise ApiError(status, warning.code, warning.message) from None


@router.get("", response_model=Page[CredentialRead])
def list_credentials(project_id: UUID, db: Database, pagination: PageQuery, user: CurrentUser) -> dict:
    get_project(project_id, db, user.id, owner_required=True)
    statement = select(Credential).where(Credential.project_id == project_id)
    return page(db, statement.order_by(Credential.name, Credential.id), pagination.limit, pagination.offset)


@router.post("", status_code=201, response_model=CredentialRead)
def create_credential(project_id: UUID, data: CredentialCreate, db: Database, request: Request, user: CurrentUser) -> Credential:
    project = get_project(project_id, db, user.id, lock=True)
    audit_action(db, project.workspace_id, user.id, "credential.create")
    require_workspace_owner(db, project.workspace_id, user.id)
    credential = Credential(id=uuid4(), project_id=project_id, name=data.name, kind=data.kind, active=True)
    credential.ciphertext = _encrypt(request, credential, data.value)
    db.add(credential)
    db.commit()
    return credential


@router.patch("/{credential_id}", response_model=CredentialRead)
def update_credential(
    project_id: UUID, credential_id: UUID, data: CredentialPatch, db: Database, request: Request, user: CurrentUser,
) -> Credential:
    project = get_project(project_id, db, user.id, lock=True)
    audit_action(db, project.workspace_id, user.id, "credential.update")
    require_workspace_owner(db, project.workspace_id, user.id)
    credential = db.scalar(select(Credential).where(
        Credential.id == credential_id, Credential.project_id == project_id,
    ).with_for_update())
    if credential is None:
        raise ApiError(404, "CREDENTIAL_NOT_FOUND", "Учётные данные не найдены.")
    if data.value is not None:
        credential.ciphertext = _encrypt(request, credential, data.value)
    if data.name is not None:
        credential.name = data.name
    if data.active is not None:
        credential.active = data.active
    db.commit()
    return credential
