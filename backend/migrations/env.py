from alembic import context

from backend.app.config import Settings
from backend.app.db.models import Base
from backend.app.db.session import make_engine


def run_migrations() -> None:
    settings = Settings()
    url = settings.database_url
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = make_engine(url, settings.data_encryption_key)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
