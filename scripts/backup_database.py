"""
Back up the SQLite database that DATABASE_URL names, WAL-safe, into a backups folder.

Fails when the database is missing or has no tables, so a wrong path never produces an empty
backup. Only after a successful backup are backups older than `--keep-days` days removed.

Usage:
    python scripts/backup_database.py [--dir backups] [--keep-days 14]
"""

import logging
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.engine import make_url  # noqa: E402

from app.config import settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def backup_database(backup_dir: Path, keep_days: int = 14) -> Path:
    """Copy the database to `<backup_dir>/<name>-<UTC date>.db` and return that path."""
    url = make_url(settings.DATABASE_URL)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError(f"Not a SQLite database file: {url.render_as_string(hide_password=True)}")
    source = Path(url.database).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"No database at {source}: check DATABASE_URL")

    today = datetime.now(UTC).date()
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{source.stem}-{today.isoformat()}.db"
    partial = target.with_suffix(".db.partial")
    # Owner-only from the start, as it holds the provider tokens; the rename keeps the mode
    partial.unlink(missing_ok=True)
    partial.touch(mode=0o600, exist_ok=False)
    # mode=rw: a file removed meanwhile is an error, not a new empty database
    with closing(sqlite3.connect(f"{source.as_uri()}?mode=rw", uri=True)) as src, closing(sqlite3.connect(partial)) as dst:
        src.backup(dst)
        has_tables = dst.execute("SELECT 1 FROM sqlite_master WHERE type = 'table'").fetchone()
    if not has_tables:
        partial.unlink()
        raise ValueError(f"{source} has no tables: check DATABASE_URL")
    partial.replace(target)

    oldest = (today - timedelta(days=keep_days)).isoformat()
    for old in backup_dir.glob(f"{source.stem}-????-??-??.db"):
        if old.stem.removeprefix(f"{source.stem}-") < oldest:
            old.unlink()
            logger.info(f"Removed {old.name}")
    return target


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Back up the SQLite database that DATABASE_URL names")
    parser.add_argument("--dir", type=Path, default=Path("backups"), help="Folder for the backups (default: backups)")
    parser.add_argument("--keep-days", type=int, default=14, help="Days of backups to keep (default: 14)")
    args = parser.parse_args()
    if args.keep_days < 1:
        parser.error("--keep-days must be at least 1")
    try:
        target = backup_database(args.dir, args.keep_days)
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        sys.exit(1)
    logger.info(f"Backed up to {target} ({target.stat().st_size // 1024} KB)")
