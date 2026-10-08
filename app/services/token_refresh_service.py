import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from authlib.integrations.base_client import OAuthError
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import IdentityProvider, User
from app.oauth.handler import oauth
from app.services.github_service import GitHubService
from app.utils.locks import refresh_locks

logger = logging.getLogger(__name__)

# A provider token counts as expired this long before its actual expiry
PROVIDER_EXPIRY_BUFFER = timedelta(minutes=5)


class TokenRefreshService:
    """Refreshes the provider access tokens stored per user.

    Google refresh tokens can be reused. GitHub's are single-use and come only with expiring
    tokens (offline_access scope); a GitHub token without one cannot be renewed.
    """

    # A GitHub token without an expiry gets the lifetime of GitHub's expiring tokens, so every session ends the same way
    GITHUB_TOKEN_LIFETIME = timedelta(hours=8)

    @staticmethod
    async def ensure_fresh_token(user: User, db: Session) -> None:
        """Renew the provider token when it is expired or about to expire.

        Same-user requests wait for the first renewal and re-read the user afterwards.
        Raises HTTPException when the token cannot be renewed.
        """
        if not TokenRefreshService.is_token_expired(user):
            return
        async with refresh_locks(user.id):
            db.refresh(user)
            if TokenRefreshService.is_token_expired(user):
                await TokenRefreshService.refresh_token_for_user(user, db)

    @staticmethod
    async def refresh_google_token(user: User, db: Session) -> dict[str, Any]:
        """Refresh Google access token using refresh token.

        Uses authlib OAuth client for proper token refresh handling.

        Args:
            user: User object with stored refresh token
            db: Database session

        Returns:
            Dictionary with new access_token and token expiry

        Raises:
            HTTPException: If refresh fails
        """
        if not user.refresh_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No refresh token available for this user",
            )

        try:
            new_token = await oauth.google.fetch_access_token(grant_type="refresh_token", refresh_token=user.refresh_token)

            access_token = new_token.get("access_token")
            expires_in = new_token.get("expires_in")
            # Google may return a new refresh token
            refresh_token = new_token.get("refresh_token", user.refresh_token)

            if not access_token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="No access token in refresh response",
                )

            # Update user with new tokens in database
            user.access_token = access_token  # Store new provider access token
            user.refresh_token = refresh_token
            if expires_in:
                user.token_expiry = datetime.now(UTC) + timedelta(seconds=expires_in)

            user.updated_at = datetime.now(UTC)
            db.commit()

            logger.info(f"Successfully refreshed provider token for Google user {user.username}")

            return {
                "access_token": access_token,
                "expires_in": expires_in,
                "refresh_token": refresh_token,
            }

        except Exception as e:
            logger.error(f"Error refreshing Google token: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Failed to refresh Google token. Please re-authenticate.",
            ) from e

    @staticmethod
    async def refresh_github_token(user: User, db: Session) -> dict[str, Any]:
        """Exchange the refresh token for a new GitHub token pair; the old pair stops working."""
        if not user.refresh_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="GitHub access token expired and cannot be renewed. Please log in again.",
            )

        try:
            new_token = await oauth.github.fetch_access_token(grant_type="refresh_token", refresh_token=user.refresh_token)
            access_token = new_token.get("access_token")
            if not access_token:
                raise ValueError("no access token in the refresh response")
        except Exception as e:
            # Includes bad_refresh_token: unused for six months, or already spent
            logger.error(f"Error refreshing GitHub token for {user.username}: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Failed to refresh GitHub token. Please log in again.",
            ) from e

        # Logged out while GitHub was answering: don't store a token pair for a session the user just ended
        db.refresh(user)
        if user.access_token is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Logged out. Please log in again.")

        expires_in = new_token.get("expires_in")
        user.access_token = access_token
        # GitHub returns a new refresh token with every refresh
        user.refresh_token = new_token.get("refresh_token", user.refresh_token)
        user.token_expiry = datetime.now(UTC) + (timedelta(seconds=int(expires_in)) if expires_in else TokenRefreshService.GITHUB_TOKEN_LIFETIME)
        user.updated_at = datetime.now(UTC)
        db.commit()

        logger.info(f"Refreshed GitHub token for user {user.username}")

        return {
            "access_token": access_token,
            "expires_in": expires_in,
            "refresh_token": user.refresh_token,
        }

    @staticmethod
    async def end_session(user: User, db: Session, github_service: GitHubService, unattended: bool = False) -> bool:
        """Revoke the provider tokens and clear them, which is what ends the session; False if it was left.

        GitHub: revoking a valid access token also kills its refresh token, but an expired one can't
        be revoked (404), so it is renewed first and the fresh one revoked; the fresh refresh token is
        never stored.

        Logout always clears. Unattended (the idle cleanup), a revocation the provider can't confirm
        raises, and a session renewed meanwhile by a login is left alone; both keep the tokens.
        """
        provider = user.identity_provider
        # Changes with every login and refresh, so it identifies the session being ended
        session_expiry = user.token_expiry
        try:
            if provider == IdentityProvider.google and (user.refresh_token or user.access_token):
                await TokenRefreshService._revoke_google_tokens(user)
            elif provider == IdentityProvider.github and (user.access_token or user.refresh_token):
                await TokenRefreshService._revoke_github_tokens(user, db, github_service, unattended)
        except Exception as revoke_error:
            if unattended:
                raise
            logger.warning(f"Failed to revoke {provider.value} token for {user.username}: {revoke_error}")

        if unattended:
            # One statement, so a login landing during the revocation above keeps its new tokens
            rows = (
                db.query(User)
                .filter(User.id == user.id, User.token_expiry.is_not_distinct_from(session_expiry))
                .update(
                    {User.access_token: None, User.refresh_token: None, User.token_expiry: None, User.updated_at: datetime.now(UTC)},
                    synchronize_session=False,
                )
            )
            db.commit()
            return rows == 1

        user.access_token = None
        user.refresh_token = None
        user.token_expiry = None
        user.updated_at = datetime.now(UTC)
        db.commit()
        return True

    @staticmethod
    async def _revoke_google_tokens(user: User) -> None:
        """Raises unless the tokens are revoked or already invalid."""
        try:
            # The refresh token takes its access tokens with it; without one, revoke the access token
            await TokenRefreshService.revoke_google_token(user.refresh_token or user.access_token)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 400:
                raise
            logger.info(f"Google token of {user.username} was already invalid")
            return
        logger.info(f"Revoked Google token for user {user.username}")

    @staticmethod
    async def _revoke_github_tokens(user: User, db: Session, github_service: GitHubService, unattended: bool = False) -> None:
        """Raises unless the tokens are known to be dead: revoked, rotated or already invalid."""
        token = user.access_token
        # Same condition as ensure_fresh_token; the lock is per process, so it doesn't cover the cron script
        if user.refresh_token and TokenRefreshService.is_token_expired(user):
            async with refresh_locks(user.id):
                # A request may have renewed the pair while we waited
                seen_expiry = user.token_expiry
                db.refresh(user)
                if unattended and user.token_expiry != seen_expiry:
                    return  # a login's new session, not the idle one; end_session leaves it
                token = user.access_token
                if user.refresh_token and TokenRefreshService.is_token_expired(user):
                    try:
                        rotated = await oauth.github.fetch_access_token(grant_type="refresh_token", refresh_token=user.refresh_token)
                    except OAuthError as e:
                        if e.error != "bad_refresh_token":
                            raise
                        # Nothing left to revoke on our side, and revoking can't stop the copy that renewed it
                        logger.error(
                            f"Refresh token of {user.username} was already used or revoked: a copy may be in use "
                            "elsewhere, or the app was revoked on GitHub. Follow up with the user."
                        )
                        return
                    # The old pair is dead now; nobody else holds the fresh one
                    try:
                        if rotated.get("access_token"):
                            await github_service.revoke_oauth_token(rotated["access_token"])
                    except Exception as e:
                        logger.warning(f"Rotated GitHub token of {user.username} not revoked, nobody holds it: {e}")
                    logger.info(f"Revoked GitHub token for user {user.username}")
                    return
        if not token:
            return
        try:
            await github_service.revoke_oauth_token(token)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise
            logger.info(f"GitHub token of {user.username} was already invalid")
            return
        logger.info(f"Revoked GitHub token for user {user.username}")

    @staticmethod
    async def revoke_google_token(token: str) -> None:
        """Revoke a Google access or refresh token; a refresh token also invalidates the access tokens issued from it."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(settings.GOOGLE_REVOKE_URL, data={"token": token})
        response.raise_for_status()

    @staticmethod
    async def refresh_token_for_user(user: User, db: Session) -> dict[str, Any]:
        """Refresh token for a user based on their identity provider.

        Args:
            user: User object
            db: Database session

        Returns:
            Dictionary with refreshed token data

        Raises:
            HTTPException: If provider doesn't support refresh or refresh fails
        """
        if user.identity_provider == IdentityProvider.github:
            return await TokenRefreshService.refresh_github_token(user, db)
        elif user.identity_provider == IdentityProvider.google:
            return await TokenRefreshService.refresh_google_token(user, db)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Token refresh not supported for provider: {user.identity_provider}",
            )

    @staticmethod
    def is_token_expired(user: User) -> bool:
        """Check if user's token is expired or about to expire.

        Args:
            user: User object with token_expiry

        Returns:
            True if token is expired or will expire in next 5 minutes
        """
        if not user.token_expiry:
            # If no expiry is set, assume it might be expired
            return True
        return datetime.now(UTC) >= user.token_expiry - PROVIDER_EXPIRY_BUFFER
