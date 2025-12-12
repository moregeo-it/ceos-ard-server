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
            "external_id": user.external_id,
            "identity_provider": user.identity_provider,
        }
    except Exception as e:
        logger.error(f"Failed to get current user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail("get current user", e),
        ) from e


@router.get("/validate")
async def validate_auth(current_user=Depends(get_current_user)):
    try:
        user = current_user["user"]

        return {
            "id": user.id,
            "email": user.email,
            "username": user.username,
            "full_name": user.full_name,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "external_id": user.external_id,
            "identity_provider": user.identity_provider,
        }
    except Exception as e:
        logger.error(f"Failed to validate user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail("validate user", e),
        ) from e
