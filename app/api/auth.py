import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.models.user import IdentityProvider
from app.oauth.handler import oauth
from app.schemas.error import create_error_detail
from app.services.auth_service import get_current_user
from app.services.jwt_service import JWTService
from app.utils.handle_oauth_callback import handle_oauth_callback
from app.utils.handle_user_info_extractor import extract_github_user_info, extract_google_user_info

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)

oauth_clients = {
    IdentityProvider.github: oauth.github,
    IdentityProvider.google: oauth.google,
}


@router.get("/login", summary="Initiate login for a specific identity provider", description="Initiate login for a specific identity provider")
async def initiate_login(request: Request, identity_provider: IdentityProvider = Query(IdentityProvider.github)):
    try:
        if identity_provider in oauth_clients:
            redirect_uri = f"{settings.CALLBACK_BASE_URI}/{identity_provider.value}"
            return await oauth_clients[identity_provider].authorize_redirect(request, redirect_uri)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid identity provider",
            )
    except Exception as e:
        logger.error(f"Failed to initiate {identity_provider.value} login: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail(f"initiate {identity_provider.value} login", e),
        ) from e


@router.get("/callback/github", summary="Handle GitHub OAuth callback", description="Handle GitHub OAuth callback")
async def github_auth_callback(request: Request, db: Session = Depends(get_db)):
    return await handle_oauth_callback(request, db, "github", oauth.github, extract_github_user_info)


@router.get("/callback/google", summary="Handle Google OAuth callback", description="Handle Google OAuth callback")
async def google_auth_callback(request: Request, db: Session = Depends(get_db)):
    return await handle_oauth_callback(request, db, "google", oauth.google, extract_google_user_info)


@router.post("/logout", summary="Logout user", description="Logout user and clear provider tokens")
async def logout(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        user = current_user["user"]
        provider = current_user["provider"]

        # Revoke token with OAuth provider
        try:
            if provider == IdentityProvider.google and user.refresh_token:
                # Revoke Google refresh token
                await oauth.google.revoke_token(user.refresh_token)
                logger.info(f"Revoked Google token for user {user.username}")
            elif provider == IdentityProvider.github and user.access_token:
                # Revoke GitHub token
                await oauth.github.revoke_token(user.access_token)
                logger.info(f"Revoked GitHub token for user {user.username}")
        except Exception as revoke_error:
            # Log but don't fail - still clear from DB
            logger.warning(f"Failed to revoke {provider.value} token for {user.username}: {revoke_error}")

        # Clear provider tokens from database
        user.access_token = None
        user.refresh_token = None
        user.token_expiry = None
        user.updated_at = datetime.utcnow()
        db.commit()

        logger.info(f"User {user.username} logged out successfully, provider tokens cleared")

        return {
            "status": "success",
            "message": f"User {user.username} logged out successfully",
        }
    except Exception as e:
        logger.error(f"Failed to logout user: {e}")
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail("logout user", e),
        ) from e


@router.get("/user")
async def current_user(current_user=Depends(get_current_user)):
    try:
        user = current_user["user"]

        return {
            "id": user.id,
            "email": user.email,
            "username": user.username,
            "full_name": user.full_name,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "identity_provider": user.identity_provider,
        }
    except Exception as e:
        logger.error(f"Failed to get current user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail("get current user", e),
        ) from e


@router.post(
    "/validate",
    summary="Validate JWT and refresh if needed",
    description="Validate JWT token, auto-refresh provider tokens, and return fresh JWT if nearing expiry",
)
async def validate_auth(authorization: str = Depends(HTTPBearer()), current_user=Depends(get_current_user)):
    """Validate JWT and provider tokens, return fresh JWT only if nearing expiry.

    This endpoint is designed for periodic client polling (e.g., every 5 minutes) to:
    - Validate the JWT is still valid
    - Check provider token status
    - For Google: Auto-refresh provider token if expired (transparent)
    - For GitHub: Return 401 if provider token expired (requires re-login)
    - Return a fresh JWT ONLY if current JWT expires within 3 × ping_interval (15 minutes)

    This optimization reduces unnecessary JWT generation while ensuring tokens are
    refreshed before they expire.

    Returns:
        - 200: Valid, returns current or fresh JWT with user info
        - 401: JWT expired, provider token expired, or refresh failed
    """
    try:
        user = current_user["user"]
        provider = current_user["provider"]

        # Decode current JWT to check expiry
        jwt_token = authorization.credentials
        payload = JWTService.decode_access_token(jwt_token)

        # Configuration: ping interval in minutes
        PING_INTERVAL_MINUTES = 5
        REFRESH_THRESHOLD_MINUTES = 3 * PING_INTERVAL_MINUTES  # 15 minutes

        # Check if JWT is nearing expiry
        jwt_exp = datetime.fromtimestamp(payload["exp"])
        time_until_expiry = jwt_exp - datetime.utcnow()
        should_refresh = time_until_expiry.total_seconds() < (REFRESH_THRESHOLD_MINUTES * 60)

        if should_refresh:
            # Generate fresh JWT (extends session)
            jwt_data = JWTService.create_access_token(user)
            logger.info(
                f"Token validation for {user.username} ({provider.value}): "
                f"JWT expiring in {int(time_until_expiry.total_seconds() / 60)} minutes, issued fresh JWT"
            )
            access_token = jwt_data["access_token"]
            token_refreshed = True
        else:
            # Return current JWT (still valid for more than 15 minutes)
            logger.info(
                f"Token validation for {user.username} ({provider.value}): "
                f"JWT valid for {int(time_until_expiry.total_seconds() / 60)} minutes, no refresh needed"
            )
            jwt_data = {
                "access_token": jwt_token,
                "token_type": "Bearer",
                "expires_in": int(time_until_expiry.total_seconds()),
                "expires_at": jwt_exp.isoformat(),
            }
            access_token = jwt_token
            token_refreshed = False

        return {
            "valid": True,
            "user_id": user.id,
            "email": user.email,
            "username": user.username,
            "provider": provider.value,
            "updated_at": user.updated_at,
            "token_type": jwt_data["token_type"],
            "expires_in": jwt_data["expires_in"],
            "expires_at": jwt_data["expires_at"],
            "access_token": access_token,
            "token_refreshed": token_refreshed,  # Indicates if new JWT was issued
        }
    except HTTPException:
        # Re-raise authentication errors (401) from get_current_user
        raise
    except Exception as e:
        logger.error(f"Failed to validate user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail("validate user", e),
        ) from e
