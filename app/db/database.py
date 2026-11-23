from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL
# PostgreSQL ends a transaction left open this long without a statement, so a forgotten commit can't hold
# a connection and its row locks indefinitely. Above the longest intended gap: the cleanup script waits
# up to a minute for a workspace lock inside its transaction
IDLE_TRANSACTION_TIMEOUT = "5min"


def create_db_engine(url: str) -> Engine:
    """Engine factory shared by the app and the test fixtures.

    pool_pre_ping replaces connections the database closed, e.g. after a restart.
    """
    return create_engine(
        url, pool_pre_ping=True, echo=False, connect_args={"options": f"-c idle_in_transaction_session_timeout={IDLE_TRANSACTION_TIMEOUT}"}
    )


engine = create_db_engine(SQLALCHEMY_DATABASE_URL)

# expire_on_commit=False so releasing a transaction early (see the commit-before-network-await
# pattern in the services) does not turn every loaded attribute into a fresh SELECT.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)

# The same constraint names on every database, so migrations can refer to them
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Base = declarative_base(metadata=MetaData(naming_convention=NAMING_CONVENTION))


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
