import os
import shutil
import logging
import subprocess
from typing import List, Tuple
from fastapi import HTTPException, status

from app.config import settings
from app.schemas.workspace import GitStatusFile

logger = logging.getLogger(__name__)

class GitService:
    def __init__(self):
        self.workspaces_root = settings.WORKSPACES_ROOT
        self._ensure_workspaces_directory()

    def _ensure_workspaces_directory(self):
        os.makedirs(self.workspaces_root, exist_ok=True)

    def _run_git_command(self, command: List[str], cwd: str) -> Tuple[str, str, int]:
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True, 
                timeout=300
            )

            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Git command timed out")
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to run git command: {e}")
        
    def generate_workspace_path(self, workspace_id: str) -> str:
        return os.path.join(self.workspaces_root, workspace_id)
    
    def generate_branch_name(self, workspace_id: str) -> str:
        return f"workspace/{workspace_id}"
    
    async def clone_repository(
            self, 
            clone_url: str, 
            workspace_path: str,
            branch_name: str,
            upstream_owner: str,
            upstream_repo: str,
            upstream_branch: str = "main"
    ) -> bool:
        try:
            workspace_path = os.path.abspath(workspace_path)

            if os.path.exists(workspace_path):
                shutil.rmtree(workspace_path)

            os.makedirs(os.path.dirname(workspace_path), exist_ok=True)

            stdout, stderr, returncode = self._run_git_command(
                ["git", "clone", clone_url, workspace_path],
                cwd=os.path.dirname(workspace_path)
            )

            if returncode != 0:
                logger.error(f"Failed to clone repository: {stderr}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to clone repository: {stderr}")
            
            upstream_url = f"https://github.com/{upstream_owner}/{upstream_repo}"
            stdout, stderr, returncode = self._run_git_command(
                ["git", "remote", "add", "upstream", upstream_url],
                cwd=workspace_path
            )

            if returncode != 0:
                logger.error(f"Failed to add upstream remote: {stderr}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to add upstream remote: {stderr}")

            stdout, stderr, returncode = self._run_git_command(
                ["git", "fetch", "upstream", upstream_branch],
                cwd=workspace_path
            )

            if returncode != 0:
                logger.error(f"Failed to fetch upstream branch: {stderr}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch upstream branch: {stderr}")

            stdout, stderr, returncode = self._run_git_command(
                ["git", "checkout", "-b", branch_name, f'upstream/{upstream_branch}'],
                cwd=workspace_path
            )

            if returncode != 0:
                logger.error(f"Failed to checkout branch: {stderr}")

                stdout, stderr, returncode = self._run_git_command(
                    ["git", "checkout", branch_name],
                    cwd=workspace_path
                )

                if returncode != 0:
                    logger.error(f"Failed to checkout branch: {stderr}")
                    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to checkout branch: {stderr}")
            
            logger.info(f"Successfully cloned repository to {workspace_path}")

            return True

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error cloning repository: {e}")

            if os.path.exists(workspace_path):
                shutil.rmtree(workspace_path)

            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to clone repository: {e}")
        
    async def get_git_status(self, workspace_path: str) -> str:
        if not os.path.exists(workspace_path):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
        try:
            stdout, stderr, returncode = self._run_git_command(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                cwd=workspace_path
            )

            if returncode != 0:
                logger.error(f"Failed to get git status: {stderr}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get current branch: {stderr}")

            current_branch = stdout

            stdout, stderr, returncode = self._run_git_command(
                ['git', 'status', '--porcelain'],
                cwd=workspace_path
            )

            modified_files = []
            untracked_files = []

            if stdout:
                for line in stdout.split('\n'):
                    if line.strip():
                        status_code = line[2]
                        file_path = line[4:]
                        if status_code == '??':
                            untracked_files.append(file_path)
                        else:
                            status_map = {
                                'M ': 'modified',
                                ' M': 'modified',
                                'MM': 'modified',
                                'A ': 'added',
                                ' A': 'added',
                                'D ': 'deleted',
                                ' D': 'deleted',
                                'R ': 'renamed',
                                'C ': 'copied',
                            }

                            file_status = status_map.get(status_code, 'unknown')
                            modified_files.append(GitStatusFile(
                                path=file_path,
                                status=file_status
                            ))
            
            ahead_commits = 0
            behind_commits = 0
            
            try:
                self._run_git_command(['git', 'fetch', 'upstream'], cwd=workspace_path)
                
                stdout, stderr, returncode = self._run_git_command([
                    'git', 'rev-list', '--left-right', '--count', f'HEAD...upstream/main'
                ], cwd=workspace_path)
                
                if returncode == 0 and stdout:
                    parts = stdout.split()
                    if len(parts) == 2:
                        ahead_commits = int(parts[0])
                        behind_commits = int(parts[1])
            except:
                pass
            
            is_clean = len(modified_files) == 0 and len(untracked_files) == 0
            
            return {
                'branch': current_branch,
                'is_clean': is_clean,
                'ahead_commits': ahead_commits,
                'behind_commits': behind_commits,
                'modified_files': modified_files,
                'untracked_files': untracked_files
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting git status: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to get git status"
            )
    
git_service = GitService()