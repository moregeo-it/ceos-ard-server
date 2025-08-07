import os
import uuid
import shutil
import logging
import asyncio

from ruamel.yaml import YAML
from datetime import datetime
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from typing import List, Dict, Any, Optional

from app.config import settings
from app.services.git_service import git_service
from app.schemas.workspace import CreatePFSRequest
from app.services.build_service import build_service
from app.services.github_service import github_service
from app.schemas.workspace import WorkspaceCreate, WorkspaceUpdate
from app.models.workspace import GitWorkspace, WorkspaceStatus, PullRequestStatus

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
        upstream_repo_name = settings.CEOS_ARD_REPO
        upstream_repo_owner = settings.CEOS_ARD_OWNER
        upstream_branch_name = settings.CEOS_ARD_MAIN_BRANCH

        if not workspace_data.pfs:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PFS is required")

        if not workspace_data.title:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Title is required")

        try:
            logger.info(f"Checking fork for user {username}")
            fork_repo, was_created = await self.github_service.get_or_create_fork(
                username=username,
                access_token=access_token,
                upstream_owner=upstream_repo_owner,
                upstream_repo=upstream_repo_name
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
                pfs=workspace_data.pfs,
                title=workspace_data.title,
                description=workspace_data.description,
                upstream_repo_owner=upstream_repo_owner,
                upstream_repo_name=upstream_repo_name,
                forked_repo_owner=fork_repo['owner']['login'],
                forked_repo_name=fork_repo['name'],
                fork_repo_clone_url=fork_repo['clone_url'],
                branch_name=branch_name,
                upstream_branch_name=upstream_branch_name or "main",
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
                pfs=workspace.pfs
            )

            logger.info(f"Successfully triggered build for workspace {workspace.id} with status {build_info.status}")
        except Exception as e:
            logger.error(f"Error triggering build for workspace {workspace.id}: {e}")

    def get_user_workspaces(self, db: Session, user_id: str) -> List[GitWorkspace]:
        return db.query(GitWorkspace).filter(
            GitWorkspace.user_id == user_id,
            GitWorkspace.status != WorkspaceStatus.DELETED
        ).order_by(GitWorkspace.created_at.desc()).all()

    def get_workspace_by_id(self, db: Session, workspace_id: str, user_id: str, access_token: Optional[str] = None, check_pr: bool = False) -> GitWorkspace:
        workspace = db.query(GitWorkspace).filter(
            GitWorkspace.id == workspace_id,
            GitWorkspace.user_id == user_id,
            GitWorkspace.status != WorkspaceStatus.DELETED
        ).first()

        if not workspace or workspace.status == WorkspaceStatus.DELETED:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found"
            )
        
        if workspace.pull_request_status == PullRequestStatus.OPEN and workspace.pull_request_number and check_pr:
            pull_request = self.github_service.get_pull_request(
                access_token=access_token,
                owner=workspace.forked_repo_owner,
                repo=workspace.forked_repo_name,
                number=workspace.pull_request_number
            )
            db.query(GitWorkspace).filter(
                GitWorkspace.id == workspace_id
            ).update({
                GitWorkspace.pull_request_status: pull_request['state'],
                GitWorkspace.pull_request_url: pull_request['html_url'],
                GitWorkspace.updated_at: datetime.now()
            }, synchronize_session=False)
            db.commit()

        return workspace

    async def update_workspace(
        self,
        db: Session,
        workspace_id: str,
        user_id: str,
        update_data: WorkspaceUpdate
    ) -> GitWorkspace:
        if not workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace ID is required"
            )

        update_dict = update_data.dict(exclude_unset=True)

        if not update_dict:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one of description, title, or pfs must be provided"
            )

        db.query(GitWorkspace).filter(
            GitWorkspace.id == workspace_id,
            GitWorkspace.user_id == user_id
        ).update(update_dict, synchronize_session=False)

        db.commit()
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)
        return workspace

    async def delete_workspace(
        self,
        db: Session,
        workspace_id: str,
        user_id: str
    ) -> bool:
        if not workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace ID is required"
            )

        workspace = self.get_workspace_by_id(db, workspace_id, user_id)
        workspace_path = str(workspace.workspace_path)

        if workspace.status != WorkspaceStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace is not active"
            )
        
        if workspace.status == WorkspaceStatus.DELETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace is already deleted"
            )
        
        if not os.path.exists(workspace_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found"
            )
        
        if workspace.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to delete this workspace"
            )

        try:
            workspace.status = WorkspaceStatus.DELETED
            workspace.updated_at = datetime.now()
            db.commit()

            if os.path.exists(workspace_path):
                shutil.rmtree(workspace_path)
                logger.info(f"Deleted workspace at {workspace_path}")
            else:
                logger.warning(f"Workspace at {workspace_path} does not exist")

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
    async def get_workspace_pfs_types(
        self,
        db: Session,
        workspace_id: str,
        user_id: str
    ) -> Dict[str, Any]:
        if not workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace ID is required"
            )

        try:
            workspace = self.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = str(workspace.workspace_path)

            if not os.path.exists(workspace_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Workspace not found"
                )
            
            pfs_path = os.path.join(workspace_path, "pfs")

            pfs_types = pfs_types = [pfs for pfs in os.listdir(pfs_path) if os.path.isdir(os.path.join(pfs_path, pfs))]

            return pfs_types

        except Exception as e:
            logger.error(f"Error getting PFS types for workspace {workspace_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get PFS types: {str(e)}"
            )
        
    async def create_workspace_pfs(
        self,
        db: Session,
        workspace_id: str,
        user_id: str,
        create_pfs_request: CreatePFSRequest
    ):
        if not workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace ID is required"
            )

        try:
            workspace = self.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = str(workspace.workspace_path)

            if not os.path.exists(workspace_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Workspace not found"
                )

            new_pfs_path = os.path.join(workspace_path, "pfs", create_pfs_request.id)

            if os.path.exists(new_pfs_path):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="PFS already exists"
                )

            os.makedirs(new_pfs_path, exist_ok=True)

            base_pfs_path = os.path.join(workspace_path, "pfs", create_pfs_request.base_pfs) if create_pfs_request.base_pfs else None

            shutil.copytree(base_pfs_path, new_pfs_path, dirs_exist_ok=True)

            yaml = YAML()
            yaml.preserve_quotes = True

            documents_path = os.path.join(new_pfs_path, "document.yaml")
            with open(documents_path, "r") as f:
                base_document = yaml.load(f)

            document = base_document.copy()
            document["id"] = create_pfs_request.id
            document["title"] = create_pfs_request.title
            document["version"] = create_pfs_request.version
            document["applies_to"] = create_pfs_request.applies_to
            document["introduction"] = create_pfs_request.introduction

            with open(documents_path, "w") as f:
                yaml.dump(document, f)

            logger.info(f"Successfully created PFS {create_pfs_request.id} for workspace {workspace_id}")

            return {
                "id": create_pfs_request.id,
                "name": create_pfs_request.title 
            }
        except Exception as e:
            logger.error(f"Error creating PFS for workspace {workspace_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create PFS: {str(e)}"
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