from pathlib import Path

from alembic import command
from alembic.config import Config


def test_migrations_match_the_models(repositories):
    # Raises if a model changed without a revision in migrations/versions
    command.check(Config(str(Path(__file__).parent.parent / "alembic.ini")))
