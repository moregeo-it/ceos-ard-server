import logging
import shutil
import subprocess
from pathlib import Path

from fastapi import HTTPException, status

from app.config import settings
from app.schemas.workspace import GitStatusFile

logger = logging.getLogger(__name__)


class GitService:
    def __init__(self):
        self.workspaces_root = Path(settings.WORKSPACES_ROOT)
        self._ensure_workspaces_directory()

    def _ensure_workspaces_directory(self):
        self.workspaces_root.mkdir(parents=True, exist_ok=True)

    def _run_git_command(self, command: list[str], cwd: str) -> tuple[str, str, int]:
        try:
            result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=300)

            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as e:
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Git command timed out") from e
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to run git command: {e}") from e

    def generate_workspace_path(self, workspace_id: str) -> str:
        return str(self.workspaces_root / workspace_id)

    def generate_branch_name(self, workspace_id: str) -> str:
        return f"workspace/{workspace_id}"

    async def clone_repository(
        self, clone_url: str, workspace_path: str, branch_name: str, upstream_owner: str, upstream_repo: str, upstream_branch: str = "main"
    ) -> bool:
        try:
            workspace_path = Path(workspace_path).resolve()

            if workspace_path.exists():
                shutil.rmtree(workspace_path)

            workspace_path.parent.mkdir(parents=True, exist_ok=True)

            stdout, stderr, returncode = self._run_git_command(["git", "clone", clone_url, workspace_path], cwd=workspace_path.parent)

            if returncode != 0:
                logger.error(f"Failed to clone repository: {stderr}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to clone repository: {stderr}")

            upstream_url = f"https://github.com/{upstream_owner}/{upstream_repo}"
            stdout, stderr, returncode = self._run_git_command(["git", "remote", "add", "upstream", upstream_url], cwd=workspace_path)

            if returncode != 0:
                logger.error(f"Failed to add upstream remote: {stderr}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to add upstream remote: {stderr}")

            stdout, stderr, returncode = self._run_git_command(["git", "fetch", "upstream", upstream_branch], cwd=workspace_path)

            if returncode != 0:
                logger.error(f"Failed to fetch upstream branch: {stderr}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch upstream branch: {stderr}")

            stdout, stderr, returncode = self._run_git_command(
                ["git", "checkout", "-b", branch_name, f"upstream/{upstream_branch}"], cwd=workspace_path
            )

            if returncode != 0:
                logger.error(f"Failed to checkout branch: {stderr}")

                stdout, stderr, returncode = self._run_git_command(["git", "checkout", branch_name], cwd=workspace_path)

                if returncode != 0:
                    logger.error(f"Failed to checkout branch: {stderr}")
                    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to checkout branch: {stderr}")

            logger.info(f"Successfully cloned repository to {workspace_path}")

            return True

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error cloning repository: {e}")

            if workspace_path.exists():
                shutil.rmtree(workspace_path)

            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to clone repository: {e}") from e

    async def get_git_status(self, workspace_path: str) -> str:
        if not workspace_path.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

        try:
            stdout, stderr, returncode = self._run_git_command(["git", "status", "--porcelain", "--branch"], cwd=workspace_path)

            if returncode != 0:
                logger.error(f"Failed to get git status: {stderr}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get git status: {stderr}")

            lines = stdout.split("\n")
            current_branch = lines[0].strip()[2:]
            modified_files = []
            untracked_files = []

            for line in lines[1:]:
                if line.strip():
                    status_code = line[:2].strip()
                    file_path = line[3:]

                    if status_code == "??":
                        untracked_files.append(file_path)
                    else:
                        status_map = {
                            "M ": "modified",
                            " M": "modified",
                            "M": "modified",
                            "MM": "modified",
                            "A ": "added",
                            " A": "added",
                            "A": "added",
                            "AA": "added",
                            "D ": "deleted",
                            " D": "deleted",
                            "R ": "renamed",
                            "C ": "copied",
                            "R": "renamed",
                        }

                        file_status = status_map.get(status_code, "unknown")
                        modified_files.append(GitStatusFile(path=file_path, status=file_status))

            ahead_commits = 0
            behind_commits = 0

            try:
                stdout, stderr, returncode = self._run_git_command(
                    ["git", "rev-list", "--left-right", "--count", "HEAD...upstream/main"], cwd=workspace_path
                )

                if returncode == 0 and stdout:
                    parts = stdout.split()
                    if len(parts) == 2:
                        ahead_commits = int(parts[0])
                        behind_commits = int(parts[1])
            except Exception as fetch_exception:
                logger.error(f"Failed to fetch upstream branch: {fetch_exception}")

            is_clean = len(modified_files) == 0 and len(untracked_files) == 0

            return {
                "branch": current_branch,
                "is_clean": is_clean,
                "ahead_commits": ahead_commits,
                "behind_commits": behind_commits,
                "modified_files": modified_files,
                "untracked_files": untracked_files,
            }
        except Exception as e:
            logger.error(f"Error getting git status: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get git status") from e

    async def revert_file_changes(self, workspace_path: Path, file_path: Path) -> str:
        try:
            if not workspace_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
            if not workspace_path.is_dir():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace path is not a directory")

            target_file_path = workspace_path / file_path
            if not target_file_path.is_relative_to(workspace_path):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found in workspace")
            if not target_file_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File does not exist")
            if not target_file_path.is_file():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path is not a file")

            output, error, returncode = self._run_git_command(["git", "checkout", "--", file_path], cwd=workspace_path)

            if returncode != 0:
                logger.error(f"Error reverting file changes: {error}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to revert file changes")

            logger.info(f"Successfully reverted changes for file: {file_path}")

            return f"Reverted changes for file: {file_path.name}"
        except Exception as e:
            logger.error(f"Error reverting file changes: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to revert file changes") from e


git_service = GitService()
