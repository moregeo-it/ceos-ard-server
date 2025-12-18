import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.user import IdentityProvider, User
from app.oauth.handler import oauth

logger = logging.getLogger(__name__)


class TokenRefreshService:
    """Service for refreshing OAuth access tokens.

    Note: GitHub standard OAuth does not support refresh tokens.
    Only Google OAuth refresh is supported.
    """

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
                user.token_expiry = datetime.now() + timedelta(seconds=expires_in)

            user.updated_at = datetime.now()
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
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GitHub does not support token refresh. Please log in again.",
            )
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

        # Add 5 minute buffer to refresh before actual expiry
        return datetime.now() >= (user.token_expiry - timedelta(minutes=5))
