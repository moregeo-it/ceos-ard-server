import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
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


@router.get("/logout", summary="Logout user", description="Logout user")
async def logout(current_user=Depends(get_current_user)):
    try:
        response = RedirectResponse(url=settings.LOGOUT_REDIRECT, status_code=status.HTTP_302_FOUND)

        return response
    except Exception as e:
        logger.error(f"Failed to logout user: {e}")
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
    "/validate", summary="Validate JWT and refresh if needed", description="Validate JWT token, auto-refresh provider tokens, and return fresh JWT"
)
async def validate_auth(current_user=Depends(get_current_user)):
    """Validate JWT and provider tokens, return fresh JWT.

    This endpoint is designed for periodic client polling (e.g., every 5 minutes) to:
    - Validate the JWT is still valid
    - Check provider token status
    - For Google: Auto-refresh provider token if expired (transparent)
    - For GitHub: Return 401 if provider token expired (requires re-login)
    - Return a fresh JWT to keep all tabs/browsers in sync

    Returns:
        - 200: Valid, returns fresh JWT with user info
        - 401: JWT expired, provider token expired, or refresh failed
    """
    try:
        user = current_user["user"]
        provider = current_user["provider"]

        # Generate fresh JWT (extends session for valid users)
        jwt_data = JWTService.create_access_token(user)

        logger.info(f"Token validation successful for {user.username} ({provider.value}), issued fresh JWT")

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
            "access_token": jwt_data["access_token"],
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
