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


async def _load_jwt_user(token: str, db: Session) -> User:
    """Decode the JWT and load its user; 401 if either fails."""
    try:
        payload = JWTService.decode_access_token(token)

        user_id = payload.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )

        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            logger.warning(f"User {user_id} from JWT not found in database")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )
        return user

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating JWT token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from e


async def get_current_user(
    token: str = Depends(get_jwt_token),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Validate the JWT and the user's provider token, and return the user.

    Provider tokens are stored server-side and never exposed to clients. An expired provider token
    is renewed with its refresh token (Google always has one, GitHub since expiring tokens); without
    one the session ends (401). Because logout clears the provider token, this is also what refuses
    a JWT after logout.

    Returns:
        Dictionary with user object and provider name

    Raises:
        HTTPException: 401 if the JWT is invalid, the user is unknown, or the provider token is unusable
    """
    user = await _load_jwt_user(token, db)

    try:
        await TokenRefreshService.ensure_fresh_token(user, db)
    except HTTPException as refresh_error:
        logger.warning(f"Provider token of {user.username} ({user.identity_provider.value}) could not be renewed: {refresh_error.detail}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Provider token expired and could not be renewed. Please log in again.",
        ) from refresh_error

    logger.debug(f"JWT validated successfully for user {user.username} ({user.identity_provider})")

    return {
        "user": user,
        "provider": user.identity_provider,
    }


async def get_logout_user(
    token: str = Depends(get_jwt_token),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Like get_current_user, but without the provider token check: logout must still revoke and clear
    the provider tokens when that token expired or can't be refreshed."""
    user = await _load_jwt_user(token, db)
    return {"user": user, "provider": user.identity_provider}


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
    if current_user["user"].identity_provider != IdentityProvider.github:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires GitHub authentication. Please log in with your GitHub account.",
        )
    return current_user
