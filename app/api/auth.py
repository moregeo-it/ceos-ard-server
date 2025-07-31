from sqlalchemy.orm import Session
from fastapi.responses import RedirectResponse
from fastapi import APIRouter, Request, Depends, HTTPException, status, Query

import logging

from app.config import settings
from app.oauth.handler import oauth
from app.db.database import get_db
from app.models.user import IdentityProvider
from app.services.auth_service import get_current_user
from app.utils.handle_oauth_callback import handle_oauth_callback
from app.utils.handle_user_info_extractor import extract_github_user_info, extract_google_user_info

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])

oauth_clients = {
    IdentityProvider.github: oauth.github,
    IdentityProvider.google: oauth.google,
}

@router.get("/login")
async def login(request: Request, identity_provider: IdentityProvider = Query(IdentityProvider.github)):
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
        logger.error(f"Failed to initiate GitHub login: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initiate GitHub login",   
        )

@router.get("/callback/github")
async def github_callback(request: Request, db: Session = Depends(get_db)):
    return await handle_oauth_callback(request, db, "github", oauth.github, extract_github_user_info)

@router.get("/callback/google")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    return await handle_oauth_callback(request, db, "google", oauth.google, extract_google_user_info)

@router.get("/logout")
async def logout(request: Request):

    response = RedirectResponse(
        url=settings.LOGOUT_REDIRECT,
        status_code=status.HTTP_302_FOUND
    )

    return response

@router.get("/profile")
async def profile(current_user = Depends(get_current_user)):
    user = current_user["user"]
    access_token = current_user["access_token"]

    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "full_name": user.full_name,
        "access_token": access_token,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "external_id": user.external_id,
        "identity_provider": user.identity_provider,
    }

@router.get("/validate")
async def validate_auth(current_user = Depends(get_current_user)):
    return {
        "authenticated": True,
        "user_id": current_user["user"].id,
        "username": current_user["user"].username
    }