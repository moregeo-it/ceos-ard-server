from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from fastapi.responses import RedirectResponse
from fastapi import APIRouter, Request, Depends, HTTPException, status, Query

import logging

from datetime import datetime
from app.config import settings
from app.models.user import User
from app.db.dependency import get_db
from app.oauth.github_oauth import oauth
from app.services.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])

LOGOUT_REDIRECT = settings.LOGOUT_REDIRECT
AUTH_SUCCESS_REDIRECT = settings.AUTH_SUCCESS_REDIRECT

@router.get("/login")
async def login(request: Request, identity_provider: str = Query("github", enum=["github", "google"])):
    try:
        if identity_provider == "github":
            redirect_uri = settings.CALLBACK_URI
            return await oauth.github.authorize_redirect(request, redirect_uri)
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

@router.get("/callback")
async def login_callback(request: Request, db: Session = Depends(get_db)):
    try:
        access_token = await oauth.github.authorize_access_token(request)
        if not access_token or "access_token" not in access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to retrieve access token",
            )
        
        user_response = await oauth.github.get('user', token=access_token)
        if user_response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to retrieve user information",
            )

        profile = user_response.json()
        
        email = profile.get('email')
        username = profile.get('login')
        external_id = str(profile.get('id'))
        full_name = profile.get('name') or username

        if not email or not username or not external_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to retrieve user information",
            )

        try: 
            existing_user = db.query(User).filter_by(external_id=external_id).first()

            if existing_user:
                existing_user.email = email
                existing_user.username = username
                existing_user.full_name = full_name
                existing_user.updated_at = datetime.now()

                db.commit()
                user_to_use = existing_user
                logger.info(f"User {user_to_use.username} updated")
            else:
                new_user = User(
                    email=email,
                    username=username,
                    full_name=full_name,
                    external_id=external_id,
                    identity_provider="github",
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )

                db.add(new_user)
                db.commit()
                db.refresh(new_user)
                user_to_use = new_user
                logger.info(f"User {user_to_use.username} created")
        except SQLAlchemyError as e:
            logger.error(f"Databse error during user creation/update: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create or update user",
            )

        response = RedirectResponse(
            status_code=status.HTTP_302_FOUND,
            url=f"{AUTH_SUCCESS_REDIRECT}?access_token={access_token['access_token']}&user_id={user_to_use.id}"
        )

        logger.info(f"User {user_to_use.username} logged in successfully")

        return response

    except Exception as e:
        logger.error(f"Unexpected error in Github callback: {e}")
        return RedirectResponse(
            status_code=status.HTTP_302_FOUND,
            url=f"{LOGOUT_REDIRECT}/auth/error?message=authentication_failed",
        )
    

@router.get("/logout")
async def github_logout(request: Request):
    response = RedirectResponse(url=LOGOUT_REDIRECT, status_code=status.HTTP_302_FOUND)

    return response

@router.get("/profile")
async def github_profile(current_user = Depends(get_current_user)):
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

@router.get("/validate")
async def validate_auth(current_user = Depends(get_current_user)):
    return {
        "authenticated": True,
        "user_id": current_user["user"].id,
        "username": current_user["user"].username
    }