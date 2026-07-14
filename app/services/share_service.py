import asyncio
import logging
from datetime import UTC, datetime

import jwt
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import IdentityProvider, User
from app.models.workspace import GitWorkspace, WorkspaceStatus
from app.models.workspace_share import ShareMode, ShareStatus, WorkspaceShare, WorkspaceShareLink
from app.schemas.share import (
    ShareCreateRequest,
    ShareLinkCreateRequest,
    ShareLinkPreview,
    ShareLinkUpdateRequest,
    ShareUpdateRequest,
)
from app.services.github_service import GitHubService

logger = logging.getLogger(__name__)

# Effective-role ranking used to gate access. Phase 1 supports readonly sharing only; the owner
# is the sole writer. Additional collaborator roles (comment, edit) are deferred to later phases.
ROLE_RANK = {ShareMode.READONLY.value: 0, "owner": 1}
SHARE_LINK_TOKEN_TYPE = "share_link"


class ShareService:
    def __init__(self):
        self.github_service = GitHubService()

    def resolve_role(self, db: Session, workspace: GitWorkspace, user_id: str) -> str | None:
        """Resolve the effective role a user has on a workspace.

        Returns "owner", "readonly", or None if the user has no access at all.
        Collaborators are always readonly (the owner is the sole writer).
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

    def _get_workspace_owned_by(self, db: Session, workspace_id: str, user_id: str) -> GitWorkspace:
        workspace = db.query(GitWorkspace).filter(GitWorkspace.id == workspace_id).first()
        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
        if workspace.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the workspace owner can manage sharing")
        return workspace

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

    # --- Direct shares (invite by GitHub username) ---

    def list_shares(self, db: Session, workspace_id: str, user_id: str) -> list[WorkspaceShare]:
        self._get_workspace_owned_by(db, workspace_id, user_id)
        return db.query(WorkspaceShare).filter(WorkspaceShare.workspace_id == workspace_id).order_by(WorkspaceShare.created_at.desc()).all()

    async def create_shares(self, db: Session, workspace_id: str, user: User, request: ShareCreateRequest) -> list[WorkspaceShare]:
        workspace = self._get_workspace_owned_by(db, workspace_id, user.id)

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
            # Use GitHub's canonical login casing rather than trusting the client's input casing.
            canonical_username = gh_user["login"]
            existing_ceos_user = (
                db.query(User).filter(User.username.ilike(canonical_username), User.identity_provider == IdentityProvider.github).first()
            )
            share = (
                db.query(WorkspaceShare)
                .filter(WorkspaceShare.workspace_id == workspace.id, WorkspaceShare.invitee_github_username.ilike(canonical_username))
                .first()
            )

            if share:
                share.mode = request.mode
                share.invitee_github_username = canonical_username
                share.revoked_at = None
                if existing_ceos_user:
                    share.invitee_user_id = existing_ceos_user.id
                    share.status = ShareStatus.ACCEPTED
                    share.accepted_at = share.accepted_at or now
                elif share.status == ShareStatus.REVOKED:
                    share.status = ShareStatus.PENDING
                    share.invitee_user_id = None
                    share.accepted_at = None
            else:
                share = WorkspaceShare(
                    workspace_id=workspace.id,
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

    def update_share(self, db: Session, workspace_id: str, share_id: str, user_id: str, request: ShareUpdateRequest) -> WorkspaceShare:
        self._get_workspace_owned_by(db, workspace_id, user_id)
        share = db.query(WorkspaceShare).filter(WorkspaceShare.id == share_id, WorkspaceShare.workspace_id == workspace_id).first()
        if not share:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share not found")

        share.mode = request.mode
        db.commit()
        db.refresh(share)
        return share

    def revoke_share(self, db: Session, workspace_id: str, share_id: str, user_id: str) -> WorkspaceShare:
        self._get_workspace_owned_by(db, workspace_id, user_id)
        share = db.query(WorkspaceShare).filter(WorkspaceShare.id == share_id, WorkspaceShare.workspace_id == workspace_id).first()
        if not share:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share not found")

        share.status = ShareStatus.REVOKED
        share.revoked_at = datetime.now(UTC)
        db.commit()
        db.refresh(share)

        return share

    # --- Share links ---

    def list_share_links(self, db: Session, workspace_id: str, user_id: str) -> list[WorkspaceShareLink]:
        self._get_workspace_owned_by(db, workspace_id, user_id)
        links = (
            db.query(WorkspaceShareLink).filter(WorkspaceShareLink.workspace_id == workspace_id).order_by(WorkspaceShareLink.created_at.desc()).all()
        )
        for link in links:
            link.url = self._build_share_link_url(link)
        return links

    def create_share_link(self, db: Session, workspace_id: str, user: User, request: ShareLinkCreateRequest) -> WorkspaceShareLink:
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

    def update_share_link(self, db: Session, workspace_id: str, link_id: str, user_id: str, request: ShareLinkUpdateRequest) -> WorkspaceShareLink:
        self._get_workspace_owned_by(db, workspace_id, user_id)
        link = db.query(WorkspaceShareLink).filter(WorkspaceShareLink.id == link_id, WorkspaceShareLink.workspace_id == workspace_id).first()
        if not link:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share link not found")

        self._validate_expires_at(request.expires_at)

        for key, value in request.model_dump(exclude_unset=True).items():
            if key in ("mode", "is_active") and value is None:
                continue
            setattr(link, key, value)

        db.commit()
        db.refresh(link)
        link.url = self._build_share_link_url(link)
        return link

    def delete_share_link(self, db: Session, workspace_id: str, link_id: str, user_id: str) -> None:
        self._get_workspace_owned_by(db, workspace_id, user_id)
        link = db.query(WorkspaceShareLink).filter(WorkspaceShareLink.id == link_id, WorkspaceShareLink.workspace_id == workspace_id).first()
        if not link:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share link not found")

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

    def _get_active_link_or_404(self, db: Session, token: str) -> WorkspaceShareLink:
        share_link_id = self._decode_share_link_token(token)
        link = db.query(WorkspaceShareLink).filter(WorkspaceShareLink.id == share_link_id).first()

        if not link or not link.is_active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid, inactive, or deleted share link")

        if link.expires_at and link.expires_at <= datetime.now(UTC):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This share link has expired")

        return link

    def get_share_link_preview(self, db: Session, token: str) -> ShareLinkPreview:
        link = self._get_active_link_or_404(db, token)
        workspace = db.query(GitWorkspace).filter(GitWorkspace.id == link.workspace_id).first()

        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid, inactive, or deleted share link")

        owner = db.query(User).filter(User.id == workspace.user_id).first()

        return ShareLinkPreview(
            workspace_title=workspace.title,
            owner_display_name=(owner.full_name or owner.username) if owner else "Unknown",
            mode=link.mode,
            is_active=link.is_active,
        )

    def redeem_share_link(self, db: Session, token: str, user: User) -> tuple[WorkspaceShare | None, GitWorkspace]:
        link = self._get_active_link_or_404(db, token)
        workspace = db.query(GitWorkspace).filter(GitWorkspace.id == link.workspace_id).first()
        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid, inactive, or deleted share link")

        if workspace.user_id == user.id:
            workspace.viewer_role = "owner"
            workspace.owner_username = user.username
            workspace.owner_full_name = user.full_name
            return None, workspace

        share = db.query(WorkspaceShare).filter(WorkspaceShare.workspace_id == workspace.id, WorkspaceShare.invitee_user_id == user.id).first()
        if not share:
            # Defensive fallback: a share may still be keyed by username only (not yet linked to this user_id).
            share = (
                db.query(WorkspaceShare)
                .filter(WorkspaceShare.workspace_id == workspace.id, WorkspaceShare.invitee_github_username.ilike(user.username))
                .first()
            )

        if share and share.status == ShareStatus.REVOKED:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your access to this workspace was previously revoked by the owner")

        if not share or share.status != ShareStatus.ACCEPTED:
            now = datetime.now(UTC)
            if share:
                share.invitee_user_id = user.id
                share.status = ShareStatus.ACCEPTED
                share.accepted_at = now
                share.share_link_id = link.id
            else:
                share = WorkspaceShare(
                    workspace_id=workspace.id,
                    share_link_id=link.id,
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

        workspace.viewer_role = self.resolve_role(db, workspace, user.id)
        workspace.owner_username = workspace.user.username if workspace.user else None
        workspace.owner_full_name = workspace.user.full_name if workspace.user else None
        return share, workspace
