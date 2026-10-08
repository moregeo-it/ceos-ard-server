"""
Add the columns that newer models expect to tables an existing database already has.

`Base.metadata.create_all` (app/main.py) creates missing tables but never alters existing ones, so a
column added to a model after a database was created has to be added here. Safe to run repeatedly:
columns that already exist are skipped.

Usage:
    python scripts/migrate_schema.py
"""

import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# (table, column, SQL type), in the order they were introduced
COLUMNS = [
    ("users", "last_seen_at", "DATETIME"),
]


def migrate(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    added = 0
    try:
        for table, column, sql_type in COLUMNS:
            existing = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
            if not existing:
                logger.info(f"{table}: table does not exist yet, the server creates it at startup")
                continue
            if column in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
            logger.info(f"{table}: added column {column}")
            added += 1
        conn.commit()
    finally:
        conn.close()
    return added


if __name__ == "__main__":
    url = settings.DATABASE_URL
    if not url.startswith("sqlite:///"):
        logger.error(f"Only SQLite databases are supported, got {url}")
        sys.exit(1)
    path = url.removeprefix("sqlite:///")
    added = migrate(path)
    logger.info(f"Done: {added} column(s) added to {path}" if added else f"Done: {path} is up to date")
