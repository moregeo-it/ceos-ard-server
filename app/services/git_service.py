import asyncio
import logging
import shutil
from pathlib import Path

import git
from fastapi import HTTPException, status

from app.config import settings
from app.utils.git_utils import get_file_status
from app.utils.validation import normalize_workspace_path, validate_workspace_path

logger = logging.getLogger(__name__)


class GitService:
    def __init__(self):
        self.workspaces_root = settings.WORKSPACES_ROOT
        self.workspaces_root.mkdir(parents=True, exist_ok=True)

    async def clone_repository(
        self, clone_url: str, workspace_path: Path, branch_name: str, upstream_owner: str, upstream_repo: str, upstream_branch: str = "main"
    ) -> bool:
        try:
            if workspace_path.exists():
                shutil.rmtree(workspace_path)

            workspace_path.parent.mkdir(parents=True, exist_ok=True)

            repo = git.Repo.clone_from(clone_url, workspace_path, depth=1)

            repo.create_remote("upstream", f"https://github.com/{upstream_owner}/{upstream_repo}")

            repo.remotes.upstream.fetch(upstream_branch)

            repo.git.checkout("-b", branch_name, f"upstream/{upstream_branch}")

            logger.info(f"Successfully cloned repository to {workspace_path}")

            return True

        except git.InvalidGitRepositoryError as e:
            logger.error(f"Invalid git repository: {clone_url}")

            if workspace_path.exists():
                shutil.rmtree(workspace_path)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a valid git repository") from e

        except git.GitCommandError as e:
            logger.error(f"Error cloning repository: {e}")

            if workspace_path.exists():
                shutil.rmtree(workspace_path)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to clone repository: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error cloning repository: {e}")

            if workspace_path.exists():
                shutil.rmtree(workspace_path)

            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to clone repository: {e}") from e

    async def revert_file_changes(self, workspace_path: Path, file_path: str):
        try:
            target_file_path = validate_workspace_path(file_path, workspace_path)
            relative_file_str = normalize_workspace_path(target_file_path, workspace_path, absolute=False)

            repo = git.Repo(workspace_path)

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

            # File has no git history - cannot revert
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot revert file with no git history. File was never committed.")

        except git.InvalidGitRepositoryError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a valid git repository") from e
        except git.GitCommandError as e:
            logger.error(f"Git command error reverting file: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to revert file changes: {str(e)}") from e
        except ValueError as e:
            # This can happen if the file path is outside the workspace
            logger.error(f"Path error: {e}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path") from e
        except Exception as e:
            logger.error(f"Error reverting file changes: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to revert file changes") from e

    async def commit_and_push_changes(self, repo: git.Repo, branch_name: str, commit_message: str):
        try:
            # Commit changes to the repository
            repo.index.commit(commit_message)
            logger.info(f"Committed changes on branch {branch_name} with message: {commit_message}")

            # Push changes to the remote repository
            origin = repo.remote(name="origin")
            push_info = origin.push(refspec=f"{branch_name}:{branch_name}")

            for info in push_info:
                if info.flags & info.ERROR:
                    raise git.GitCommandError(f"Push failed: {info.summary}", 1)

            logger.info(f"Pushed changes to remote branch {branch_name}")
        except git.GitCommandError as e:
            logger.error(f"Git command error during commit/push: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to commit or push changes: {str(e)}") from e
        except Exception as e:
            logger.error(f"Error during commit/push: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to commit or push changes") from e
