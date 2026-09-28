import asyncio
import logging
from datetime import UTC, datetime

import jwt
from fastapi import HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import IdentityProvider, User
from app.models.workspace import GitWorkspace, WorkspaceStatus
from app.models.workspace_share import ShareMode, ShareStatus, WorkspaceShare, WorkspaceShareLink
from app.schemas.events import EventType
from app.schemas.share import (
    ShareCreateRequest,
    ShareLinkCreateRequest,
    ShareLinkPreview,
    ShareLinkUpdateRequest,
    ShareUpdateRequest,
)
from app.services.events_service import EventBroker, event_broker
from app.services.github_service import GitHubService

logger = logging.getLogger(__name__)

# Effective-role ranking used to gate access. Owner is the sole writer.
ROLE_RANK = {ShareMode.READONLY.value: 0, "owner": 1}
SHARE_LINK_TOKEN_TYPE = "share_link"


def resolve_role(db: Session, workspace: GitWorkspace, user_id: str) -> str | None:
    """The effective role of a user on a workspace: "owner", "readonly", or None without access.

    The owner is the sole writer; collaborators are readonly, as is everyone on an archived workspace.
    """
    if not user_id:
        return None

    if workspace.user_id == user_id:
        return "owner"

    share = (
        db.query(WorkspaceShare)
        .filter(
            WorkspaceShare.workspace_id == workspace.id,
            WorkspaceShare.invitee_user_id == user_id,
            WorkspaceShare.status == ShareStatus.ACCEPTED,
        )
        .first()
    )
    if not share:
        return None

    if workspace.status == WorkspaceStatus.ARCHIVED:
        return ShareMode.READONLY.value

    return share.mode.value


def activate_pending_shares(db: Session, user: User) -> None:
    """At login: accept this GitHub account's pending shares and refresh the cached username.

    Matched by account id, never by username: a renamed username can be claimed by someone else.
    """
    if user.identity_provider != IdentityProvider.github:
        return

    try:
        shares = db.query(WorkspaceShare).filter(WorkspaceShare.invitee_github_id == user.external_id).all()
        if not shares:
            return

        now = datetime.now(UTC)
        activated = 0
        for share in shares:
            share.invitee_github_username = user.username
            if share.status == ShareStatus.PENDING:
                share.accept(user.id, user.username, now)
                activated += 1

        db.commit()
        if activated:
            logger.info(f"Activated {activated} pending workspace share(s) for user {user.username}")
    except SQLAlchemyError as e:
        logger.error(f"Failed to activate pending workspace shares for user {user.username}: {e}")
        db.rollback()


class ShareService:
    def __init__(self, github_service: GitHubService | None = None, broker: EventBroker | None = None):
        self.github_service = github_service or GitHubService()
        self.broker = broker or event_broker

    @staticmethod
    def _ensure_mode_enabled(mode: ShareMode) -> None:
        if mode not in settings.SHARING_MODES_ENABLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Share mode '{mode}' is not enabled. Enabled modes: {', '.join(settings.SHARING_MODES_ENABLED)}",
            )

    def _get_workspace_owned_by(self, db: Session, workspace_id: str, user_id: str) -> GitWorkspace:
        workspace = db.query(GitWorkspace).filter(GitWorkspace.id == workspace_id).first()
        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
        if workspace.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the workspace owner can manage sharing")
        return workspace

    @staticmethod
    def _get_child_or_404(db: Session, model, child_id: str, workspace_id: str, detail: str):
        """A share or share link by id, scoped to its workspace."""
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
                detail="expiresAt must include a timezone offset (e.g. suffix with 'Z' for UTC)",
            )
        if expires_at <= datetime.now(UTC):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="expiresAt must be in the future")

    # --- Direct shares (invited by username, bound to the GitHub account id) ---

    async def list_shares(self, db: Session, workspace_id: str, user_id: str) -> list[WorkspaceShare]:
        self._get_workspace_owned_by(db, workspace_id, user_id)
        return db.query(WorkspaceShare).filter(WorkspaceShare.workspace_id == workspace_id).order_by(WorkspaceShare.created_at.desc()).all()

    async def create_shares(self, db: Session, workspace_id: str, user: User, request: ShareCreateRequest) -> list[WorkspaceShare]:
        self._ensure_mode_enabled(request.mode)
        # Validate that the workspace exists and is owned by the current user before proceeding.
        workspace = self._get_workspace_owned_by(db, workspace_id, user.id)
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
        shares = []
        for gh_user in github_users:
            # Bound to the account id (survives renames); the login is display only, in GitHub's canonical casing.
            github_id = str(gh_user["id"])
            canonical_username = gh_user["login"]
            if github_id == user.external_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot share a workspace with yourself")
            existing_ceos_user = db.query(User).filter(User.external_id == github_id, User.identity_provider == IdentityProvider.github).first()
            share = (
                db.query(WorkspaceShare).filter(WorkspaceShare.workspace_id == workspace.id, WorkspaceShare.invitee_github_id == github_id).first()
            )

            if share:
                share.mode = request.mode
                share.invitee_github_username = canonical_username
                share.revoked_at = None
                if existing_ceos_user:
                    share.accept(existing_ceos_user.id, canonical_username, now)
                elif share.status == ShareStatus.REVOKED:
                    share.status = ShareStatus.PENDING
                    share.invitee_user_id = None
                    share.accepted_at = None
            else:
                share = WorkspaceShare(
                    workspace_id=workspace.id,
                    invitee_github_id=github_id,
                    invitee_github_username=canonical_username,
                    invitee_user_id=existing_ceos_user.id if existing_ceos_user else None,
                    invited_by_user_id=user.id,
                    mode=request.mode,
                    status=ShareStatus.ACCEPTED if existing_ceos_user else ShareStatus.PENDING,
                    accepted_at=now if existing_ceos_user else None,
                )
                db.add(share)

            shares.append(share)

        db.commit()
        for share in shares:
            db.refresh(share)

        return shares

    async def update_share(self, db: Session, workspace_id: str, share_id: str, user_id: str, request: ShareUpdateRequest) -> WorkspaceShare:
        self._ensure_mode_enabled(request.mode)
        self._get_workspace_owned_by(db, workspace_id, user_id)
        share = self._get_child_or_404(db, WorkspaceShare, share_id, workspace_id, "Share not found")

        share.mode = request.mode
        db.commit()
        db.refresh(share)
        return share

    async def revoke_share(self, db: Session, workspace_id: str, share_id: str, user_id: str) -> WorkspaceShare:
        self._get_workspace_owned_by(db, workspace_id, user_id)
        share = self._get_child_or_404(db, WorkspaceShare, share_id, workspace_id, "Share not found")

        share.status = ShareStatus.REVOKED
        share.revoked_at = datetime.now(UTC)
        db.commit()
        db.refresh(share)

        # Only the affected user is told; the gateway closes their connection after delivery
        if share.invitee_user_id:
            self.broker.emit(workspace_id, EventType.SHARE_REVOKED, actor_user_id=user_id, target_user_id=share.invitee_user_id)

        return share

    # --- Share links ---

    async def list_share_links(self, db: Session, workspace_id: str, user_id: str) -> list[WorkspaceShareLink]:
        self._get_workspace_owned_by(db, workspace_id, user_id)
        links = (
            db.query(WorkspaceShareLink).filter(WorkspaceShareLink.workspace_id == workspace_id).order_by(WorkspaceShareLink.created_at.desc()).all()
        )
        for link in links:
            link.url = self._build_share_link_url(link)
        return links

    async def create_share_link(self, db: Session, workspace_id: str, user: User, request: ShareLinkCreateRequest) -> WorkspaceShareLink:
        self._ensure_mode_enabled(request.mode)
        self._get_workspace_owned_by(db, workspace_id, user.id)
        self._validate_expires_at(request.expires_at)

        link = WorkspaceShareLink(
            workspace_id=workspace_id,
            mode=request.mode,
            is_active=True,
            expires_at=request.expires_at,
            created_by_user_id=user.id,
        )
        db.add(link)
        db.commit()
        db.refresh(link)

        link.url = self._build_share_link_url(link)
        return link

    async def update_share_link(
        self, db: Session, workspace_id: str, link_id: str, user_id: str, request: ShareLinkUpdateRequest
    ) -> WorkspaceShareLink:
        self._get_workspace_owned_by(db, workspace_id, user_id)
        link = self._get_child_or_404(db, WorkspaceShareLink, link_id, workspace_id, "Share link not found")

        if request.mode is not None:
            self._ensure_mode_enabled(request.mode)
        self._validate_expires_at(request.expires_at)

        for key, value in request.model_dump(exclude_unset=True).items():
            if key in ("mode", "is_active") and value is None:
                continue
            setattr(link, key, value)

        db.commit()
        db.refresh(link)
        link.url = self._build_share_link_url(link)
        return link

    async def delete_share_link(self, db: Session, workspace_id: str, link_id: str, user_id: str) -> None:
        self._get_workspace_owned_by(db, workspace_id, user_id)
        link = self._get_child_or_404(db, WorkspaceShareLink, link_id, workspace_id, "Share link not found")

        db.query(WorkspaceShare).filter(WorkspaceShare.share_link_id == link.id).update(
            {WorkspaceShare.share_link_id: None}, synchronize_session=False
        )

        db.delete(link)
        db.commit()

    def _build_share_link_url(self, link: WorkspaceShareLink) -> str:
        # The token only references the link's ID - mode/active/expiry are always re-read live from
        # the WorkspaceShareLink row, so the owner can change them later without reissuing the URL.
        token = jwt.encode({"share_link_id": link.id, "type": SHARE_LINK_TOKEN_TYPE}, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        return f"{settings.CLIENT_URL}/share/{token}"

    def _decode_share_link_token(self, token: str) -> str:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        except jwt.InvalidTokenError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or expired share link") from e

        share_link_id = payload.get("share_link_id")
        if payload.get("type") != SHARE_LINK_TOKEN_TYPE or not share_link_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or expired share link")

        return share_link_id

    def _get_active_link_or_404(self, db: Session, token: str) -> tuple[WorkspaceShareLink, GitWorkspace]:
        """The link behind a token and its workspace; 404 unless the link is active and unexpired."""
        share_link_id = self._decode_share_link_token(token)
        link = db.query(WorkspaceShareLink).filter(WorkspaceShareLink.id == share_link_id).first()

        if not link or not link.is_active or not link.workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid, inactive, or deleted share link")

        if link.expires_at and link.expires_at <= datetime.now(UTC):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This share link has expired")

        return link, link.workspace

    async def get_share_link_preview(self, db: Session, token: str) -> ShareLinkPreview:
        link, workspace = self._get_active_link_or_404(db, token)
        owner = workspace.user

        return ShareLinkPreview(
            workspace_title=workspace.title,
            owner_display_name=(owner.full_name or owner.username) if owner else "Unknown",
            mode=link.mode,
            is_active=link.is_active,
        )

    async def redeem_share_link(self, db: Session, token: str, user: User) -> tuple[WorkspaceShare | None, GitWorkspace]:
        link, workspace = self._get_active_link_or_404(db, token)
        share = None

        if workspace.user_id != user.id:
            # Matched by account id, not the renameable username: a pending invite is activated, not duplicated.
            share = (
                db.query(WorkspaceShare)
                .filter(WorkspaceShare.workspace_id == workspace.id, WorkspaceShare.invitee_github_id == user.external_id)
                .first()
            )

            if share and share.status == ShareStatus.REVOKED:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your access to this workspace was previously revoked by the owner")

            if not share or share.status != ShareStatus.ACCEPTED:
                now = datetime.now(UTC)
                if share:
                    share.accept(user.id, user.username, now)
                    share.share_link_id = link.id
                else:
                    share = WorkspaceShare(
                        workspace_id=workspace.id,
                        share_link_id=link.id,
                        invitee_github_id=user.external_id,
                        invitee_github_username=user.username,
                        invitee_user_id=user.id,
                        invited_by_user_id=link.created_by_user_id,
                        mode=link.mode,
                        status=ShareStatus.ACCEPTED,
                        accepted_at=now,
                    )
                    db.add(share)
                db.commit()
                db.refresh(share)

        workspace.annotate_viewer(resolve_role(db, workspace, user.id))
        return share, workspace
