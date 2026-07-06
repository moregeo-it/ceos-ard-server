import logging
import shutil
from datetime import UTC, datetime
from typing import Any

import pygit2
from ceos_ard_cli.schema import PFS_DOCUMENT
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from strictyaml import YAMLValidationError, as_document
from yaml import load as yaml_load

from app.config import settings
from app.models.user import User
from app.models.workspace import GitWorkspace, PullRequestStatus, WorkspaceStatus
from app.models.workspace_share import ShareStatus, WorkspaceShare
from app.schemas.workspace import CreatePFSRequest, Proposal, ProposalRequest, WorkspaceCreate, WorkspaceUpdate
from app.services.build_service import BuildService
from app.services.git_service import GitService
from app.services.github_service import GitHubService
from app.services.share_service import ROLE_RANK, ShareService
from app.utils.file_utils import create_folder
from app.utils.git_utils import get_repo, get_repo_changes
from app.utils.pfs_utils import PlainStringSafeLoader, build_default_pfs_document
from app.utils.validation import validate_pathname

logger = logging.getLogger(__name__)


class WorkspaceService:
    def __init__(self):
        self.git_service = GitService()
        self.build_service = BuildService()
        self.github_service = GitHubService()
        self.share_service = ShareService()

    async def create_workspace(self, db: Session, workspace_data: WorkspaceCreate, user: User) -> GitWorkspace:
        if not workspace_data.title:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Title is required")

        try:
            fork_repo = await self.github_service.fork(user=user, upstream_owner=settings.CEOS_ARD_ORG, upstream_repo=settings.CEOS_ARD_REPO)

            # Create workspace record in database
            workspace = GitWorkspace(
                user_id=user.id,
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
            workspace.viewer_role = "owner"
            workspace.owner_username = user.username

            # Clone the forked repository into the workspace directory
            success = await self.git_service.clone_repository(
                user=user,
                clone_url=fork_repo["clone_url"],
                workspace_path=workspace.abs_path,
                branch_name=workspace.branch_name,
                upstream_repo=settings.CEOS_ARD_REPO,
                upstream_owner=settings.CEOS_ARD_ORG,
                upstream_branch=settings.CEOS_ARD_BRANCH,
            )

            if success:
                db.commit()
                db.refresh(workspace)
                workspace.viewer_role = "owner"
                workspace.owner_username = user.username

                logger.info(f"Successfully setup workspace {workspace.id}")
            else:
                db.rollback()
                raise Exception("Failed to setup workspace")

            return workspace

        except Exception as e:
            logger.error(f"Error creating workspace: {e}")
            if workspace:
                db.rollback()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create workspace: {str(e)}") from e

    def get_user_workspaces(self, db: Session, user: User) -> list[GitWorkspace]:
        try:
            if not user.id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

            owned = (
                db.query(GitWorkspace)
                .filter(GitWorkspace.user_id == user.id)
                .order_by(GitWorkspace.created_at.desc())
                .with_for_update(of=GitWorkspace)
                .all()
            )
            for workspace in owned:
                workspace.viewer_role = "owner"
                workspace.owner_username = user.username

            shared_workspace_ids = [
                share.workspace_id
                for share in db.query(WorkspaceShare.workspace_id)
                .filter(WorkspaceShare.invitee_user_id == user.id, WorkspaceShare.status == ShareStatus.ACCEPTED)
                .all()
            ]
            shared = db.query(GitWorkspace).filter(GitWorkspace.id.in_(shared_workspace_ids)).all() if shared_workspace_ids else []
            for workspace in shared:
                workspace.viewer_role = self.share_service.resolve_role(db, workspace, user.id)
                workspace.owner_username = workspace.user.username if workspace.user else None

            return owned + shared

        except Exception as e:
            logger.error(f"Error getting user workspaces: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get user workspaces: {str(e)}") from e

    def get_workspace_by_id(self, db: Session, workspace_id: str, user_id: str, exists=True, min_role: str = "readonly") -> GitWorkspace:
        try:
            if not workspace_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")
            elif not user_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

            workspace = db.query(GitWorkspace).filter(GitWorkspace.id == workspace_id).first()
            if not workspace:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

            role = self.share_service.resolve_role(db, workspace, user_id)
            # Don't leak workspace existence to users who have no access to it at all.
            if role is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

            if ROLE_RANK[role] < ROLE_RANK[min_role]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to perform this action on this workspace"
                )

            if exists and not workspace.abs_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found on filesystem")
            elif exists and not workspace.abs_path.is_dir():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace is not a directory")

            workspace.viewer_role = role
            workspace.owner_username = workspace.user.username if workspace.user else None
            return workspace
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get workspace: {str(e)}") from e

    async def sync_workspace(
        self, db: Session, user_id: str, workspace_id: str, access_token: str, min_role: str = "readonly"
    ) -> GitWorkspace | None:
        try:
            workspace = self.get_workspace_by_id(db, workspace_id, user_id, min_role=min_role)

            if not access_token:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Access token is required")

            # Update pull request status if needed
            if workspace.pull_request_number:
                pull_request = await self.github_service.get_pull_request(
                    access_token=access_token,
                    repo=settings.CEOS_ARD_REPO,
                    owner=settings.CEOS_ARD_ORG,
                    number=workspace.pull_request_number,
                )

                if pull_request is not None:
                    pr_state = pull_request["state"]
                    workspace.pull_request_status = pr_state.upper()
                    workspace.pull_request_status_last_updated_at = datetime.now(UTC)

                    if pr_state in [PullRequestStatus.CLOSED.value, PullRequestStatus.MERGED.value]:
                        workspace.archived_at = datetime.now(UTC)
                        workspace.status = WorkspaceStatus.ARCHIVED

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
            workspace = self.get_workspace_by_id(db, workspace_id, user_id, min_role="owner")

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
                    archived_at = datetime.now(UTC)
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

        workspace = self.get_workspace_by_id(db, workspace_id, user_id, exists=False, min_role="owner")

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

    def get_workspace_commits(self, db: Session, workspace_id: str, user_id: str) -> list[pygit2.Commit]:
        # Commit history is exclusively surfaced in the Propose view, which is owner-only.
        workspace = self.get_workspace_by_id(db, workspace_id, user_id, min_role="owner")
        return self.git_service.get_commits(workspace.abs_path)

    async def get_workspace_pfs_types(self, db: Session, workspace_id: str, user_id: str) -> list[dict[str, Any]]:
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

        try:
            workspace = self.get_workspace_by_id(db, workspace_id, user_id)

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
                    document = yaml_load(pfs_document_path.read_text(encoding="utf-8"), Loader=PlainStringSafeLoader)
                    if not isinstance(document, dict):
                        logger.error(f"Invalid PFS document format in {pfs_document_path}")
                        continue
                    pfs_types.append(
                        {
                            "id": pfs_dir.name,
                            "title": document.get("title") or pfs_dir.name,
                        }
                    )
                except Exception as e:
                    logger.error(f"Error reading PFS document {pfs_document_path}: {e}")
                    continue

            return pfs_types
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting PFS types for workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get PFS types: {str(e)}") from e

    async def create_workspace_pfs(self, db: Session, workspace_id: str, user: User, request_data: CreatePFSRequest):
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")
        if not request_data.id or not request_data.title:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PFS ID and title are required")

        workspace = self.get_workspace_by_id(db, workspace_id, user.id, min_role="edit")
        repo = get_repo(workspace.abs_path)
        pfs_container = workspace.abs_path / "pfs"
        pfs_id = validate_pathname(request_data.id)
        pfs_path = pfs_container / pfs_id
        if pfs_path.exists():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PFS already exists")

        folder_details = create_folder(workspace.abs_path, pfs_id, pfs_path)

        documents_path = pfs_path / "document.yaml"
        pfs_schema = PFS_DOCUMENT(file=documents_path.name, base_path=workspace.abs_path)

        try:
            data = None
            if request_data.base:
                base_pfs_path = pfs_container / validate_pathname(request_data.base)
                if not base_pfs_path.exists():
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Base PFS not found")

                shutil.copytree(base_pfs_path, pfs_path, dirs_exist_ok=True)

                if documents_path.exists():
                    try:
                        data = yaml_load(documents_path.read_text(encoding="utf-8"), Loader=PlainStringSafeLoader)
                        # Ideally we would load from strictyaml, but it resolves references so we can't write it back.
                        # This means we will loose e.g. comments in the document.yaml file.
                        # Until ceos-ard-cli allows us to load unresolved documents, we have to keep it as is.
                        # data = strict_yaml_load(documents_path.read_text(encoding="utf-8"), pfs_schema).data
                    except YAMLValidationError as ye:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Failed to read base PFS document: {str(ye)}"
                        ) from ye

                    data.update(request_data.model_dump(include={"title", "version", "applies_to", "type"}, exclude_unset=True))

            if not isinstance(data, dict):
                data = build_default_pfs_document(
                    **request_data.model_dump(include={"title", "version", "applies_to", "type"}, exclude_unset=True),
                    author_username=user.full_name or user.username,
                )

            # Set default values if any is None
            default_document = build_default_pfs_document()
            for key, value in data.items():
                if value is None and key in default_document:
                    data[key] = default_document[key]

            try:
                yaml_content = as_document(data, pfs_schema)
                documents_path.write_text(yaml_content.as_yaml(), encoding="utf-8")

                logger.info(f"Successfully created PFS {pfs_id} for workspace {workspace_id}")
            except YAMLValidationError as ye:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Failed to write PFS document: {str(ye)}") from ye

            # Add changes to the repository
            try:
                # Add all files in the new PFS directory
                for file_path in pfs_path.rglob("*"):
                    if file_path.is_file():
                        rel_file = str(file_path.relative_to(workspace.abs_path)).replace("\\", "/")
                        repo.index.add(rel_file)
                repo.index.write()
            except pygit2.GitError as e:
                logger.error(f"Failed to stage changes for workspace {workspace_id}: {e}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to stage changes: {str(e)}") from e

            return folder_details
        except HTTPException:
            shutil.rmtree(pfs_path, ignore_errors=True)
            raise
        except Exception as e:
            shutil.rmtree(pfs_path, ignore_errors=True)
            logger.error(f"Error creating PFS {pfs_id} for workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create PFS: {str(e)}") from e

    async def get_proposal(self, db: Session, access_token: str, workspace_id: str, user_id: str) -> Proposal | None:
        # The proposal (PR) is exclusively surfaced in the Propose view, which is owner-only.
        workspace = self.get_workspace_by_id(db, workspace_id, user_id, min_role="owner")
        if not workspace.pull_request_number:
            return None

        try:
            pull_request = await self.github_service.get_pull_request(
                access_token=access_token,
                owner=settings.CEOS_ARD_ORG,
                repo=settings.CEOS_ARD_REPO,
                number=workspace.pull_request_number,
            )
            if not pull_request:
                return None

            pull_request_status = pull_request["state"]
            workspace.pull_request_status = pull_request_status.upper()
            workspace.pull_request_status_last_updated_at = datetime.now(UTC)

            if pull_request_status in [PullRequestStatus.CLOSED.value, PullRequestStatus.MERGED.value]:
                workspace.archived_at = datetime.now(UTC)
                workspace.status = WorkspaceStatus.ARCHIVED

            db.commit()

            return Proposal(
                number=pull_request["number"],
                url=pull_request["html_url"],
                title=pull_request["title"],
                state=pull_request["state"],
                draft=pull_request["draft"],
                description=pull_request["body"],
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting proposal changes for workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get proposal changes: {str(e)}") from e

    async def propose(self, db: Session, workspace_id: str, user: User, data: ProposalRequest) -> Proposal:
        workspace = await self.sync_workspace(db, user.id, workspace_id, user.access_token, min_role="owner")

        if workspace.pull_request_status == PullRequestStatus.MERGED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pull request is already merged, cannot propose further changes. Please create a new workspace.",
            )

        if data.state != "open":
            if workspace.status == WorkspaceStatus.ARCHIVED:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot make changes to an archived workspace. Please reactivate it first."
                )

            if workspace.pull_request_status == PullRequestStatus.CLOSED:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Pull request has been closed. Please reopen it before making further changes."
                )

        repo = get_repo(workspace.abs_path)
        try:
            changed_files = get_repo_changes(repo)
            if len(changed_files) > 0:
                # Commit and push changes to the repository
                await self.git_service.push(
                    repo=repo,
                    user=user,
                    branch_name=workspace.branch_name,
                )

            # Create or update pull request
            pr_response = await self._handle_pull_request(
                propose_data=data,
                access_token=user.access_token,
                head_branch_name=workspace.branch_name,
                head_repo_owner=workspace.fork_repo_owner,
                pull_request_number=workspace.pull_request_number,
            )

            if data.state == "open":
                workspace.archived_at = None
                workspace.status = WorkspaceStatus.ACTIVE
            workspace.pull_request_number = pr_response["number"]
            workspace.pull_request_status = pr_response["state"].upper()
            workspace.pull_request_status_last_updated_at = datetime.now(UTC)
            db.commit()

            return Proposal(
                number=pr_response["number"],
                url=pr_response["html_url"],
                title=pr_response["title"],
                state=pr_response["state"],
                draft=pr_response["draft"],
                description=pr_response["body"],
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error proposing changes for workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to propose changes: {str(e)}") from e

    async def _handle_pull_request(
        self,
        access_token: str,
        pull_request_number: int | None,
        head_repo_owner: str,
        head_branch_name: str,
        propose_data: ProposalRequest,
    ):
        try:
            pr_data = {
                "title": propose_data.title,
                "body": propose_data.description,
            }

            if pull_request_number is not None:
                # Update existing PR
                if propose_data.state:
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
                # Set the base branch
                pr_data["base"] = settings.CEOS_ARD_BRANCH
                # Allow CEOS-ARD maintainers to modify the PR
                pr_data["maintainer_can_modify"] = True
                return await self.github_service.create_pull_request(
                    pr_data=pr_data,
                    access_token=access_token,
                    owner=settings.CEOS_ARD_ORG,
                    repo=settings.CEOS_ARD_REPO,
                )
        except Exception as e:
            logger.error(f"Error handling pull request: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to handle pull request") from e
