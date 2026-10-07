import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
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
    async def end_session(user: User, db: Session, github_service: GitHubService) -> None:
        """Revoke the provider tokens (best effort) and clear them, which is what ends the session.

        GitHub: revoking a valid access token also kills its refresh token, but an expired one can't
        be revoked (404), so it is renewed first and the fresh one revoked; the fresh refresh token is
        never stored.
        """
        provider = user.identity_provider
        try:
            if provider == IdentityProvider.google and (user.refresh_token or user.access_token):
                # The refresh token takes its access tokens with it; without one, revoke the access token
                await TokenRefreshService.revoke_google_token(user.refresh_token or user.access_token)
                logger.info(f"Revoked Google token for user {user.username}")
            elif provider == IdentityProvider.github and (user.access_token or user.refresh_token):
                token = user.access_token
                # Same condition as ensure_fresh_token, so no concurrent refresh can run outside the lock
                if user.refresh_token and TokenRefreshService.is_token_expired(user):
                    async with refresh_locks(user.id):
                        try:
                            rotated = await oauth.github.fetch_access_token(grant_type="refresh_token", refresh_token=user.refresh_token)
                            token = rotated.get("access_token") or token
                        except Exception as e:
                            # Already rotated or expired: by a concurrent refresh, or by whoever holds a copy
                            logger.warning(f"Refresh token of {user.username} could not be rotated before revocation: {e}")
                if token:
                    await github_service.revoke_oauth_token(token)
                logger.info(f"Revoked GitHub token for user {user.username}")
        except Exception as revoke_error:
            logger.warning(f"Failed to revoke {provider.value} token for {user.username}: {revoke_error}")

        user.access_token = None
        user.refresh_token = None
        user.token_expiry = None
        user.updated_at = datetime.now(UTC)
        db.commit()

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
