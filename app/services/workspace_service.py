import asyncio
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
from app.schemas.workspace import CreatePFSRequest, Proposal, ProposalRequest, SyncResult, WorkspaceCreate, WorkspaceUpdate
from app.services.build_service import BuildService
from app.services.git_service import GitService, RemoteAccessError
from app.services.github_service import GitHubAPIError, GitHubService, head_repo_missing, pull_request_is_merged
from app.utils.file_utils import create_folder
from app.utils.git_utils import get_repo
from app.utils.locks import KeyedLocks
from app.utils.pfs_utils import PlainStringSafeLoader, build_default_pfs_document
from app.utils.validation import validate_pathname

logger = logging.getLogger(__name__)

# One lock per user, guarding fork repair: GitHub allows one fork per account per upstream, so
# all of a user's workspaces share it and recreating it is a per-user operation.
#
# ORDERING: acquire OUTSIDE git_service's per-workspace sync lock, never the reverse. That
# holds today only because repair runs after sync_with_origin has released its lock, on the way
# out via RemoteAccessError; reversing it anywhere deadlocks the event loop.
fork_lock = KeyedLocks()

# How long to wait for GitHub to finish creating a fork, which POST /forks does asynchronously.
# Short on purpose: telling the caller to retry beats holding a request open.
FORK_READY_TIMEOUT_SECONDS = 20
FORK_READY_POLL_SECONDS = 2


class WorkspaceService:
    def __init__(self):
        self.git_service = GitService()
        self.build_service = BuildService()
        self.github_service = GitHubService()

    async def create_workspace(self, db: Session, workspace_data: WorkspaceCreate, user: User) -> GitWorkspace:
        if not workspace_data.title:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Title is required")

        # Bound before the try: the handler below inspects it, and if fork() raises it would
        # otherwise be unbound and mask the real failure with an UnboundLocalError.
        workspace = None

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

                logger.info(f"Successfully setup workspace {workspace.id}")
            else:
                db.rollback()
                raise Exception("Failed to setup workspace")

            return workspace

        except HTTPException:
            if workspace:
                db.rollback()
            raise
        except Exception as e:
            logger.error(f"Error creating workspace: {e}")
            if workspace:
                db.rollback()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create workspace: {str(e)}") from e

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

            return workspaces

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting user workspaces: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get user workspaces: {str(e)}") from e

    def get_workspace_by_id(self, db: Session, workspace_id: str, user_id: str, exists=True) -> GitWorkspace:
        try:
            if not workspace_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")
            elif not user_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

            query = db.query(GitWorkspace).filter(GitWorkspace.id == workspace_id, GitWorkspace.user_id == user_id)

            workspace = query.first()
            if not workspace:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
            elif exists and not workspace.abs_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found on filesystem")
            elif exists and not workspace.abs_path.is_dir():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace is not a directory")

            return workspace
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get workspace: {str(e)}") from e

    def _can_restore_branch(self, workspace: GitWorkspace) -> bool:
        """
        Whether a branch missing from the fork should be pushed back. Only for live work: on a
        merged or closed proposal the deletion was probably deliberate.
        """
        return workspace.status == WorkspaceStatus.ACTIVE and workspace.pull_request_status not in (
            PullRequestStatus.MERGED,
            PullRequestStatus.CLOSED,
        )

    async def _wait_for_fork(self, owner: str, name: str, access_token: str) -> dict[str, Any] | None:
        """
        Poll until a freshly created fork is usable, or give up. GitHub creates the repository
        asynchronously, so its git objects are not pushable the moment POST /forks returns.
        """
        deadline = asyncio.get_running_loop().time() + FORK_READY_TIMEOUT_SECONDS

        while True:
            repository = await self.github_service.get_repository(owner, name, access_token)
            if repository:
                return repository
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(FORK_READY_POLL_SECONDS)

    async def _repair_fork(self, db: Session, workspace: GitWorkspace, user: User) -> bool:
        """
        Recreate the user's fork if it is gone, and realign every workspace that depends on it.

        True means something was repaired and retrying the failed operation is worth it; False
        means the fork was fine and the failure had another cause. Safe to re-run: the local
        clone keeps every commit, and GitHub returns the existing fork rather than a second one.
        """
        async with fork_lock(user.id):
            # Check the token before reading anything into a 404: GitHub answers 404 for "you
            # cannot see this" too, so a revoked token would look exactly like a deleted fork
            # and trigger a re-fork instead of a re-login.
            await self.github_service.get_authenticated_user(user.access_token)

            repository = await self.github_service.get_repository(workspace.fork_repo_owner, workspace.fork_repo_name, user.access_token)

            if repository:
                # The fork exists but will not accept a push. Re-forking cannot fix either of
                # these, and would fail anyway with the name already taken, so ask the user.
                if repository.get("archived") or repository.get("disabled"):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Your fork {repository['full_name']} is archived on GitHub and cannot accept changes. "
                        "Please un-archive it in your GitHub repository settings.",
                    )
                if repository.get("permissions", {}).get("push") is False:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"You no longer have permission to write to {repository['full_name']}.",
                    )
                # Healthy fork, so whatever failed was not about the fork existing
                return False

            logger.warning(f"Fork {workspace.fork_repo_owner}/{workspace.fork_repo_name} is missing on GitHub; recreating it")

            fork_repo = await self.github_service.fork(user=user, upstream_owner=settings.CEOS_ARD_ORG, upstream_repo=settings.CEOS_ARD_REPO)
            owner = fork_repo["owner"]["login"]
            name = fork_repo["name"]

            if not await self._wait_for_fork(owner, name, user.access_token):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="GitHub is still creating your fork. Please try again in a moment.",
                )

            # GitHub reports the fork's current name, so this also picks up the case where the
            # user had already re-forked it themselves, possibly under a different name.
            self._update_fork_reference(db, user.id, owner, name, fork_repo.get("clone_url"))

            # Only this workspace's branch: a recreated fork has none of them, and the others
            # push their own back when they are next opened, from their own local history.
            repo = get_repo(workspace.abs_path)
            await self.git_service.push(repo=repo, branch_name=workspace.branch_name, user=user, set_upstream=True)

            logger.info(f"Recreated fork {owner}/{name} and restored branch {workspace.branch_name}")
            return True

    async def with_remote_recovery(self, db: Session, workspace: GitWorkspace, user: User, operation):
        """
        Run a git network operation, recreating the fork and retrying once if it was deleted.

        Classification lives here rather than in `git_service`, which only sees that the remote
        said no and has no way to check what the workspace expects to find.
        """
        try:
            return await operation()
        except RemoteAccessError as e:
            logger.info(f"Remote {e.operation} failed for workspace {workspace.id}; checking whether the fork still exists")
            if not await self._repair_fork(db, workspace, user):
                raise  # nothing was repaired, so the original failure stands
            return await operation()

    async def sync_git(self, db: Session, workspace_id: str, user: User) -> SyncResult:
        workspace = self.get_workspace_by_id(db, workspace_id, user.id)

        if workspace.status == WorkspaceStatus.ARCHIVED:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot sync an archived workspace")

        repo = get_repo(workspace.abs_path)
        restore_branch = self._can_restore_branch(workspace)

        async def sync():
            return await self.git_service.sync_with_origin(
                repo=repo, user=user, branch_name=workspace.branch_name, workspace_id=workspace.id, restore_branch=restore_branch
            )

        try:
            return await sync()
        except RemoteAccessError as e:
            logger.info(f"Sync failed for workspace {workspace_id}; checking whether the fork still exists")
            if not await self._repair_fork(db, workspace, user):
                raise
            # Sync again rather than returning early: repair only restored the fork and branch,
            # the ahead/behind/conflict answer still has to be computed.
            logger.info(f"Recovered workspace {workspace_id} from a deleted fork after a failed {e.operation}")
            result = await sync()
            result.repaired = True
            return result

    def _update_fork_reference(self, db: Session, user_id: str, owner: str, name: str, clone_url: str = None) -> None:
        """
        Point every workspace of this user at a new fork location, on disk as well as in the
        database. All of them live on the same fork, so a rename, transfer or recreation moves
        all of them at once.
        """
        clone_url = clone_url or f"https://github.com/{owner}/{name}.git"
        workspaces = db.query(GitWorkspace).filter(GitWorkspace.user_id == user_id).all()

        for workspace in workspaces:
            workspace.fork_repo_owner = owner
            workspace.fork_repo_name = name

            if not workspace.abs_path.exists():
                continue
            try:
                self.git_service.set_origin_url(get_repo(workspace.abs_path), clone_url)
            except Exception as e:
                # One unrepointable clone is not a reason to abandon the rest
                logger.error(f"Could not update origin for workspace {workspace.id}: {e}")

        db.commit()
        logger.info(f"Repointed {len(workspaces)} workspace(s) of user {user_id} at {owner}/{name}")

    def _realign_renamed_fork(self, db: Session, workspace: GitWorkspace, pull_request: dict[str, Any]) -> None:
        """
        Follow a renamed or transferred fork, reading the head repo GitHub already returned.

        Done eagerly, and for free, because a rename is a latent failure: git keeps working
        through GitHub's redirect until someone reuses the old name, then breaks all at once.
        """
        head_repo = (pull_request.get("head") or {}).get("repo") or {}
        full_name = head_repo.get("full_name")
        if not full_name or "/" not in full_name:
            return

        owner, _, name = full_name.partition("/")
        if owner == workspace.fork_repo_owner and name == workspace.fork_repo_name:
            return

        logger.info(f"Fork for workspace {workspace.id} moved to {full_name}; realigning")
        self._update_fork_reference(db, workspace.user_id, owner, name, head_repo.get("clone_url"))

    def _apply_pull_request_state(self, workspace: GitWorkspace, pull_request: dict[str, Any]) -> None:
        """
        Copy live pull request state onto the workspace.

        Archiving is the consequential part: it starts the one-month timer in
        scripts/cleanup_archived_workspaces.py, which deletes the local clone and the database
        row. That clone is the only writable copy of the user's work, so archive only when the
        proposal is genuinely finished.
        """
        workspace.pull_request_status_last_updated_at = datetime.now(UTC)

        if head_repo_missing(pull_request):
            # GitHub closed this pull request because the fork was deleted, not because anyone
            # decided anything about the work. It can never be reopened, so leave the workspace
            # active and let the next push recreate the fork; propose() opens a fresh one.
            workspace.pull_request_status = PullRequestStatus.UNKNOWN
            logger.info(f"Pull request {workspace.pull_request_number} is detached: its fork was deleted on GitHub")
            return

        if pull_request_is_merged(pull_request):
            workspace.pull_request_status = PullRequestStatus.MERGED
        elif pull_request.get("state") == "open":
            workspace.pull_request_status = PullRequestStatus.OPEN
        else:
            workspace.pull_request_status = PullRequestStatus.CLOSED

        if workspace.pull_request_status in (PullRequestStatus.MERGED, PullRequestStatus.CLOSED):
            # Only on the transition, so reopening the workspace does not keep pushing the
            # deletion date back.
            if workspace.status != WorkspaceStatus.ARCHIVED:
                workspace.archived_at = datetime.now(UTC)
                workspace.status = WorkspaceStatus.ARCHIVED

    async def sync_workspace(self, db: Session, user_id: str, workspace_id: str, access_token: str) -> GitWorkspace | None:
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)

        if not access_token:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Access token is required")

        if not workspace.pull_request_number:
            return workspace

        try:
            pull_request = await self.github_service.get_pull_request(
                access_token=access_token,
                repo=settings.CEOS_ARD_REPO,
                owner=settings.CEOS_ARD_ORG,
                number=workspace.pull_request_number,
            )
        except HTTPException as e:
            # Refreshing the pull request is enrichment, not the point of this endpoint. A rate
            # limit or a GitHub outage must not make the workspace impossible to open — the
            # client navigates away on failure, stranding the user.
            logger.warning(f"Could not refresh the pull request for workspace {workspace_id}: {e.detail}")
            return workspace

        if pull_request is None:
            return workspace

        self._realign_renamed_fork(db, workspace, pull_request)
        self._apply_pull_request_state(workspace, pull_request)

        db.add(workspace)
        db.commit()
        db.refresh(workspace)

        return workspace

    async def _reactivation_blocked_reason(self, workspace: GitWorkspace, access_token: str) -> str | None:
        """
        Why this archived workspace cannot be reactivated, or None if it can.

        A merged proposal is finished, and a closed one usually is — but not when GitHub closed
        it only because the fork was deleted. Re-checked against GitHub rather than trusted from
        the database, so workspaces archived that way before the detection existed are not stuck
        with a deletion timer running.
        """
        if workspace.pull_request_status == PullRequestStatus.MERGED:
            return "Cannot reactivate a workspace whose pull request has already been merged. Please create a new workspace."

        if workspace.pull_request_status != PullRequestStatus.CLOSED:
            return None

        if not workspace.pull_request_number or not access_token:
            return None

        try:
            pull_request = await self.github_service.get_pull_request(
                access_token=access_token,
                owner=settings.CEOS_ARD_ORG,
                repo=settings.CEOS_ARD_REPO,
                number=workspace.pull_request_number,
            )
        except HTTPException as e:
            # Fail open. Being unable to reach GitHub is not a reason to keep someone locked
            # out of their own work; propose() re-checks before doing anything irreversible.
            logger.warning(f"Could not verify the pull request before reactivating workspace {workspace.id}: {e.detail}")
            return None

        if pull_request is None:
            return None

        if pull_request_is_merged(pull_request):
            return "Cannot reactivate a workspace whose pull request has already been merged. Please create a new workspace."

        if head_repo_missing(pull_request):
            # Closed by GitHub because the fork went away, not by anyone's decision.
            return None

        return "Cannot reactivate an archived workspace with a closed pull request. Please reopen the pull request first."

    async def update_workspace(self, db: Session, workspace_id: str, user: User, update_data: WorkspaceUpdate) -> GitWorkspace:
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if update_data.status and update_data.status not in WorkspaceStatus:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid workspace status")

        try:
            workspace = self.get_workspace_by_id(db, workspace_id, user.id)

            if workspace.status == WorkspaceStatus.ARCHIVED and update_data.status == WorkspaceStatus.ACTIVE:
                blocked_reason = await self._reactivation_blocked_reason(workspace, user.access_token)
                if blocked_reason:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=blocked_reason)

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
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update workspace: {str(e)}") from e

    async def delete_workspace(self, db: Session, workspace_id: str, user_id: str) -> str:
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        workspace = self.get_workspace_by_id(db, workspace_id, user_id, exists=False)

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

    def get_workspace_commits(self, db: Session, workspace_id: str, user_id: str) -> list[pygit2.Commit]:
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)
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

        workspace = self.get_workspace_by_id(db, workspace_id, user.id)
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
        workspace = self.get_workspace_by_id(db, workspace_id, user_id)
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

            self._apply_pull_request_state(workspace, pull_request)
            db.commit()

            return self._to_proposal(pull_request)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting proposal changes for workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get proposal changes: {str(e)}") from e

    @staticmethod
    def _to_proposal(pull_request: dict[str, Any]) -> Proposal:
        """Map a GitHub pull request onto the shape the editor consumes."""
        return Proposal(
            number=pull_request["number"],
            url=pull_request["html_url"],
            title=pull_request["title"],
            # Our API exposes "merged" as a third state, which GitHub does not: it reports a
            # merged pull request as "closed" and records the merge only in merged_at.
            state="merged" if pull_request_is_merged(pull_request) else pull_request["state"],
            draft=pull_request["draft"],
            description=pull_request["body"] or "",
            # The fork this was opened from is gone, so this pull request can never be
            # reopened. The UI needs to say so instead of offering a button that 422s.
            detached=head_repo_missing(pull_request),
        )

    async def propose(self, db: Session, workspace_id: str, user: User, data: ProposalRequest) -> Proposal:
        workspace = await self.sync_workspace(db, user.id, workspace_id, user.access_token)

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
            # Everything but a withdrawal needs the branch to genuinely be on the fork first:
            # GitHub refuses a pull request whose head is missing, and the local
            # remote-tracking ref is not evidence that it is there. Withdrawing touches only
            # the pull request API, and is the one action worth allowing when the fork is not.
            if data.state != "closed":
                await self.with_remote_recovery(
                    db,
                    workspace,
                    user,
                    lambda: self.git_service.ensure_branch_pushed(repo=repo, user=user, branch_name=workspace.branch_name, workspace_id=workspace.id),
                )

            pr_response = await self._handle_pull_request(
                propose_data=data,
                access_token=user.access_token,
                head_branch_name=workspace.branch_name,
                head_repo_owner=workspace.fork_repo_owner,
                workspace=workspace,
            )

            if data.state == "open":
                workspace.archived_at = None
                workspace.status = WorkspaceStatus.ACTIVE

            # String column: bind a string so this keeps working on a stricter database
            workspace.pull_request_number = str(pr_response["number"])
            self._apply_pull_request_state(workspace, pr_response)
            db.commit()

            return self._to_proposal(pr_response)

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error proposing changes for workspace {workspace_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to propose changes: {str(e)}") from e

    @staticmethod
    def _is_unreopenable(error: GitHubAPIError) -> bool:
        """
        Whether GitHub rejected a state change because the pull request can never reopen —
        either its branch or its whole fork is gone.

        Matched on the structured `errors[]` entry rather than the prose, which GitHub is free
        to reword. Any other 422, a bad title say, is a real error and must surface.
        """
        if error.status_code != status.HTTP_422_UNPROCESSABLE_ENTITY:
            return False
        return any(item.get("field") == "state" for item in error.github_errors)

    async def _create_pull_request(
        self,
        access_token: str,
        head_repo_owner: str,
        head_branch_name: str,
        title: str,
        body: str,
        supersedes: dict[str, Any] = None,
    ) -> dict[str, Any]:
        """Open a pull request, optionally noting the dead one it continues."""
        if supersedes:
            # The old pull request keeps the review conversation, which exists only on GitHub.
            # Linking is the only way the user can still get back to it.
            body = f"{body}\n\nContinues #{supersedes['number']}, which GitHub closed when its fork was deleted."

        return await self.github_service.create_pull_request(
            access_token=access_token,
            owner=settings.CEOS_ARD_ORG,
            repo=settings.CEOS_ARD_REPO,
            pr_data={
                "title": title,
                "body": body,
                "head": f"{head_repo_owner}:{head_branch_name}",
                "base": settings.CEOS_ARD_BRANCH,
                # Allow CEOS-ARD maintainers to modify the PR
                "maintainer_can_modify": True,
            },
        )

    async def _handle_pull_request(
        self,
        access_token: str,
        head_repo_owner: str,
        head_branch_name: str,
        propose_data: ProposalRequest,
        workspace: GitWorkspace,
    ) -> dict[str, Any]:
        """
        Create the pull request, update it, or replace one that can no longer be revived.

        A pull request whose fork was deleted is permanently unusable, so it is replaced rather
        than reopened: `head_repo_missing` explains why GitHub can never relink it. The
        replacement carries the old title and description across.
        """
        if not workspace.pull_request_number:
            return await self._create_pull_request(
                access_token=access_token,
                head_repo_owner=head_repo_owner,
                head_branch_name=head_branch_name,
                title=propose_data.title,
                body=propose_data.description,
            )

        existing = await self.github_service.get_pull_request(
            access_token=access_token,
            owner=settings.CEOS_ARD_ORG,
            repo=settings.CEOS_ARD_REPO,
            number=workspace.pull_request_number,
        )

        if existing is None:
            # Recorded but not on GitHub. Nothing to update, so start over.
            logger.warning(f"Pull request {workspace.pull_request_number} for workspace {workspace.id} no longer exists; opening a new one")
            return await self._create_pull_request(
                access_token=access_token,
                head_repo_owner=head_repo_owner,
                head_branch_name=head_branch_name,
                title=propose_data.title,
                body=propose_data.description,
            )

        if head_repo_missing(existing):
            logger.info(f"Replacing detached pull request {existing['number']} for workspace {workspace.id}")
            return await self._create_pull_request(
                access_token=access_token,
                head_repo_owner=head_repo_owner,
                head_branch_name=head_branch_name,
                # Prefer what the user just typed, falling back to the dead proposal's text
                title=propose_data.title or existing["title"],
                body=propose_data.description or existing["body"] or "",
                supersedes=existing,
            )

        pr_data = {"title": propose_data.title, "body": propose_data.description}
        if propose_data.state:
            pr_data["state"] = propose_data.state

        try:
            updated = await self.github_service.update_pull_request(
                access_token=access_token,
                owner=settings.CEOS_ARD_ORG,
                repo=settings.CEOS_ARD_REPO,
                number=workspace.pull_request_number,
                pr_data=pr_data,
            )
        except GitHubAPIError as e:
            if not self._is_unreopenable(e):
                raise
            # The branch was restored before this call, so a refusal now means the head moved
            # on — force push, or no longer a descendant of the one recorded at close time.
            logger.info(f"Pull request {workspace.pull_request_number} cannot be reopened ({e.detail}); opening a replacement")
            return await self._create_pull_request(
                access_token=access_token,
                head_repo_owner=head_repo_owner,
                head_branch_name=head_branch_name,
                title=propose_data.title or existing["title"],
                body=propose_data.description or existing["body"] or "",
                supersedes=existing,
            )

        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Pull request #{workspace.pull_request_number} could not be found on GitHub.",
            )

        return updated
