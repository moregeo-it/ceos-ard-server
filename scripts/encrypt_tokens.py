"""
Encrypt every stored provider token with the first key of TOKEN_ENCRYPTION_KEY.

Covers plain-text tokens from before encryption at rest, and tokens under an older key after a key
rotation (new key first, old key after it; once this ran, the old key can go). Safe to run
repeatedly: tokens already under the first key are skipped. Run it while the server is stopped, so
the final VACUUM can rewrite the file without the old values in its free pages.

Usage:
    python scripts/encrypt_tokens.py
"""

import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cryptography.fernet import Fernet, InvalidToken  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.types import FERNET_PREFIX, token_cipher  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

COLUMNS = ["access_token", "refresh_token"]


def reencrypted(token: str) -> str | None:
    """The token encrypted with the first key, or None when it already is."""
    if not token.startswith(FERNET_PREFIX):
        return token_cipher().encrypt(token.encode()).decode()
    try:
        Fernet(settings.TOKEN_ENCRYPTION_KEYS[0]).decrypt(token.encode())
        return None
    except InvalidToken:
        # Under an older key; raises InvalidToken when no listed key decrypts it
        return token_cipher().rotate(token.encode()).decode()


def encrypt_tokens(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    encrypted = 0
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone():
            logger.info("users: table does not exist yet, nothing to encrypt")
            return 0
        for user_id, *tokens in conn.execute(f"SELECT id, {', '.join(COLUMNS)} FROM users").fetchall():
            updates = {column: new for column, token in zip(COLUMNS, tokens, strict=True) if token and (new := reencrypted(token))}
            if updates:
                conn.execute(f"UPDATE users SET {', '.join(f'{c} = ?' for c in updates)} WHERE id = ?", (*updates.values(), user_id))
                encrypted += len(updates)
        conn.commit()
        if encrypted:
            # The replaced values stay in free pages and the WAL until the file is rewritten
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return encrypted


if __name__ == "__main__":
    url = settings.DATABASE_URL
    if not url.startswith("sqlite:///"):
        logger.error(f"Only SQLite databases are supported, got {url}")
        sys.exit(1)
    path = url.removeprefix("sqlite:///")
    encrypted = encrypt_tokens(path)
    logger.info(f"Done: {encrypted} token(s) encrypted in {path}" if encrypted else f"Done: every token in {path} is under the first key")
