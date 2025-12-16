import logging
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import git
from ceos_ard_cli.schema import PFS_DOCUMENT
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from strictyaml import YAMLValidationError, as_document, load

from app.config import settings
from app.models.workspace import GitWorkspace, PullRequestStatus, WorkspaceStatus
from app.schemas.workspace import CreatePFSRequest, WorkspaceCreate, WorkspaceUpdate
from app.services.build_service import BuildService
from app.services.git_service import GitService
from app.services.github_service import GitHubService

logger = logging.getLogger(__name__)


class WorkspaceService:
    def __init__(self):
        self.git_service = GitService()
        self.build_service = BuildService()
        self.github_service = GitHubService()

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
                status=WorkspaceStatus.ACTIVE,
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
                db.commit()
                db.refresh(workspace)

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

            await self.build_service.start_build(workspace_id=workspace.id, workspace_path=workspace.workspace_path, pfs=workspace.pfs)
        except Exception as e:
            logger.error(f"Error triggering build for workspace {workspace.id}: {e}")

    def get_user_workspaces(self, db: Session, user_id: str, access_token: str) -> list[GitWorkspace]:
        try:
            if not user_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

            workspaces = (
                db.query(GitWorkspace)
                .filter(GitWorkspace.user_id == user_id)
                .order_by(GitWorkspace.created_at.desc())
                .with_for_update(of=GitWorkspace)
                .all()
            )

            # Update pull request status if needed
            for workspace in workspaces:
                if (
                    workspace.pull_request_status != PullRequestStatus.MERGED
                    and workspace.pull_request_number
                    and workspace.pull_request_status_last_updated_at <= datetime.now() - timedelta(hours=2)
                ):
                    pull_request = self.github_service.get_pull_request(
                        access_token=access_token,
                        repo=workspace.fork_repo_name,
                        owner=workspace.fork_repo_owner,
                        number=workspace.pull_request_number,
                    )
                    if pull_request is not None:
                        workspace.pull_request_status = pull_request["state"]
                        workspace.pull_request_status_last_updated_at = datetime.now()
                        workspace.updated_at = datetime.now()
                        db.add(workspace)

            db.commit()

            return workspaces

        except Exception as e:
            logger.error(f"Error getting user workspaces: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get user workspaces: {str(e)}") from e

    def get_workspace_by_id(
        self, db: Session, workspace_id: str, user_id: str, *, access_token: str | None = None, check_pr: bool = False
    ) -> GitWorkspace:
        try:
            if not workspace_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

            if not user_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

            query = db.query(GitWorkspace).filter(GitWorkspace.id == workspace_id, GitWorkspace.user_id == user_id)

            if check_pr and access_token is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Access token is required")

            workspace = query.first()

            if not workspace:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

            # Update pull request status if needed
            if (
                check_pr
                and workspace.pull_request_number
                and workspace.pull_request_status != PullRequestStatus.MERGED
                and workspace.pull_request_status_last_updated_at <= datetime.now() - timedelta(hours=2)
            ):
                query = query.with_for_update(of=GitWorkspace)
                workspace = query.first()
                if workspace.pull_request_status_last_updated_at <= datetime.now() - timedelta(hours=2):
                    pull_request = self.github_service.get_pull_request(
                        access_token=access_token,
                        owner=workspace.fork_repo_owner,
                        repo=workspace.fork_repo_name,
                        number=workspace.pull_request_number,
                    )

                    if pull_request is not None:
                        workspace.pull_request_status = pull_request["state"]
                        workspace.pull_request_status_last_updated_at = datetime.now()
                        workspace.updated_at = datetime.now()
                        db.add(workspace)
                        db.commit()

            return workspace
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get workspace: {str(e)}") from e

    async def update_workspace(self, db: Session, workspace_id: str, user_id: str, update_data: WorkspaceUpdate) -> GitWorkspace:
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if update_data.status and update_data.status not in WorkspaceStatus:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid workspace status")

        try:
            workspace = self.get_workspace_by_id(db, workspace_id, user_id)

            if workspace.status == WorkspaceStatus.ARCHIVED and update_data.status == WorkspaceStatus.ACTIVE:
                if workspace.pull_request_status == PullRequestStatus.MERGED or workspace.pull_request_status == PullRequestStatus.CLOSED:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot reactivate an archived workspace with a merged or closed pull request"
                    )

            update_dict = update_data.model_dump(exclude_unset=True)

            if not update_dict:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one of description or title must be provided")

            if "status" in update_dict and isinstance(update_dict["status"], str):
                update_dict["status"] = update_dict["status"].upper()

            for key, value in update_dict.items():
                if hasattr(workspace, key):
                    setattr(workspace, key, value)

            db.commit()
            db.refresh(workspace)

            return workspace

        except Exception as e:
            logger.error(f"Error updating workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update workspace: {str(e)}") from e

    async def delete_workspace(self, db: Session, workspace_id: str, user_id: str) -> str:
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
            db.rollback()
            logger.error(f"Error deleting workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete workspace: {str(e)}") from e

    async def get_workspace_status(self, db: Session, workspace_id: str, user_id: str) -> dict[str, Any]:
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)
        workspace_path = Path(workspace.workspace_path)

        if workspace.status != WorkspaceStatus.ACTIVE:
            return {"workspace_status": workspace.status.value, "git_status": None}

        try:
            git_status = await self.git_service.get_git_status(workspace_path)

            return {"workspace_status": workspace.status.value, "git_status": git_status}

        except Exception as e:
            logger.error(f"Error getting git status for workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get workspace status: {str(e)}") from e

    async def get_workspace_pfs_types(self, db: Session, workspace_id: str, user_id: str) -> list[dict[str, Any]]:
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

        try:
            workspace = self.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = Path(workspace.workspace_path)

            if not workspace_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

            pfs_path = workspace_path / "pfs"

            if not pfs_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PFS directory not found in workspace")

            pfs_types = []

            for pfs_dir in pfs_path.iterdir():
                if not pfs_dir.is_dir():
                    continue

                pfs_document_path = pfs_dir / "document.yaml"
                if not pfs_document_path.exists():
                    continue

                try:
                    yaml_content = pfs_document_path.read_text()
                    validated_document = load(yaml_content, PFS_DOCUMENT(file=pfs_document_path.name, base_path=workspace_path))
                    document_data = validated_document.data

                    if not document_data.get("id") or not document_data.get("title"):
                        logger.warning(f"Skipping PFS {pfs_dir.name} due to missing id or title in document.yaml")
                        continue
                    pfs_types.append(
                        {
                            "id": document_data["id"],
                            "name": document_data["title"],
                        }
                    )
                except YAMLValidationError as e:
                    logger.error(f"Invalid YAML content in {pfs_document_path}: {e}")
                    continue
                except Exception as e:
                    logger.error(f"Error reading PFS document {pfs_document_path}: {e}")
                    continue

            return pfs_types
        except HTTPException:
            raise
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

            try:
                if create_pfs_request.base_pfs:
                    base_pfs_path = workspace_path / "pfs" / create_pfs_request.base_pfs
                    if not base_pfs_path.exists():
                        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Base PFS not found")
                    shutil.copytree(base_pfs_path, new_pfs_path, dirs_exist_ok=True)

                    documents_path = new_pfs_path / "document.yaml"
                    if documents_path.exists():
                        yaml_content = documents_path.read_text()
                        validated_document = load(yaml_content)
                        documents_data = validated_document.data
                    else:
                        documents_data = {
                            "id": create_pfs_request.id,
                            "title": create_pfs_request.title,
                            "version": create_pfs_request.version or "1.0-draft",
                        }
                else:
                    # Handle case when no base_pfs is provided
                    documents_path = new_pfs_path / "document.yaml"
                    documents_data = {
                        "id": create_pfs_request.id,
                        "title": create_pfs_request.title,
                        "version": create_pfs_request.version or "1.0-draft",
                    }

                update_data = create_pfs_request.model_dump(
                    include={"id", "title", "version", "applies_to", "introduction", "type"}, exclude_unset=True
                )

                documents_data.update(update_data)
                yaml_document = as_document(documents_data)
                documents_path.write_text(yaml_document.as_yaml(), encoding="utf-8")

                logger.info(f"Successfully created PFS {create_pfs_request.id} for workspace {workspace_id}")

                repo = git.Repo(workspace_path)

                # Add changes to the repository
                try:
                    repo.git.add(str(new_pfs_path.relative_to(workspace_path)))
                except git.GitCommandError as e:
                    logger.error(f"Failed to stage changes for workspace {workspace_id}: {e}")
                    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to stage changes: {str(e)}") from e

                return {"id": create_pfs_request.id, "name": create_pfs_request.title}
            except YAMLValidationError as e:
                shutil.rmtree(new_pfs_path, ignore_errors=True)
                logger.error(f"Invalid YAML content for PFS {create_pfs_request.id}: {e}")
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid YAML content: {str(e)}") from e
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error creating PFS for workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create PFS: {str(e)}") from e

    async def propose_changes(
        self, db: Session, workspace_id: str, user_id: str, pr_title: str, pr_description: str, access_token: str
    ) -> dict[str, Any]:
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)
        workspace_path = Path(workspace.workspace_path)

        if not workspace_path.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

        if workspace.status != WorkspaceStatus.ACTIVE:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace is not active")

        if workspace.pull_request_status == PullRequestStatus.OPEN:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pull request is already open")

        if workspace.pull_request_status == PullRequestStatus.MERGED:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pull request is already merged")

        if workspace.pull_request_status == PullRequestStatus.CLOSED:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pull request is already closed")

        try:
            git_status = await self.git_service.get_git_status(workspace_path)

            if git_status["is_clean"]:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No changes to commit")

            repo = git.Repo(workspace.workspace_path)

            # Add changes to the repository
            try:
                repo.git.add(".")
                logger.info(f"Staged changes for workspace {workspace_id}")
            except git.GitCommandError as e:
                logger.error(f"Failed to stage changes for workspace {workspace_id}: {e}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to stage changes: {str(e)}") from e

            # Commit changes to the repository
            try:
                repo.index.commit(pr_description)
                logger.info(f"Committed changes for workspace {workspace_id}")
            except git.GitCommandError as e:
                logger.error(f"Failed to commit changes for workspace {workspace_id}: {e}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to commit changes: {str(e)}") from e

            # Push changes to the remote repository
            try:
                origin = repo.remote("origin")
                push_info = origin.push(workspace.branch_name)

                if push_info and push_info[0].flags & git.push_info.ERROR:
                    error_msg = f"Push failed with flags: {push_info[0].flags}"
                    if push_info[0].summary:
                        error_msg += f", summary: {push_info[0].summary}"
                    logger.error(f"Failed to push changes for workspace {workspace_id}: {error_msg}")
                    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to push changes: {error_msg}")

                logger.info(f"Pushed changes for workspace {workspace_id} to branch {workspace.branch_name}")

            except git.GitCommandError as e:
                logger.error(f"Failed to push changes for workspace {workspace_id}: {e}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to push changes: {str(e)}") from e

            except git.InvalidGitRepositoryError as e:
                logger.error(f"Failed to push changes for workspace {workspace_id}: {e}")
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to push changes: {str(e)}") from e

            # Create a pull request
            logger.info(f"Creating pull request for workspace {workspace_id}")

            pr_data = {
                "title": pr_title,
                "body": pr_description,
                "head": f"{workspace.fork_repo_owner}:{workspace.branch_name}",
                "base": settings.CEOS_ARD_MAIN_BRANCH,
            }

            pr_response = await self.github_service.create_pull_request(
                access_token=access_token, upstream_owner=settings.CEOS_ARD_OWNER, upstream_repo=settings.CEOS_ARD_REPO, pr_data=pr_data
            )

            workspace.pull_request_number = pr_response["number"]
            workspace.pull_request_status = pr_response["state"]
            workspace.pull_request_status_last_updated_at = datetime.now()
            db.commit()

            return {
                "title": pr_title,
                "description": pr_description,
                "url": pr_response["html_url"],
                "number": pr_response["number"],
                "state": pr_response["state"],
                "created_at": pr_response["created_at"],
                "updated_at": pr_response["updated_at"],
                "author": pr_response["user"]["login"],
            }

        except HTTPException:
            raise
        except git.InvalidGitRepositoryError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Git repository") from e
        except git.GitCommandError as e:
            logger.error(f"Error proposing changes for workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to propose changes: {str(e)}") from e
        except Exception as e:
            logger.error(f"Error proposing changes for workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to propose changes: {str(e)}") from e
