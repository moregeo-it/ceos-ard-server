from logging.config import fileConfig

from alembic import context
from sqlalchemy.types import TypeDecorator

import app.models  # noqa: F401  # registers every table on Base.metadata
from app.config import settings
from app.db.database import Base, create_db_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    # App types render as their database type, so revisions never import app code
    if type_ == "type" and isinstance(obj, TypeDecorator):
        return f"sa.{obj.impl_instance!r}"
    return False


def run_migrations_offline() -> None:
    context.configure(url=settings.DATABASE_URL, target_metadata=target_metadata, literal_binds=True, render_item=render_item)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_db_engine(settings.DATABASE_URL)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_item=render_item)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
