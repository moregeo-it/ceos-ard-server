import logging
import shutil
from pathlib import Path

import git
import os
from fastapi import HTTPException, status

from app.config import settings
from app.schemas.workspace import GitStatusFile
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

    async def get_git_status(self, workspace_path: Path) -> dict[str, list[GitStatusFile]]:
        if not workspace_path.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
        elif not workspace_path.is_dir():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace path is not a directory")

        try:
            repo = git.Repo(workspace_path)

            modified_files = []
            untracked_files = []
            current_branch = repo.active_branch.name
            untracked_files = list(repo.untracked_files)

            # Get modified/staged files
            # Check index (staged changes)
            # repo.index.diff("HEAD", R=True) will show staged changes from HEAD to index.
            for item in repo.index.diff("HEAD", R=True):
                file_status = "modified"
                if item.change_type == "A":
                    file_status = "added"
                elif item.change_type == "D":
                    file_status = "deleted"
                elif item.change_type == "R":
                    file_status = "renamed"
                elif item.change_type == "C":
                    file_status = "copied"
                elif item.change_type == "M":
                    file_status = "modified"

                modified_files.append(GitStatusFile(path=item.a_path, status=file_status))

            # Check working directory (unstaged changes)
            for item in repo.index.diff(None):
                file_status = "modified"
                if item.change_type == "D":
                    file_status = "deleted"
                elif item.change_type == "M":
                    file_status = "modified"

                # Check if file is already in modified_files (staged)
                existing_file = next((f for f in modified_files if f.path == item.a_path), None)
                if existing_file:
                    # File has both staged and unstaged changes
                    existing_file.status = "modified"
                else:
                    modified_files.append(GitStatusFile(path=item.a_path, status=file_status))

            ahead_commits = 0
            behind_commits = 0

            try:
                # Try to get upstream tracking branch
                tracking_branch = repo.active_branch.tracking_branch()
                if tracking_branch:
                    # Get commits ahead/behind
                    ahead_commits = len(list(repo.iter_commits(f"{tracking_branch.name}..HEAD")))
                    behind_commits = len(list(repo.iter_commits(f"HEAD..{tracking_branch.name}")))
            except Exception as fetch_exception:
                logger.error(f"Failed to get upstream branch info: {fetch_exception}")
                # Fallback: try to find upstream/main or origin/main
                try:
                    upstream_ref = None
                    for remote_ref in repo.remote("upstream").refs:
                        if remote_ref.name.endswith("/main"):
                            upstream_ref = remote_ref
                            break

                    if not upstream_ref:
                        for remote_ref in repo.remote("origin").refs:
                            if remote_ref.name.endswith("/main"):
                                upstream_ref = remote_ref
                                break

                    if upstream_ref:
                        ahead_commits = len(list(repo.iter_commits(f"{upstream_ref.name}..HEAD")))
                        behind_commits = len(list(repo.iter_commits(f"HEAD..{upstream_ref.name}")))

                except Exception as e:
                    logger.error(f"Failed to find upstream/main branch: {e}")

            is_clean = len(modified_files) == 0 and len(untracked_files) == 0

            return {
                "branch": current_branch,
                "is_clean": is_clean,
                "ahead_commits": ahead_commits,
                "behind_commits": behind_commits,
                "modified_files": modified_files,
                "untracked_files": untracked_files,
            }

        except git.InvalidGitRepositoryError as e:
            logger.error(f"Invalid git repository: {workspace_path}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a valid git repository") from e
        except git.GitCommandError as e:
            logger.error(f"Git command error: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Git operation failed: {str(e)}") from e
        except Exception as e:
            logger.error(f"Error getting git status: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get git status") from e

    async def revert_file_changes(self, workspace_path: Path, file_path: str):
        try:
            target_file_path = validate_workspace_path(file_path, workspace_path)
            relative_file_str = normalize_workspace_path(target_file_path, workspace_path, absolute=False)

            repo = git.Repo(workspace_path)
            # Check if file is tracked by Git (exists in HEAD commit)
            is_tracked = False
            try:
                repo.git.cat_file("-e", f"HEAD:{relative_file_str}")
                is_tracked = True
            except git.GitCommandError:
                is_tracked = False

            # Handle file existence based on tracking status
            if not target_file_path.exists():
                if not is_tracked:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File does not exist and is not tracked by Git")
                # File is tracked but deleted - this is okay, we can restore it
            elif not target_file_path.is_file():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path is not a file")

            if not target_file_path.exists() and is_tracked:
                pass
            elif target_file_path.exists():
                has_changes = False

                # Check for staged changes
                staged_diff = repo.index.diff("HEAD", R=True)
                for item in staged_diff:
                    if item.a_path == relative_file_str or item.b_path == relative_file_str:
                        has_changes = True
                        break

                # Check for unstaged changes
                if not has_changes:
                    unstaged_diff = repo.index.diff(None)
                    for item in unstaged_diff:
                        if item.a_path == relative_file_str:
                            has_changes = True
                            break

                if not has_changes:
                    return f"No changes to revert for file: {file_path}"

            else:
                # File does not exist and is not tracked by Git
                if relative_file_str in repo.untracked_files:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot revert untracked file. Use delete instead.")

            # Revert the file using GitPython
            # This is equivalent to `git checkout -- file_path`
            repo.git.checkout("--", relative_file_str)

            if not target_file_path.exists():
                # File was deleted, now it's restored
                logger.info(f"Successfully reverted deleted file: {file_path}")
                return {"path": str(target_file_path), "name": str(target_file_path.name), "directory":     False}
            else:
                logger.info(f"Successfully reverted changes for file: {file_path}")
                return {"path": str(target_file_path), "name": str(target_file_path.name), "directory": False}

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
