from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL


def create_db_engine(url: str) -> Engine:
    """Engine factory shared by the app and the test fixtures, so both run the same pragmas.

    check_same_thread=False allows the threadpooled get_db and the event loop to share
    connections; the pragmas below do the actual hardening.
    """
    engine = create_engine(url, connect_args={"check_same_thread": False}, echo=False)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            # WAL lets readers and the writer coexist; without it a request holding a read
            # transaction blocks every writer (requests and cron scripts) file-wide.
            cursor.execute("PRAGMA journal_mode=WAL")
            # SQLite does not enforce foreign keys unless asked, per connection.
            cursor.execute("PRAGMA foreign_keys=ON")
            # Retry instead of raising "database is locked" when another process writes.
            cursor.execute("PRAGMA busy_timeout=5000")
            # Safe with WAL: the log is synced, checkpoints are batched.
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine


engine = create_db_engine(SQLALCHEMY_DATABASE_URL)

# expire_on_commit=False so releasing a transaction early (see the commit-before-network-await
# pattern in the services) does not turn every loaded attribute into a fresh SELECT.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
