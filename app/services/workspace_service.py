import logging
import shutil
from datetime import datetime
from typing import Any

import git
from ceos_ard_cli.schema import PFS_DOCUMENT
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from strictyaml import YAMLValidationError, as_document, load

from app.config import settings
from app.models.workspace import GitWorkspace, PullRequestStatus, WorkspaceStatus
from app.schemas.workspace import CreatePFSRequest, ProposalRequest, WorkspaceCreate, WorkspaceUpdate
from app.services.build_service import BuildService
from app.services.git_service import GitService
from app.services.github_service import GitHubService
from app.utils.git_utils import format_pr_response, get_repo_changes

from ..utils.validation import normalize_workspace_path

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
                username=username, access_token=access_token, upstream_owner=settings.CEOS_ARD_ORG, upstream_repo=settings.CEOS_ARD_REPO
            )

            if was_created:
                logger.info(f"Created new fork for user {username}")

            # Create workspace record in database
            workspace = GitWorkspace(
                user_id=user_id,
                pfs=workspace_data.pfs,
                title=workspace_data.title,
                description=workspace_data.description,
                fork_repo_owner=fork_repo["owner"]["login"],
                fork_repo_name=fork_repo["name"],
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
                workspace_path=workspace.abs_path,
                branch_name=workspace.branch_name,
                upstream_repo=settings.CEOS_ARD_REPO,
                upstream_owner=settings.CEOS_ARD_ORG,
                upstream_branch=settings.CEOS_ARD_BRANCH,
            )

            if success:
                workspace.status = WorkspaceStatus.ACTIVE
                db.commit()
                db.refresh(workspace)

                logger.info(f"Successfully setup workspace {workspace.id}")
            else:
                raise Exception("Failed to setup workspace")

        except Exception as e:
            logger.error(f"Error setting up workspace {workspace.id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to setup workspace: {str(e)}") from e

    async def get_user_workspaces(self, db: Session, user_id: str, access_token: str) -> list[GitWorkspace]:
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
                if workspace.pull_request_status == PullRequestStatus.OPEN and workspace.pull_request_number:
                    pull_request = await self.github_service.get_pull_request(
                        access_token=access_token,
                        repo=settings.CEOS_ARD_REPO,
                        owner=settings.CEOS_ARD_ORG,
                        number=workspace.pull_request_number,
                    )

                    if pull_request is not None:
                        workspace.pull_request_status = pull_request["state"].upper()
                        workspace.pull_request_status_last_updated_at = datetime.now()
                        workspace.status = (
                            WorkspaceStatus.ARCHIVED if pull_request["state"] == "closed" or pull_request["state"] == "merged" else workspace.status
                        )
                        workspace.updated_at = datetime.now()
                        db.add(workspace)

            db.commit()

            return workspaces

        except Exception as e:
            logger.error(f"Error getting user workspaces: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get user workspaces: {str(e)}") from e

    def get_workspace_by_id(self, db: Session, workspace_id: str, user_id: str) -> GitWorkspace:
        try:
            if not workspace_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

            if not user_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

            query = db.query(GitWorkspace).filter(GitWorkspace.id == workspace_id, GitWorkspace.user_id == user_id)

            workspace = query.first()
            if not workspace:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

            return workspace
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get workspace: {str(e)}") from e

    async def get_workspace(self, db: Session, user_id: str, workspace_id: str, access_token: str) -> GitWorkspace | None:
        try:
            workspace = self.get_workspace_by_id(db, workspace_id, user_id)

            if not access_token:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Access token is required")

            # Update pull request status if needed
            if workspace.pull_request_number and workspace.pull_request_status == PullRequestStatus.OPEN:
                pull_request = await self.github_service.get_pull_request(
                    access_token=access_token,
                    repo=settings.CEOS_ARD_REPO,
                    owner=settings.CEOS_ARD_ORG,
                    number=workspace.pull_request_number,
                )

                if pull_request is not None:
                    workspace.pull_request_status = pull_request["state"].upper()
                    workspace.pull_request_status_last_updated_at = datetime.now()
                    workspace.status = (
                        WorkspaceStatus.ARCHIVED if pull_request["state"] == "closed" or pull_request["state"] == "merged" else workspace.status
                    )

                    db.add(workspace)
                    db.commit()
                    db.refresh(workspace)

            return workspace

        except Exception as e:
            logger.error(f"Error getting user workspace: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get user workspace: {str(e)}") from e

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
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one of description, title, or status must be provided")

            if "status" in update_dict and isinstance(update_dict["status"], str):
                update_dict["status"] = update_dict["status"].upper()

            # Handle archiving - set timestamps when status changes to ARCHIVED
            if "status" in update_dict and update_dict["status"] == WorkspaceStatus.ARCHIVED.value.upper():
                if workspace.status != WorkspaceStatus.ARCHIVED:
                    archived_at = datetime.utcnow()
                    update_dict["archived_at"] = archived_at
                    logger.info(f"Archiving workspace {workspace_id}, deletion scheduled for 1 month from now")

            # Handle reactivation - clear timestamps when status changes from ARCHIVED to ACTIVE
            if "status" in update_dict and update_dict["status"] == WorkspaceStatus.ACTIVE.value.upper():
                if workspace.status == WorkspaceStatus.ARCHIVED:
                    update_dict["archived_at"] = None
                    logger.info(f"Reactivating archived workspace {workspace_id}, clearing archival timestamp")

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

        if workspace.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not authorized to delete this workspace")

        try:
            if workspace.abs_path.exists():
                shutil.rmtree(workspace.abs_path)
                logger.info(f"Deleted workspace files at {workspace.abs_path}")
            else:
                logger.warning(f"Workspace path does not exist: {workspace.abs_path}")
            db.delete(workspace)
            db.commit()

            logger.info(f"Successfully deleted workspace {workspace_id} (title: {workspace.title})")
            return "Workspace deleted successfully"

        except Exception as e:
            db.rollback()
            logger.error(f"Error deleting workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete workspace: {str(e)}") from e

    async def get_workspace_pfs_types(self, db: Session, workspace_id: str, user_id: str) -> list[dict[str, Any]]:
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

        try:
            workspace = self.get_workspace_by_id(db, workspace_id, user_id)

            if not workspace.abs_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

            pfs_path = workspace.abs_path / "pfs"

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
                    validated_document = load(yaml_content, PFS_DOCUMENT(file=pfs_document_path.name, base_path=workspace.abs_path))
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

            if not workspace.abs_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

            if not create_pfs_request.id or not create_pfs_request.title:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PFS ID and title are required")

            new_pfs_path = workspace.abs_path / "pfs" / create_pfs_request.id

            if new_pfs_path.exists():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PFS already exists")

            new_pfs_path.mkdir(parents=True, exist_ok=True)

            try:
                if create_pfs_request.base_pfs:
                    base_pfs_path = workspace.abs_path / "pfs" / create_pfs_request.base_pfs
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

                repo = git.Repo(workspace.abs_path)

                # Add changes to the repository
                try:
                    repo.git.add(normalize_workspace_path(new_pfs_path, workspace.abs_path, absolute=False))
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

    async def get_proposal_changes(self, db: Session, access_token: str, workspace_id: str, user_id: str) -> dict[str, Any] | None:
        try:
            workspace = await self.get_workspace(db, user_id, workspace_id, access_token=access_token)

            if not workspace.abs_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

            if not workspace.pull_request_number:
                return None

            pull_request = await self.github_service.get_pull_request(
                access_token=access_token,
                owner=settings.CEOS_ARD_ORG,
                repo=settings.CEOS_ARD_REPO,
                number=workspace.pull_request_number,
            )

            if not pull_request:
                return None

            commits = await self.github_service.get_pull_request_commits(
                access_token=access_token,
                owner=settings.CEOS_ARD_ORG,
                repo=settings.CEOS_ARD_REPO,
                number=workspace.pull_request_number,
            )

            pull_request_status = pull_request["state"]
            workspace.pull_request_status = pull_request_status.upper()
            workspace.pull_request_status_last_updated_at = datetime.now()
            workspace.status = (
                WorkspaceStatus.ARCHIVED
                if pull_request_status in [PullRequestStatus.CLOSED.value, PullRequestStatus.MERGED.value]
                else workspace.status
            )

            db.commit()

            return format_pr_response(pull_request, commits)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting proposal changes for workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get proposal changes: {str(e)}") from e

    async def propose_changes(self, db: Session, workspace_id: str, user_id: str, access_token: str, propose_data: ProposalRequest) -> dict[str, Any]:
        workspace = await self.get_workspace(db, user_id, workspace_id, access_token)

        if not workspace.abs_path.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

        if workspace.status == WorkspaceStatus.ARCHIVED:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot propose changes for an archived workspace")

        if workspace.pull_request_status in [PullRequestStatus.MERGED, PullRequestStatus.CLOSED]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Pull request is already {workspace.pull_request_status.value}; cannot propose changes",
            )
        try:
            changed_files = get_repo_changes(workspace.abs_path)

            if changed_files:
                repo = git.Repo(workspace.abs_path)
                commit_message = propose_data.commit_message or propose_data.title

                # Commit and push changes to the repository
                await self.git_service.commit_and_push_changes(repo, workspace.branch_name, commit_message)

            # Create or update pull request
            pr_response = await self._handle_pull_request(
                access_token=access_token,
                propose_data=propose_data,
                head_branch_name=workspace.branch_name,
                head_repo_owner=workspace.fork_repo_owner,
                pull_request_number=workspace.pull_request_number,
            )

            workspace.pull_request_number = pr_response["number"]
            workspace.pull_request_status = pr_response["state"].upper()
            workspace.pull_request_status_last_updated_at = datetime.now()
            db.commit()

            # Get commits for the pull request
            commits = await self.github_service.get_pull_request_commits(
                access_token=access_token,
                repo=settings.CEOS_ARD_REPO,
                owner=settings.CEOS_ARD_ORG,
                number=workspace.pull_request_number,
            )

            return format_pr_response(pr_response, commits)

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

    async def _handle_pull_request(
        self,
        access_token: str,
        pull_request_number: int | None,
        head_repo_owner: str,
        head_branch_name: str,
        propose_data,
    ):
        try:
            pr_data = {
                "title": propose_data.title,
                "draft": propose_data.draft,
                "body": propose_data.description,
                "base": settings.CEOS_ARD_BRANCH,
            }

            if pull_request_number is not None:
                # Update existing PR
                pr_data["state"] = propose_data.state
                return await self.github_service.update_pull_request(
                    access_token=access_token,
                    owner=settings.CEOS_ARD_ORG,
                    repo=settings.CEOS_ARD_REPO,
                    number=pull_request_number,
                    pr_data=pr_data,
                )
            else:
                # Create new PR
                pr_data["head"] = f"{head_repo_owner}:{head_branch_name}"

                return await self.github_service.create_pull_request(
                    pr_data=pr_data,
                    access_token=access_token,
                    owner=settings.CEOS_ARD_ORG,
                    repo=settings.CEOS_ARD_REPO,
                )
        except Exception as e:
            logger.error(f"Error handling pull request: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to handle pull request") from e
