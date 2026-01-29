import logging
import shutil
from pathlib import Path

import git
from fastapi import HTTPException, status

from app.config import settings
from app.models.user import User
from app.utils.git_utils import build_authenticated_url, get_file_status, get_repo, sanitize_git_error
from app.utils.validation import normalize_workspace_path, validate_workspace_path

logger = logging.getLogger(__name__)


class GitService:
    def __init__(self):
        self.workspaces_root = settings.WORKSPACES_ROOT
        self.workspaces_root.mkdir(parents=True, exist_ok=True)

    async def clone_repository(
        self,
        user: User,
        clone_url: str,
        workspace_path: Path,
        branch_name: str,
        upstream_owner: str,
        upstream_repo: str,
        upstream_branch: str = "main",
    ) -> bool:
        try:
            workspace_path.parent.mkdir(parents=True, exist_ok=True)

            repo = git.Repo.clone_from(clone_url, workspace_path, depth=1)
            repo.create_remote("upstream", f"https://github.com/{upstream_owner}/{upstream_repo}")
            repo.remotes.upstream.fetch(upstream_branch)
            # Check out from the upstream branch so that the data is up-to-date
            # Use --no-track to avoid tracking upstream, we'll set origin tracking after push
            repo.git.checkout("-b", branch_name, "--no-track", f"upstream/{upstream_branch}")
            # Push to the repo and set upstream tracking branch in one operation
            # This also verifies we have the proper permissions to push
            await self.push(repo=repo, branch_name=branch_name, user=user, set_upstream=True)

            logger.info(f"Successfully cloned repository to {workspace_path}")

            return True
        except git.InvalidGitRepositoryError as e:
            logger.error(f"Invalid git repository: {clone_url}")

            if workspace_path.exists():
                shutil.rmtree(workspace_path)

            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a valid git repository") from e
        except Exception as e:
            logger.error(f"Error cloning repository: {e}")

            if workspace_path.exists():
                shutil.rmtree(workspace_path)

            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to clone repository: {e}") from e

    async def revert_file_changes(self, workspace_path: Path, file_path: str):
        target_file_path = validate_workspace_path(file_path, workspace_path)
        relative_file_str = normalize_workspace_path(target_file_path, workspace_path, absolute=False)

        repo = get_repo(workspace_path)

        # Check if file exists in HEAD commit
        try:
            repo.git.cat_file("-e", f"HEAD:{relative_file_str}")
            # File exists in HEAD - restore it directly
            repo.git.checkout("HEAD", "--", relative_file_str)

            return {
                "name": str(target_file_path.name),
                "is_directory": target_file_path.is_dir(),
                "status": get_file_status(repo, target_file_path),
                "path": normalize_workspace_path(target_file_path, workspace_path),
            }
        except git.GitCommandError:
            # File not in HEAD - check if it's part of a rename
            pass

        # Check for renames in staged changes
        try:
            for item in repo.index.diff("HEAD", R=True):
                if item.change_type == "R" and item.b_path == relative_file_str:
                    old_path = item.a_path

                    # Unstage the rename
                    repo.git.reset("HEAD", "--", old_path, relative_file_str)

                    # Restore original file from HEAD
                    repo.git.checkout("HEAD", "--", old_path)

                    # Remove new file if it exists
                    if target_file_path.exists():
                        target_file_path.unlink()

                    old_file_path = workspace_path / old_path
                    return {
                        "name": str(old_file_path.name),
                        "is_directory": old_file_path.is_dir(),
                        "status": get_file_status(repo, old_file_path),
                        "path": normalize_workspace_path(old_file_path, workspace_path),
                    }
        except git.GitCommandError as e:
            logger.error(f"Git command error reverting file: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to revert file changes: {str(e)}") from e
        except Exception as e:
            logger.error(f"Error reverting file changes: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to revert file changes") from e

        # File has no git history - cannot revert
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot revert file with no git history. File was never committed.")

    async def commit_changes(self, repo: git.Repo, message: str):
        """
        Commit changes to the current branch in the repository.

        Args:
            repo: GitPython Repo instance
            message: Commit message
        """
        try:
            return repo.index.commit(message)
        except Exception as e:
            logger.error(f"Unable to commit changes: {str(e)}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to commit changes, please try again.") from e

    async def push(self, repo: git.Repo, branch_name: str, user: User, set_upstream: bool = False):
        """
        Push changes with user-specific credentials.

        Args:
            repo: GitPython Repo instance
            branch_name: Branch to push to
            user: User object with username and access_token for authentication
        """
        try:
            # Build authenticated URL (credentials are per-request, not stored)
            origin = repo.remote(name="origin")
            old_url = origin.url
            temp_url = build_authenticated_url(origin.url, user.username, user.access_token)
            origin.set_url(temp_url, old_url)
            if set_upstream:
                repo.git.push("-u", "origin", branch_name)
                origin.fetch()
                repo.heads[branch_name].set_tracking_branch(origin.refs[branch_name])
            else:
                origin.push()
            origin.set_url(old_url, temp_url)

            logger.info(f"Pushed changes to remote branch {branch_name}")
        except Exception as e:
            error_msg = sanitize_git_error(e, user.username, user.access_token)
            logger.error(f"Unable to push: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to send changes to GitHub, please try again. Error: {error_msg}"
            ) from None  # don't raise e to avoid leaking sensitive information
