import asyncio
import logging
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from ruamel.yaml import YAML
from sqlalchemy.orm import Session

from app.config import settings
from app.models.workspace import GitWorkspace, PullRequestStatus, WorkspaceStatus
from app.schemas.workspace import CreatePFSRequest, WorkspaceCreate, WorkspaceUpdate
from app.services.build_service import build_service
from app.services.git_service import git_service
from app.services.github_service import github_service

logger = logging.getLogger(__name__)


class WorkspaceService:
    def __init__(self):
        self.git_service = git_service
        self.github_service = github_service
        self.build_service = build_service

    async def create_workspace(self, db: Session, workspace_data: WorkspaceCreate, user_id: str, username: str, access_token: str) -> GitWorkspace:
        if not workspace_data.title:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Title is required")

        try:
            logger.info(f"Checking fork for user {username}")
            fork_repo, was_created = await self.github_service.get_or_create_fork(
                username=username, access_token=access_token, upstream_owner=settings.CEOS_ARD_OWNER, upstream_repo=settings.CEOS_ARD_REPO
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
                fork_repo_owner=fork_repo["owner"]["login"],
                fork_repo_name=fork_repo["name"],
                branch_name=branch_name,
                workspace_path=workspace_path,
                status=WorkspaceStatus.CREATING,
            )

            db.add(workspace)
            db.commit()
            db.refresh(workspace)

            await self._setup_workspace(db, workspace, clone_url=fork_repo["clone_url"])

            return workspace

        except Exception as e:
            logger.error(f"Error creating workspace: {e}")
            db.rollback()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create workspace: {str(e)}") from e

    async def _setup_workspace(self, db: Session, workspace: GitWorkspace, clone_url: str):
        try:
            workspace.status = WorkspaceStatus.BUILDING
            db.commit()

            success = await self.git_service.clone_repository(
                clone_url=clone_url,
                workspace_path=workspace.workspace_path,
                branch_name=workspace.branch_name,
                upstream_repo=settings.CEOS_ARD_REPO,
                upstream_owner=settings.CEOS_ARD_OWNER,
                upstream_branch=settings.CEOS_ARD_MAIN_BRANCH,
            )

            if success:
                workspace.status = WorkspaceStatus.ACTIVE
                workspace.last_build_at = datetime.utcnow()

                logger.info(f"Successfully setup workspace {workspace.id}")

                if workspace.pfs is not None and len(workspace.pfs) > 0:
                    await self._trigger_build(workspace)
            else:
                raise Exception("Failed to setup workspace")

        except Exception as e:
            logger.error(f"Error setting up workspace {workspace.id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to setup workspace: {str(e)}") from e

    async def _trigger_build(self, workspace: GitWorkspace):
        try:
            logger.info(f"Triggering build for workspace {workspace.id}")

            build_info = await self.build_service.start_build(workspace_id=workspace.id, workspace_path=workspace.workspace_path, pfs=workspace.pfs)

            logger.info(f"Successfully triggered build for workspace {workspace.id} with status {build_info.status}")
        except Exception as e:
            logger.error(f"Error triggering build for workspace {workspace.id}: {e}")

    def get_user_workspaces(self, db: Session, user_id: str) -> list[GitWorkspace]:
        return (
            db.query(GitWorkspace)
            .filter(GitWorkspace.user_id == user_id, GitWorkspace.status != WorkspaceStatus.ARCHIVED)
            .order_by(GitWorkspace.created_at.desc())
            .all()
        )

    def get_workspace_by_id(
        self, db: Session, workspace_id: str, user_id: str, *, access_token: str | None = None, check_pr: bool = False
    ) -> GitWorkspace:
        try:
            if not workspace_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

            if not user_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

            if check_pr and access_token is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Access token is required")

            workspace = (
                db.query(GitWorkspace)
                .filter(GitWorkspace.id == workspace_id, GitWorkspace.user_id == user_id, GitWorkspace.status != WorkspaceStatus.ARCHIVED)
                .first()
            )

            if not workspace or workspace.status == WorkspaceStatus.ARCHIVED:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

            if (
                workspace.pull_request_status != PullRequestStatus.MERGED
                and workspace.pull_request_number
                and check_pr
                and workspace.pull_request_status_last_updated_at <= datetime.now() - timedelta(hours=2)
            ):
                pull_request = self.github_service.get_pull_request(
                    access_token=access_token,
                    owner=workspace.fork_repo_owner,
                    repo=workspace.fork_repo_name,
                    number=workspace.pull_request_number,
                )
                db.query(GitWorkspace).filter(GitWorkspace.id == workspace_id).update(
                    {
                        GitWorkspace.pull_request_status: pull_request["state"],
                        GitWorkspace.pull_request_status_last_updated_at: datetime.now(),
                        GitWorkspace.updated_at: datetime.now(),
                    },
                    synchronize_session=False,
                )
                db.commit()

            return workspace

        except Exception as e:
            logger.error(f"Error getting workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get workspace: {str(e)}") from e

    async def update_workspace(self, db: Session, workspace_id: str, user_id: str, update_data: WorkspaceUpdate) -> GitWorkspace:
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        update_dict = update_data.model_dump(exclude_unset=True)

        if not update_dict:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one of description or title must be provided")

        db.query(GitWorkspace).filter(GitWorkspace.id == workspace_id, GitWorkspace.user_id == user_id).update(update_dict, synchronize_session=False)

        db.commit()
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)
        return workspace

    async def delete_workspace(self, db: Session, workspace_id: str, user_id: str) -> bool:
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        workspace = self.get_workspace_by_id(db, workspace_id, user_id)
        workspace_path = str(workspace.workspace_path)

        if workspace.status != WorkspaceStatus.ACTIVE:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace is not active")

        if workspace.status == WorkspaceStatus.ARCHIVED:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace is already deleted")

        if not Path(workspace_path).exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

        if workspace.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not authorized to delete this workspace")

        try:
            workspace.status = WorkspaceStatus.ARCHIVED
            db.commit()

            if Path(workspace_path).exists():
                shutil.rmtree(workspace_path)
                logger.info(f"Deleted workspace at {workspace_path}")
            else:
                logger.warning(f"Workspace at {workspace_path} does not exist")

            logger.info(f"Successfully deleted workspace {workspace_id}")
            return "Workspace deleted successfully"

        except Exception as e:
            logger.error(f"Error deleting workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete workspace: {str(e)}") from e

    async def get_workspace_status(self, db: Session, workspace_id: str, user_id: str) -> dict[str, Any]:
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)

        if workspace.status != WorkspaceStatus.ACTIVE:
            return {"workspace_status": workspace.status.value, "git_status": None}

        try:
            git_status = await self.git_service.get_git_status(workspace.workspace_path)

            return {"workspace_status": workspace.status.value, "git_status": git_status}

        except Exception as e:
            logger.error(f"Error getting git status for workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get workspace status: {str(e)}") from e

    async def get_workspace_pfs_types(self, db: Session, workspace_id: str, user_id: str) -> dict[str, Any]:
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        try:
            workspace = self.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = Path(workspace.workspace_path)

            if not workspace_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

            pfs_path = workspace_path / "pfs"

            pfs_types = []
            yaml = YAML(typ="safe")

            for pfs in pfs_path.iterdir():
                if pfs.is_dir():
                    document_path = pfs / "document.yaml"
                    if document_path.exists():
                        with open(document_path, encoding="utf-8") as f:
                            document = yaml.load(f)
                            pfs_types.append(
                                {
                                    "id": document["id"],
                                    "name": document["title"],
                                }
                            )

            return pfs_types

        except Exception as e:
            logger.error(f"Error getting PFS types for workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get PFS types: {str(e)}") from e

    async def create_workspace_pfs(self, db: Session, workspace_id: str, user_id: str, create_pfs_request: CreatePFSRequest):
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        try:
            workspace = self.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = Path(workspace.workspace_path)

            if not workspace_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

            if not create_pfs_request.id or not create_pfs_request.title:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PFS ID and title are required")

            new_pfs_path = workspace_path / "pfs" / create_pfs_request.id

            if new_pfs_path.exists():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PFS already exists")

            new_pfs_path.mkdir(parents=True, exist_ok=True)

            base_pfs_path = workspace_path / "pfs" / create_pfs_request.base_pfs if create_pfs_request.base_pfs else None

            if base_pfs_path and not base_pfs_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Base PFS not found")
            if base_pfs_path:
                logger.info(f"Copying base PFS from {base_pfs_path} to {new_pfs_path}")
                shutil.copytree(base_pfs_path, new_pfs_path, dirs_exist_ok=True)

            yaml = YAML()
            yaml.preserve_quotes = True

            documents_path = new_pfs_path / "document.yaml"
            with open(documents_path) as f:
                document = yaml.load(f)

            document.update(
                create_pfs_request.model_dump(include={"id", "title", "version", "applies_to", "introduction", "type"}, exclude_unset=True)
            )

            with open(documents_path, "w") as f:
                yaml.dump(document, f)

            logger.info(f"Successfully created PFS {create_pfs_request.id} for workspace {workspace_id}")

            return {"id": create_pfs_request.id, "name": create_pfs_request.title}
        except Exception as e:
            logger.error(f"Error creating PFS for workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create PFS: {str(e)}") from e

    async def propose_changes(
        self, db: Session, workspace_id: str, user_id: str, pr_title: str, pr_description: str, access_token: str
    ) -> dict[str, Any]:
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)

        if workspace.status != WorkspaceStatus.ACTIVE:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace is not active")

        try:
            git_status = await self.git_service.get_git_status(workspace.workspace_path)

            if git_status["is_clean"]:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No changes to commit")

            stdout, stderr, returncode = self.git_service._run_git_command(["git", "add", "."], cwd=workspace.workspace_path)

            if returncode != 0:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to stage changes: {stderr}")

            stdout, stderr, returncode = self.git_service._run_git_command(["git", "commit", "-m", pr_description], cwd=workspace.workspace_path)

            if returncode != 0:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to commit changes: {stderr}")

            stdout, stderr, returncode = self.git_service._run_git_command(
                ["git", "push", "origin", workspace.branch_name], cwd=workspace.workspace_path
            )

            if returncode != 0:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to push changes: {stderr}")

            pr_data = {
                "title": pr_title,
                "body": pr_description,
                "head": f"{workspace.fork_repo_owner}:{workspace.branch_name}",
                "base": workspace.upstream_branch_name,
            }

            pr_response = await self.github_service.create_pull_request(
                access_token=access_token, upstream_owner=settings.CEOS_ARD_OWNER, upstream_repo=settings.CEOS_ARD_REPO, pr_data=pr_data
            )

            workspace.pull_request_number = pr_response["number"]
            workspace.pull_request_status = pr_response["state"]
            workspace.pull_request_status_last_updated_at = datetime.now()
            db.commit()

            return {"commit_sha": stdout.strip() if stdout else None, "pull_request": pr_response}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error proposing changes for workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to propose changes: {str(e)}") from e

    async def get_build_status(self, db: Session, workspace_id: str, user_id: str) -> dict[str, Any]:
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)

        build_status = self.build_service.get_build_status(workspace_id)

        return {"workspace_id": workspace_id, "workspace_status": workspace.status.value, "build_status": build_status}

    async def start_manual_build(self, db: Session, workspace_id: str, user_id: str, pfs: str | None = None) -> dict[str, Any]:
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)

        if workspace.status != WorkspaceStatus.ACTIVE:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace is not active")

        try:
            build_info = await self.build_service.start_build(workspace_path=workspace.workspace_path, workspace_id=workspace_id, pfs=pfs)

            # Update last build time
            workspace.last_build_at = datetime.now()
            db.commit()

            return {
                "workspace_id": workspace_id,
                "build_started": True,
                "build_type": build_info.build_type,
                "pfs": build_info.pfs,
                "status": build_info.status.value,
            }

        except Exception as e:
            logger.error(f"Error starting manual build for workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to start build: {str(e)}") from e

    async def cancel_build(self, db: Session, workspace_id: str, user_id: str) -> dict[str, Any]:
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)

        if workspace.status != WorkspaceStatus.ACTIVE:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace is not active")

        cancelled = await self.build_service.cancel_build(workspace_id)

        return {"workspace_id": workspace_id, "build_cancelled": cancelled}


workspace_service = WorkspaceService()
