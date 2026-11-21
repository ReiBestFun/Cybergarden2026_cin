"""Assign explicitly selected legacy projects to an existing GitHub profile."""

import argparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings
from ..db.encryption import check_storage_key, require_cipher
from ..db.models import Project, User
from ..db.session import make_engine


def claim_projects(db: Session, owner_id: UUID, project_ids: set[UUID]) -> int:
    check_storage_key(db.connection())
    if db.scalar(select(User.id).where(User.id == owner_id)) is None:
        raise ValueError("Профиль не найден. Пользователь должен сначала войти через GitHub.")
    projects = db.scalars(select(Project).where(
        Project.id.in_(project_ids), Project.owner_id.is_(None),
    ).order_by(Project.id).with_for_update()).all()
    if len(projects) != len(project_ids):
        raise ValueError("Один из указанных проектов не найден или уже назначен владельцу.")
    cipher = require_cipher(db.get_bind().dialect)
    for project in projects:
        project.owner_id = owner_id
        project.repository_key = cipher.digest(f"project:{owner_id}", project.repository_url.casefold())
    db.flush()
    return len(projects)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="Показать проекты без владельца.")
    parser.add_argument("--user-id", type=UUID)
    parser.add_argument("--project-id", type=UUID, action="append")
    args = parser.parse_args()
    if args.list and (args.user_id or args.project_id):
        parser.error("Для списка укажите только --list.")
    if not args.list and (not args.user_id or not args.project_id):
        parser.error("Укажите --user-id и хотя бы один --project-id.")
    settings = Settings()
    engine = make_engine(settings.database_url, settings.data_encryption_key)
    try:
        with Session(engine) as db, db.begin():
            if args.list:
                check_storage_key(db.connection())
                rows = db.scalars(select(Project).where(Project.owner_id.is_(None)).order_by(Project.created_at))
                for project in rows:
                    print(f"{project.id}  {project.repository_url}")
                return 0
            count = claim_projects(db, args.user_id, set(args.project_id))
    except (RuntimeError, ValueError) as error:
        parser.error(str(error))
    except IntegrityError:
        parser.error("У владельца уже есть такой репозиторий. Укажите проекты без совпадающих URL.")
    finally:
        engine.dispose()
    print(f"Назначено проектов: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
