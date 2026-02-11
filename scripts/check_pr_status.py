"""
Pull Request Status Checker Script

Periodically checks the status of all pull requests for workspaces and updates their
status in the database. Automatically archives workspaces when their PRs are merged or closed.

This script:
- Fetches all PRs from the upstream repository
- Matches workspace PR numbers against fetched PRs
- Updates PR status (open/merged/closed) in the database
- Auto-archives workspaces with merged/closed PRs
- Supports dry-run mode for testing

Usage:
    python scripts/check_pr_status.py [--dry-run] [--limit N]

Options:
    --dry-run   Show what would be updated without making changes
    --limit N   Limit number of workspaces to check (for testing)

"""

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402

# Import User first to register it with SQLAlchemy before GitWorkspace
from app.models.user import User  # noqa: E402, F401
from app.models.workspace import GitWorkspace, PullRequestStatus, WorkspaceStatus  # noqa: E402
from app.services.github_service import GitHubService  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def check_pr_status(dry_run: bool = False, limit: int = None):
    """
    Check and update the status of all workspace pull requests.

    Args:
        dry_run: If True, only log what would be updated without making changes
        limit: Optional limit on number of workspaces to check (for testing)
    """
    db = SessionLocal()
    github_service = GitHubService()

    try:
        # Validate service token is configured
        service_token = settings.GITHUB_SERVICE_TOKEN
        if not service_token:
            logger.error("GITHUB_SERVICE_TOKEN not configured in environment")
            logger.error("Please set GITHUB_SERVICE_TOKEN in your .env file")
            return

        logger.info("Starting PR status check...")
        logger.info(f"Upstream repository: {settings.CEOS_ARD_ORG}/{settings.CEOS_ARD_REPO}")
        if dry_run:
            logger.info("DRY RUN MODE - No changes will be made")

        # Fetch all PRs from upstream repository
        logger.info(f"Fetching all pull requests from {settings.CEOS_ARD_ORG}/{settings.CEOS_ARD_REPO}...")
        all_prs = await github_service.get_repository_pull_requests(
            owner=settings.CEOS_ARD_ORG,
            repo=settings.CEOS_ARD_REPO,
            token=service_token,
            state="all",  # Get open, closed, and merged PRs
        )

        # Create lookup dict: {pr_number: pr_data}
        pr_lookup = {str(pr["number"]): pr for pr in all_prs}
        logger.info(f"Found {len(pr_lookup)} total pull requests in repository")

        # Get all workspaces with PR numbers
        query = db.query(GitWorkspace).filter(GitWorkspace.pull_request_number.isnot(None))

        if limit is not None:
            if limit <= 0:
                logger.warning(f"Ignoring non-positive limit value: {limit} (must be > 0)")
            else:
                query = query.limit(limit)
                logger.info(f"Limiting check to {limit} workspaces")

        workspaces = query.all()
        logger.info(f"Found {len(workspaces)} workspace(s) with pull requests")

        if not workspaces:
            logger.info("No workspaces with pull requests found")
            return

        # Process each workspace
        for workspace in workspaces:
            try:
                pr_number = str(workspace.pull_request_number)

                # Check if PR exists in our fetched data
                if pr_number not in pr_lookup:
                    logger.warning(f"PR #{pr_number} not found in repository for workspace {workspace.id} (title: {workspace.title})")
                    continue

                pr = pr_lookup[pr_number]

                # Determine PR status
                # GitHub PR state is 'open' or 'closed'
                # For closed PRs, check if it was merged
                pr_state = pr["state"]  # 'open' or 'closed'
                is_merged = pr.get("merged_at") is not None

                if is_merged:
                    new_status = PullRequestStatus.MERGED
                elif pr_state == "open":
                    new_status = PullRequestStatus.OPEN
                elif pr_state == "closed":
                    new_status = PullRequestStatus.CLOSED
                else:
                    logger.warning(f"Unknown PR state '{pr_state}' for PR #{pr_number}")
                    new_status = PullRequestStatus.UNKNOWN

                # Determine if PR status field needs to be updated
                status_changed = workspace.pull_request_status != new_status
                if not status_changed:
                    logger.debug(f"Workspace {workspace.id} PR #{pr_number} status unchanged: {new_status.value}")

                # Status has changed - log transition
                if status_changed:
                    old_status = workspace.pull_request_status.value if workspace.pull_request_status else "None"
                    logger.info(f"Workspace {workspace.id} (title: {workspace.title}) PR #{pr_number}: {old_status} -> {new_status.value}")

                if dry_run:
                    # Log what would happen for PR status
                    if status_changed:
                        logger.info(f"[DRY RUN] Would update workspace {workspace.id} PR status to {new_status.value}")

                    # Check if it would be archived (even if PR status value is unchanged)
                    if new_status in [PullRequestStatus.MERGED, PullRequestStatus.CLOSED]:
                        if workspace.status != WorkspaceStatus.ARCHIVED:
                            logger.info(f"[DRY RUN] Would archive workspace {workspace.id} (PR is {new_status.value})")

                    # Check if it would be reactivated (PR reopened)
                    if new_status == PullRequestStatus.OPEN:
                        if workspace.status == WorkspaceStatus.ARCHIVED:
                            logger.info(f"[DRY RUN] Would reactivate workspace {workspace.id} (PR #{pr_number} reopened)")
                else:
                    changed = False

                    # Update workspace PR status if it changed
                    if status_changed:
                        workspace.pull_request_status = new_status
                        workspace.pull_request_status_last_updated_at = datetime.utcnow()
                        changed = True

                    # Auto-archive if merged or closed (even if PR status field did not change)
                    if new_status in [PullRequestStatus.MERGED, PullRequestStatus.CLOSED]:
                        if workspace.status != WorkspaceStatus.ARCHIVED:
                            workspace.status = WorkspaceStatus.ARCHIVED
                            workspace.archived_at = datetime.utcnow()
                            logger.info(f"Archived workspace {workspace.id} (PR #{pr_number} is {new_status.value})")
                            changed = True

                    # Reactivate if PR is reopened
                    elif new_status == PullRequestStatus.OPEN:
                        if workspace.status == WorkspaceStatus.ARCHIVED:
                            workspace.status = WorkspaceStatus.ACTIVE
                            workspace.archived_at = None
                            logger.info(f"Reactivated workspace {workspace.id} (PR #{pr_number} reopened)")
                            changed = True

                    if changed:
                        db.commit()
                        db.refresh(workspace)
            except Exception as e:
                db.rollback()
                logger.error(f"Error processing workspace {workspace.id}: {e}", exc_info=True)
                continue

        if dry_run:
            logger.info("DRY RUN complete - no changes were made to the database")
        else:
            logger.info("PR status check complete")

    except Exception as e:
        logger.error(f"Fatal error during PR status check: {e}", exc_info=True)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Check and update pull request status for all workspaces")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without making changes",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of workspaces to check (for testing)",
    )

    args = parser.parse_args()

    logger.info("Starting PR status checker...")
    asyncio.run(check_pr_status(dry_run=args.dry_run, limit=args.limit))
    logger.info("PR status checker finished")
