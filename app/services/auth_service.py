import logging
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.user import IdentityProvider, User
from app.services.jwt_service import JWTService
from app.services.token_refresh_service import TokenRefreshService

logger = logging.getLogger(__name__)


async def get_current_user(authorization: str = Depends(HTTPBearer()), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Validate JWT token and return user.

    The client sends a JWT token (not a provider token).
    This function:
    1. Decodes and validates the JWT
    2. Retrieves the user from database
    3. For Google users: Automatically refreshes provider token if needed

    Provider tokens are stored server-side and never exposed to clients.

    Args:
        authorization: Bearer token from client (JWT)
        db: Database session

    Returns:
        Dictionary with user object and provider name

    Raises:
        HTTPException: If token is invalid or user not found
    """
    jwt_token = authorization.credentials

    if not jwt_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated - missing token",
        )

    try:
        # Decode and validate JWT token
        payload = JWTService.decode_access_token(jwt_token)

        user_id = payload.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )

        # Retrieve user from database
        user = db.query(User).filter(User.id == user_id).first()

        if not user:
            logger.warning(f"User {user_id} from JWT not found in database")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        # Check provider token expiry and handle accordingly
        if user.identity_provider == IdentityProvider.google:
            # Google: Auto-refresh if token expired
            if TokenRefreshService.is_token_expired(user):
                logger.info(f"Google provider token expired for user {user.username}, auto-refreshing")
                try:
                    await TokenRefreshService.refresh_google_token(user, db)
                    logger.info(f"Successfully auto-refreshed Google token for {user.username}")
                except Exception as refresh_error:
                    logger.error(f"Failed to auto-refresh Google token for {user.username}: {refresh_error}")
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Provider token expired and refresh failed. Please log in again.",
                    ) from refresh_error
        elif user.identity_provider == IdentityProvider.github:
            # GitHub: No refresh available, check if token expired and require re-login
            if TokenRefreshService.is_token_expired(user):
                logger.warning(f"GitHub provider token expired for user {user.username}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="GitHub access token expired. Please log in again.",
                )

        logger.debug(f"JWT validated successfully for user {user.username} ({user.identity_provider})")

        return {
            "user": user,
            "provider": user.identity_provider,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating JWT token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from e
