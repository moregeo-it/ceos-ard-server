"""
Cleanup script to permanently delete archived workspaces past their deletion_at date

This script should be run periodically (e.g., via cron job) to clean up archived workspaces.
It deletes the workspace files from disk and database records for workspaces where:
- status = ARCHIVED
- archived_at + 1 month <= current_time (deletion_at is computed dynamically)

Usage:
    python scripts/cleanup_archived_workspaces.py [--dry-run]
Options:
    --dry-run   Show what would be deleted without actually deleting
"""

import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

from dateutil.relativedelta import relativedelta

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.database import SessionLocal  # noqa: E402

# Import User first to register it with SQLAlchemy before GitWorkspace
from app.models.user import User  # noqa: E402, F401
from app.models.workspace import GitWorkspace, WorkspaceStatus  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def cleanup_archived_workspaces(dry_run: bool = False):
    """
    Delete workspace files for archived workspaces past their deletion date

    Args:
        dry_run: If True, only log what would be deleted without actually deleting
    """
    db = SessionLocal()

    try:
        current_time = datetime.utcnow()
        # Calculate cutoff date: workspaces archived more than 1 month ago
        cutoff_date = current_time - relativedelta(months=1)

        # Find archived workspaces past their deletion date (archived_at + 1 month <= now)
        workspaces_to_delete = (
            db.query(GitWorkspace)
            .filter(
                GitWorkspace.status == WorkspaceStatus.ARCHIVED,
                GitWorkspace.archived_at.isnot(None),
                GitWorkspace.archived_at <= cutoff_date,
            )
            .all()
        )

        if not workspaces_to_delete:
            logger.info("No archived workspaces found for cleanup")
            return

        logger.info(f"Found {len(workspaces_to_delete)} workspace(s) to clean up")

        deleted_count = 0
        error_count = 0

        for workspace in workspaces_to_delete:
            workspace_path = Path(workspace.workspace_path)

            try:
                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would delete workspace {workspace.id} "
                        f"(title: {workspace.title}, path: {workspace_path}, "
                        f"archived: {workspace.archived_at}, deletion: {workspace.deletion_at})"
                    )
                else:
                    # Delete workspace files if they exist
                    if workspace_path.exists():
                        shutil.rmtree(workspace_path)
                        logger.info(f"Deleted workspace files for {workspace.id} at {workspace_path}")
                    else:
                        logger.warning(f"Workspace {workspace.id} path does not exist: {workspace_path}")

                    # Delete database record
                    db.delete(workspace)
                    db.commit()
                    logger.info(f"Deleted workspace {workspace.id} " f"(title: {workspace.title}) from database")
                    deleted_count += 1

            except Exception as e:
                db.rollback()
                logger.error(f"Error deleting workspace {workspace.id} at {workspace_path}: {e}")
                error_count += 1

        if not dry_run:
            logger.info(f"Cleanup complete: {deleted_count} deleted, {error_count} errors")
        else:
            logger.info(f"Dry run complete: {len(workspaces_to_delete)} workspace(s) would be deleted")

    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Clean up archived workspaces past their deletion date")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )

    args = parser.parse_args()

    logger.info("Starting workspace cleanup...")
    cleanup_archived_workspaces(dry_run=args.dry_run)
    logger.info("Workspace cleanup finished")
