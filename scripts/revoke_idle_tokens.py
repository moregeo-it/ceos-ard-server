"""
Revoke the provider tokens of users who have not used the app for a while.

A refresh token lives six months unused; a stolen copy would stay usable that long. Users without
activity for `--days` days (default 7) get their tokens revoked and cleared, so the copy is dead
and the next visit is a fresh login. Activity is `users.last_seen_at`, the last authenticated
request (kept to the hour); rows from before that column fall back to `updated_at`.

Usage:
    python scripts/revoke_idle_tokens.py [--days N] [--dry-run]
"""

import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import func, inspect  # noqa: E402

from app.db.database import SessionLocal, engine  # noqa: E402
from app.dependencies import github_service  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.token_refresh_service import TokenRefreshService  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def revoke_idle_tokens(days: int = 7, dry_run: bool = False) -> int:
    """Revoke and clear the tokens of users idle for `days`; returns how many were handled."""
    if not inspect(engine).has_table("users"):
        # The server creates the schema at startup; a fresh database has nothing to clean yet
        logger.error("No users table: start the server once to create the schema, then rerun")
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=days)
    db = SessionLocal()
    handled = 0
    try:
        last_seen = func.coalesce(User.last_seen_at, User.updated_at)
        idle = db.query(User).filter((User.access_token.isnot(None)) | (User.refresh_token.isnot(None)), last_seen < cutoff).order_by(last_seen).all()
        logger.info(f"{len(idle)} user(s) with tokens and no activity since {cutoff.date()}")
        for user in idle:
            # The row was loaded before the GitHub calls for earlier users; a login since then is not idle
            db.refresh(user)
            seen = user.last_seen_at or user.updated_at
            if seen >= cutoff or not (user.access_token or user.refresh_token):
                logger.info(f"Skipped {user.username}: active since the query")
                continue
            idle_days = (datetime.now(UTC) - seen).days
            if dry_run:
                logger.info(f"[DRY RUN] Would revoke the {user.identity_provider.value} tokens of {user.username} (idle {idle_days} days)")
                continue
            try:
                await TokenRefreshService.end_session(user, db, github_service)
                handled += 1
                logger.info(f"Revoked the {user.identity_provider.value} tokens of {user.username} (idle {idle_days} days)")
            except Exception as e:
                db.rollback()
                logger.error(f"Failed to revoke the tokens of {user.username}: {e}")
    finally:
        db.close()
        await github_service.aclose()
    return handled


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Revoke the provider tokens of users idle for a while")
    parser.add_argument("--days", type=int, default=7, help="Days without activity before tokens are revoked (default: 7)")
    parser.add_argument("--dry-run", action="store_true", help="List the users without revoking anything")
    args = parser.parse_args()
    handled = asyncio.run(revoke_idle_tokens(days=args.days, dry_run=args.dry_run))
    logger.info(f"Done: {handled} user(s) revoked")
