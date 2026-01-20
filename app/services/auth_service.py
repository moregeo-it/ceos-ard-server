import logging
from typing import Any

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.user import IdentityProvider, User
from app.services.jwt_service import JWTService
from app.services.token_refresh_service import TokenRefreshService

logger = logging.getLogger(__name__)


bearer_scheme = HTTPBearer(auto_error=False)


async def get_jwt_token(
    request: Request,
    authorization: str | None = Query(default=None),
) -> str:
    """Extract JWT access token.

    Priority:
    1) Authorization header via HTTP Bearer scheme (expects: "Bearer <token>")
    2) Query parameter "authorization" (expects: "<token>")
    """
    credentials = await bearer_scheme(request)
    if credentials and credentials.credentials:
        return credentials.credentials

    if authorization:
        token = authorization.strip()
        if token.startswith("Bearer "):
            token = token[7:].strip()
        if token:
            return token

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated - missing token",
    )


async def get_current_user(
    token: str = Depends(get_jwt_token),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Validate JWT token and return user.

    The client sends a JWT token (not a provider token).
    This function:
    1. Decodes and validates the JWT
    2. Retrieves the user from database
    3. For Google users: Automatically refreshes provider token if needed

    Provider tokens are stored server-side and never exposed to clients.

    Args:
        jwt_token: JWT token from Authorization header or query param
        db: Database session

    Returns:
        Dictionary with user object and provider name

    Raises:
        HTTPException: If token is invalid or user not found
    """
    try:
        # Decode and validate JWT token
        payload = JWTService.decode_access_token(token)

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


async def require_github_user(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Require user to be authenticated via GitHub.

    This dependency wraps get_current_user and adds GitHub provider validation.
    Use this for workspace-related endpoints that require GitHub integration.

    Args:
        current_user: User dict from get_current_user dependency

    Returns:
        User dict if authenticated with GitHub

    Raises:
        HTTPException: 403 if user authenticated with non-GitHub provider
    """
    user = current_user["user"]
    if user.identity_provider != IdentityProvider.github:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires GitHub authentication. Please log in with your GitHub account.",
        )
    return current_user
