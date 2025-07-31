import uuid
import logging
import asyncio
from datetime import datetime
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from typing import List, Dict, Any, Optional

from app.services.git_service import git_service
from app.services.build_service import build_service
from app.services.github_service import github_service
from app.models.workspace import GitWorkspace, WorkspaceStatus
from app.schemas.workspace import WorkspaceCreate, WorkspaceUpdate

logger = logging.getLogger(__name__)

class WorkspaceService:
    def __init__(self):
        self.git_service = git_service
        self.github_service = github_service
        self.build_service = build_service

    async def create_workspace(
        self,
        db: Session,
        workspace_data: WorkspaceCreate,
        user_id: str,
        username: str,
        access_token: str
    ) -> GitWorkspace:
        try:
            logger.info(f"Checking fork for user {username}")
            fork_repo, was_created = await self.github_service.get_or_create_fork(
                username=username,
                access_token=access_token,
                upstream_owner=workspace_data.upstream_repo_owner,
                upstream_repo=workspace_data.upstream_repo_name
            )

            if was_created:
                logger.info(f"Created new fork for user {username}")
                await asyncio.sleep(2)

            # Generate workspace path and branch name
            workspace_id = str(uuid.uuid4())

            branch_name = self.git_service.generate_branch_name(workspace_id)
            workspace_path = self.git_service.generate_workspace_path(workspace_id)

            # Create workspace record in database
            workspace = GitWorkspace(
                user_id=user_id,
                title=workspace_data.title,
                default_pfs=workspace_data.default_pfs or '',
                upstream_repo_owner=workspace_data.upstream_repo_owner,
                upstream_repo_name=workspace_data.upstream_repo_name,
                forked_repo_owner=fork_repo['owner']['login'],
                forked_repo_name=fork_repo['name'],
                fork_repo_clone_url=fork_repo['clone_url'],
                branch_name=branch_name,
                upstream_branch_name=workspace_data.upstream_branch_name or "main",
                workspace_path=workspace_path,
                status=WorkspaceStatus.CREATING
            )

            db.add(workspace)
            db.commit()
            db.refresh(workspace)

            # Start workspace setup in background
            asyncio.create_task(self._setup_workspace(db, workspace))

            return workspace

        except Exception as e:
            logger.error(f"Error creating workspace: {e}")
            if 'workspace' in locals():
                workspace.status = WorkspaceStatus.ERROR
                workspace.error_message = str(e)
                db.commit()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create workspace: {str(e)}"
            )

    async def _setup_workspace(self, db: Session, workspace: GitWorkspace):
        try:
            workspace.status = WorkspaceStatus.BUILDING
            db.commit()

            success = await self.git_service.clone_repository(
                clone_url=workspace.fork_repo_clone_url,
                workspace_path=workspace.workspace_path,
                branch_name=workspace.branch_name,
                upstream_owner=workspace.upstream_repo_owner,
                upstream_repo=workspace.upstream_repo_name,
                upstream_branch=workspace.upstream_branch_name
            )

            if success:
                workspace.status = WorkspaceStatus.ACTIVE
                workspace.last_build_at = datetime.utcnow()

                logger.info(f"Successfully setup workspace {workspace.id}")

                asyncio.create_task(self._trigger_build(workspace))
            else:
                workspace.status = WorkspaceStatus.ERROR
                workspace.error_message = "Failed to clone repository"

        except Exception as e:
            logger.error(f"Error setting up workspace {workspace.id}: {e}")
            workspace.status = WorkspaceStatus.ERROR
            workspace.error_message = str(e)

        finally:
            workspace.updated_at = datetime.now()
            db.commit()

    async def _trigger_build(self, workspace: GitWorkspace):
        try:
            logger.info(f"Triggering build for workspace {workspace.id}")

            build_info = await self.build_service.start_build(
                workspace_id=workspace.id,
                workspace_path=workspace.workspace_path,
                pfs=workspace.default_pfs
            )

            logger.info(f"Successfully triggered build for workspace {workspace.id} with status {build_info.status}")
        except Exception as e:
            logger.error(f"Error triggering build for workspace {workspace.id}: {e}")

    def get_user_workspaces(self, db: Session, user_id: str) -> List[GitWorkspace]:
        return db.query(GitWorkspace).filter(
            GitWorkspace.user_id == user_id,
            GitWorkspace.status != WorkspaceStatus.DELETED
        ).order_by(GitWorkspace.created_at.desc()).all()

    def get_workspace_by_id(self, db: Session, workspace_id: str, user_id: str) -> GitWorkspace:
        """Get workspace by ID for a specific user"""
        workspace = db.query(GitWorkspace).filter(
            GitWorkspace.id == workspace_id,
            GitWorkspace.user_id == user_id,
            GitWorkspace.status != WorkspaceStatus.DELETED
        ).first()

        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found"
            )

        return workspace

    async def update_workspace(
        self,
        db: Session,
        workspace_id: str,
        user_id: str,
        update_data: WorkspaceUpdate
    ) -> GitWorkspace:
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)

        if update_data.title:
            workspace.title = update_data.title
            workspace.updated_at = datetime.now(datetime.timezone.utc)

        db.commit()
        db.refresh(workspace)
        return workspace

    async def delete_workspace(
        self,
        db: Session,
        workspace_id: str,
        user_id: str
    ) -> bool:
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)

        try:
            workspace.status = WorkspaceStatus.DELETED
            workspace.updated_at = datetime.now(datetime.timezone.utc)
            db.commit()

            await self.git_service.delete_workspace(workspace.workspace_path)

            logger.info(f"Successfully deleted workspace {workspace_id}")
            return True

        except Exception as e:
            logger.error(f"Error deleting workspace {workspace_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to delete workspace: {str(e)}"
            )

    async def get_workspace_status(
        self,
        db: Session,
        workspace_id: str,
        user_id: str
    ) -> Dict[str, Any]:
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)

        if workspace.status != WorkspaceStatus.ACTIVE:
            return {
                "workspace_status": workspace.status.value,
                "error_message": workspace.error_message,
                "git_status": None
            }

        try:
            git_status = await self.git_service.get_git_status(workspace.workspace_path)
            
            return {
                "workspace_status": workspace.status.value,
                "error_message": workspace.error_message,
                "git_status": git_status
            }

        except Exception as e:
            logger.error(f"Error getting git status for workspace {workspace_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get workspace status: {str(e)}"
            )

    async def propose_changes(
        self,
        db: Session,
        workspace_id: str,
        user_id: str,
        commit_message: str,
        pr_title: str,
        pr_description: str,
        access_token: str
    ) -> Dict[str, Any]:
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)

        if workspace.status != WorkspaceStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace is not active"
            )

        try:
            git_status = await self.git_service.get_git_status(workspace.workspace_path)
            
            if git_status['is_clean']:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No changes to commit"
                )

            stdout, stderr, returncode = self.git_service._run_git_command(
                ["git", "add", "."],
                cwd=workspace.workspace_path
            )

            if returncode != 0:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to stage changes: {stderr}"
                )

            stdout, stderr, returncode = self.git_service._run_git_command(
                ["git", "commit", "-m", commit_message],
                cwd=workspace.workspace_path
            )

            if returncode != 0:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to commit changes: {stderr}"
                )

            stdout, stderr, returncode = self.git_service._run_git_command(
                ["git", "push", "origin", workspace.branch_name],
                cwd=workspace.workspace_path
            )

            if returncode != 0:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to push changes: {stderr}"
                )

            pr_data = {
                "title": pr_title,
                "body": pr_description,
                "head": f"{workspace.forked_repo_owner}:{workspace.branch_name}",
                "base": workspace.upstream_branch_name
            }

            pr_response = await self.github_service.create_pull_request(
                access_token=access_token,
                upstream_owner=workspace.upstream_repo_owner,
                upstream_repo=workspace.upstream_repo_name,
                pr_data=pr_data
            )

            return {
                "commit_sha": stdout.strip() if stdout else None,
                "pull_request": pr_response
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error proposing changes for workspace {workspace_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to propose changes: {str(e)}"
            )
    async def get_build_status(
        self,
        db: Session,
        workspace_id: str,
        user_id: str
    ) -> Dict[str, Any]:
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)
        
        build_status = self.build_service.get_build_status(workspace_id)
        
        return {
            "workspace_id": workspace_id,
            "workspace_status": workspace.status.value,
            "build_status": build_status
        }

    async def start_manual_build(
        self,
        db: Session,
        workspace_id: str,
        user_id: str,
        pfs: Optional[str] = None
    ) -> Dict[str, Any]:
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)
        
        if workspace.status != WorkspaceStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace is not active"
            )

        try:
            build_info = await self.build_service.start_build(
                workspace_path=workspace.workspace_path,
                workspace_id=workspace_id,
                pfs=pfs
            )
            
            # Update last build time
            workspace.last_build_at = datetime.utcnow()
            workspace.updated_at = datetime.utcnow()
            db.commit()

            return {
                "workspace_id": workspace_id,
                "build_started": True,
                "build_type": build_info.build_type,
                "pfs": build_info.pfs,
                "status": build_info.status.value
            }

        except Exception as e:
            logger.error(f"Error starting manual build for workspace {workspace_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to start build: {str(e)}"
            )

    async def cancel_build(
        self,
        db: Session,
        workspace_id: str,
        user_id: str
    ) -> Dict[str, Any]:
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)
        
        if workspace.status != WorkspaceStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace is not active"
            )
        
        cancelled = await self.build_service.cancel_build(workspace_id)
        
        return {
            "workspace_id": workspace_id,
            "build_cancelled": cancelled
        }

workspace_service = WorkspaceService()