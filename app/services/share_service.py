from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import IdentityProvider, User
from app.models.workspace import GitWorkspace, WorkspaceStatus
from app.models.workspace_share import AccessMode, CollaboratorStatus, WorkspaceCollaborator, WorkspaceShare
from app.schemas.events import EventType
from app.schemas.share import (
    CollaboratorCreateRequest,
    CollaboratorUpdateRequest,
    ShareCreateRequest,
    SharePreview,
    ShareUpdateRequest,
)
from app.services.events_service import EventBroker, event_broker
from app.services.github_service import GitHubService

if TYPE_CHECKING:
    from app.services.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)

# Effective-role ranking used to gate access. Owner is the sole writer.
ROLE_RANK = {AccessMode.READONLY.value: 0, "owner": 1}
# GitHub login syntax: alphanumerics and single hyphens, up to 39 characters. Checked before the
# name goes into the API URL path, where e.g. "octocat/repos" would hit another endpoint.
GITHUB_USERNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")


def resolve_role(db: Session, workspace: GitWorkspace, user_id: str) -> str | None:
    """The effective role of a user on a workspace: "owner", "readonly", or None without access.

    The owner is the sole writer; collaborators are readonly, as is everyone on an archived workspace.
    """
    if not user_id:
        return None

    if workspace.user_id == user_id:
        return "owner"

    collaborator = (
        db.query(WorkspaceCollaborator)
        .filter(
            WorkspaceCollaborator.workspace_id == workspace.id,
            WorkspaceCollaborator.invitee_user_id == user_id,
            WorkspaceCollaborator.status == CollaboratorStatus.ACCEPTED,
        )
        .first()
    )
    if not collaborator:
        return None

    if workspace.status == WorkspaceStatus.ARCHIVED:
        return AccessMode.READONLY.value

    return collaborator.mode.value


def activate_pending_collaborators(db: Session, user: User) -> None:
    """At login: accept this GitHub account's pending collaborations and refresh the cached username.

    Matched by account id, never by username: a renamed username can be claimed by someone else.
    """
    if user.identity_provider != IdentityProvider.github:
        return

    try:
        collaborators = db.query(WorkspaceCollaborator).filter(WorkspaceCollaborator.invitee_github_id == user.external_id).all()
        if not collaborators:
            return

        now = datetime.now(UTC)
        activated = 0
        for collaborator in collaborators:
            collaborator.invitee_github_username = user.username
            if collaborator.status == CollaboratorStatus.PENDING:
                collaborator.accept(user.id, user.username, now)
                activated += 1

        db.commit()
        if activated:
            logger.info(f"Activated {activated} pending workspace collaboration(s) for user {user.username}")
    except SQLAlchemyError as e:
        logger.error(f"Failed to activate pending workspace collaborations for user {user.username}: {e}")
        db.rollback()


class ShareService:
    def __init__(self, workspace_service: WorkspaceService, github_service: GitHubService | None = None, broker: EventBroker | None = None):
        self.workspace_service = workspace_service
        self.github_service = github_service or GitHubService()
        self.broker = broker or event_broker

    @staticmethod
    def _ensure_mode_enabled(mode: AccessMode) -> None:
        if mode not in settings.SHARING_MODES_ENABLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Share mode '{mode}' is not enabled. Enabled modes: {', '.join(settings.SHARING_MODES_ENABLED)}",
            )

    @staticmethod
    def _get_child_or_404(db: Session, model, child_id: str, workspace_id: str, detail: str):
        """A collaborator or share by id, scoped to its workspace."""
        child = db.query(model).filter(model.id == child_id, model.workspace_id == workspace_id).first()
        if not child:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
        return child

    def _validate_expires_at(self, expires_at: datetime | None) -> None:
        if expires_at is None:
            return
        if expires_at.tzinfo is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="expires_at must include a timezone offset (e.g. suffix with 'Z' for UTC)",
            )
        if expires_at <= datetime.now(UTC):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="expires_at must be in the future")

    # --- Collaborators (invited by username, bound to the GitHub account id) ---

    async def list_collaborators(self, db: Session, workspace_id: str, user_id: str) -> list[WorkspaceCollaborator]:
        self.workspace_service.get_workspace_by_id(db, workspace_id, user_id, exists=False, min_role="owner")
        return (
            db.query(WorkspaceCollaborator)
            .filter(WorkspaceCollaborator.workspace_id == workspace_id)
            .order_by(WorkspaceCollaborator.created_at.desc())
            .all()
        )

    async def add_collaborators(self, db: Session, workspace_id: str, user: User, request: CollaboratorCreateRequest) -> list[WorkspaceCollaborator]:
        self._ensure_mode_enabled(request.mode)
        # Validate that the workspace exists and is owned by the current user before proceeding.
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user.id, exists=False, min_role="owner")
        # Deduplicate and clean the GitHub usernames, ignoring empty strings and duplicates (case-insensitive).
        seen_usernames: set[str] = set()
        usernames: list[str] = []
        for raw_username in request.github_usernames:
            cleaned = raw_username.strip() if raw_username else ""
            if cleaned and cleaned.lower() not in seen_usernames:
                seen_usernames.add(cleaned.lower())
                usernames.append(cleaned)
        if not usernames:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one GitHub username is required")

        malformed = [username for username in usernames if not GITHUB_USERNAME.match(username)]
        if malformed:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid GitHub username(s): {', '.join(malformed)}")

        if any(username.lower() == user.username.lower() for username in usernames):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot share a workspace with yourself")

        # Validate every username against GitHub before writing anything (all-or-nothing).
        github_users = await asyncio.gather(*(self.github_service.get_github_user(username, user.access_token) for username in usernames))

        invalid_usernames = [username for username, gh_user in zip(usernames, github_users, strict=True) if gh_user is None]
        if invalid_usernames:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"GitHub username(s) not found: {', '.join(invalid_usernames)}",
            )

        now = datetime.now(UTC)
        try:
            return self._upsert_collaborators(db, workspace, user, request.mode, github_users, now)
        except IntegrityError:
            # Lost a race with a concurrent share of the same account: redo on top of its row
            db.rollback()
            return self._upsert_collaborators(db, workspace, user, request.mode, github_users, now)

    @staticmethod
    def _upsert_collaborators(
        db: Session, workspace: GitWorkspace, user: User, mode: AccessMode, github_users: list[dict], now: datetime
    ) -> list[WorkspaceCollaborator]:
        collaborators = []
        for gh_user in github_users:
            # Bound to the account id (survives renames); the login is display only, in GitHub's canonical casing.
            github_id = str(gh_user["id"])
            canonical_username = gh_user["login"]
            if github_id == user.external_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot share a workspace with yourself")
            existing_ceos_user = db.query(User).filter(User.external_id == github_id, User.identity_provider == IdentityProvider.github).first()
            collaborator = (
                db.query(WorkspaceCollaborator)
                .filter(WorkspaceCollaborator.workspace_id == workspace.id, WorkspaceCollaborator.invitee_github_id == github_id)
                .first()
            )

            if collaborator:
                collaborator.mode = mode
                collaborator.invitee_github_username = canonical_username
                collaborator.revoked_at = None
                if existing_ceos_user:
                    collaborator.accept(existing_ceos_user.id, canonical_username, now)
                elif collaborator.status == CollaboratorStatus.REVOKED:
                    collaborator.status = CollaboratorStatus.PENDING
                    collaborator.invitee_user_id = None
                    collaborator.accepted_at = None
            else:
                collaborator = WorkspaceCollaborator(
                    workspace_id=workspace.id,
                    invitee_github_id=github_id,
                    invitee_github_username=canonical_username,
                    invitee_user_id=existing_ceos_user.id if existing_ceos_user else None,
                    invited_by_user_id=user.id,
                    mode=mode,
                    status=CollaboratorStatus.ACCEPTED if existing_ceos_user else CollaboratorStatus.PENDING,
                    accepted_at=now if existing_ceos_user else None,
                )
                db.add(collaborator)

            collaborators.append(collaborator)

        db.commit()
        for collaborator in collaborators:
            db.refresh(collaborator)

        return collaborators

    async def update_collaborator(
        self, db: Session, workspace_id: str, collaborator_id: str, user_id: str, request: CollaboratorUpdateRequest
    ) -> WorkspaceCollaborator:
        self._ensure_mode_enabled(request.mode)
        self.workspace_service.get_workspace_by_id(db, workspace_id, user_id, exists=False, min_role="owner")
        collaborator = self._get_child_or_404(db, WorkspaceCollaborator, collaborator_id, workspace_id, "Collaborator not found")

        changed = collaborator.mode != request.mode
        collaborator.mode = request.mode
        db.commit()
        db.refresh(collaborator)

        if changed and collaborator.invitee_user_id:
            self.broker.emit(
                workspace_id,
                EventType.COLLABORATOR_UPDATED,
                actor_user_id=user_id,
                target_user_id=collaborator.invitee_user_id,
                mode=collaborator.mode.value,
            )
        return collaborator

    async def revoke_collaborator(self, db: Session, workspace_id: str, collaborator_id: str, user_id: str) -> WorkspaceCollaborator:
        self.workspace_service.get_workspace_by_id(db, workspace_id, user_id, exists=False, min_role="owner")
        collaborator = self._get_child_or_404(db, WorkspaceCollaborator, collaborator_id, workspace_id, "Collaborator not found")

        collaborator.status = CollaboratorStatus.REVOKED
        collaborator.revoked_at = datetime.now(UTC)
        db.commit()
        db.refresh(collaborator)

        # Only the affected user is told; the gateway closes their connection after delivery
        if collaborator.invitee_user_id:
            self.broker.emit(workspace_id, EventType.COLLABORATOR_REVOKED, actor_user_id=user_id, target_user_id=collaborator.invitee_user_id)

        return collaborator

    # --- Shares ---

    async def list_shares(self, db: Session, workspace_id: str, user_id: str) -> list[WorkspaceShare]:
        self.workspace_service.get_workspace_by_id(db, workspace_id, user_id, exists=False, min_role="owner")
        shares = db.query(WorkspaceShare).filter(WorkspaceShare.workspace_id == workspace_id).order_by(WorkspaceShare.created_at.desc()).all()
        return shares

    async def create_share(self, db: Session, workspace_id: str, user: User, request: ShareCreateRequest) -> WorkspaceShare:
        self._ensure_mode_enabled(request.mode)
        self.workspace_service.get_workspace_by_id(db, workspace_id, user.id, exists=False, min_role="owner")
        self._validate_expires_at(request.expires_at)

        share = WorkspaceShare(
            workspace_id=workspace_id,
            mode=request.mode,
            expires_at=request.expires_at,
            created_by_user_id=user.id,
        )
        db.add(share)
        db.commit()
        db.refresh(share)

        return share

    async def update_share(self, db: Session, workspace_id: str, share_id: str, user_id: str, request: ShareUpdateRequest) -> WorkspaceShare:
        self.workspace_service.get_workspace_by_id(db, workspace_id, user_id, exists=False, min_role="owner")
        share = self._get_child_or_404(db, WorkspaceShare, share_id, workspace_id, "Share not found")

        if request.mode is not None:
            self._ensure_mode_enabled(request.mode)
        self._validate_expires_at(request.expires_at)

        for key, value in request.model_dump(exclude_unset=True).items():
            if key == "mode" and value is None:
                continue
            setattr(share, key, value)

        db.commit()
        db.refresh(share)
        return share

    async def delete_share(self, db: Session, workspace_id: str, share_id: str, user_id: str) -> None:
        self.workspace_service.get_workspace_by_id(db, workspace_id, user_id, exists=False, min_role="owner")
        share = self._get_child_or_404(db, WorkspaceShare, share_id, workspace_id, "Share not found")

        db.query(WorkspaceCollaborator).filter(WorkspaceCollaborator.share_id == share.id).update(
            {WorkspaceCollaborator.share_id: None}, synchronize_session=False
        )

        db.delete(share)
        db.commit()

    def _get_live_share_or_404(self, db: Session, token: str) -> tuple[WorkspaceShare, GitWorkspace]:
        """The share behind a token and its workspace; 404 unless the share exists and is unexpired."""
        share = db.query(WorkspaceShare).filter(WorkspaceShare.token == token).first()

        if not share or not share.workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or deleted share")

        if share.expires_at and share.expires_at <= datetime.now(UTC):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This share has expired")

        return share, share.workspace

    async def get_share_preview(self, db: Session, token: str) -> SharePreview:
        share, workspace = self._get_live_share_or_404(db, token)
        owner = workspace.user

        return SharePreview(
            workspace_title=workspace.title,
            owner_display_name=(owner.full_name or owner.username) if owner else "Unknown",
            mode=share.mode,
        )

    async def redeem_share(self, db: Session, token: str, user: User) -> tuple[WorkspaceCollaborator | None, GitWorkspace]:
        share, workspace = self._get_live_share_or_404(db, token)
        collaborator = None

        if workspace.user_id != user.id:
            try:
                collaborator = self._accept_share(db, share, workspace, user)
            except IntegrityError:
                # Lost a race with a concurrent redemption by the same account: redo on top of its row
                db.rollback()
                collaborator = self._accept_share(db, share, workspace, user)

        workspace.annotate_viewer(resolve_role(db, workspace, user.id))
        return collaborator, workspace

    @staticmethod
    def _accept_share(db: Session, share: WorkspaceShare, workspace: GitWorkspace, user: User) -> WorkspaceCollaborator:
        # Matched by account id, not the renameable username: a pending invite is activated, not duplicated.
        collaborator = (
            db.query(WorkspaceCollaborator)
            .filter(WorkspaceCollaborator.workspace_id == workspace.id, WorkspaceCollaborator.invitee_github_id == user.external_id)
            .first()
        )

        if collaborator and collaborator.status == CollaboratorStatus.REVOKED:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your access to this workspace was previously revoked by the owner")

        if not collaborator or collaborator.status != CollaboratorStatus.ACCEPTED:
            now = datetime.now(UTC)
            if collaborator:
                collaborator.accept(user.id, user.username, now)
                collaborator.share_id = share.id
            else:
                collaborator = WorkspaceCollaborator(
                    workspace_id=workspace.id,
                    share_id=share.id,
                    invitee_github_id=user.external_id,
                    invitee_github_username=user.username,
                    invitee_user_id=user.id,
                    invited_by_user_id=share.created_by_user_id,
                    mode=share.mode,
                    status=CollaboratorStatus.ACCEPTED,
                    accepted_at=now,
                )
                db.add(collaborator)
            db.commit()
            db.refresh(collaborator)

        return collaborator
