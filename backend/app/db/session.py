from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from ..api.common import ApiError
from ..workspace_logs import rejected_action
from .encryption import StorageCipher


def make_engine(database_url: str, encryption_key: SecretStr | None = None) -> Engine:
    cipher = StorageCipher(encryption_key.get_secret_value()) if encryption_key and encryption_key.get_secret_value() else None
    engine = create_engine(
        database_url, pool_pre_ping=True, hide_parameters=True,
        connect_args={"options": "-c timezone=UTC", "connect_timeout": 5},
    )
    engine.dialect.cyberflow_cipher = cipher
    return engine


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        try:
            yield session
        except ApiError as error:
            rejected_action(session, error.status)
            raise


Database = Annotated[Session, Depends(get_session)]
