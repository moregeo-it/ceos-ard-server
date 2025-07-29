from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from fastapi.responses import RedirectResponse
from fastapi import APIRouter, Request, Depends, HTTPException, status

import logging

from datetime import datetime
from app.config import settings
from app.models.user import User
from app.db.dependency import get_db
from app.oauth.github_oauth import oauth
from app.services.auth import get_current_user_from_cookies

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])

LOGOUT_REDIRECT = settings.LOGOUT_REDIRECT
AUTH_SUCCESS_REDIRECT = settings.AUTH_SUCCESS_REDIRECT

@router.get("/github/login")
async def github_login(request: Request):
    try:
        redirect_uri = settings.GITHUB_CALLBACK_URI
        return await oauth.github.authorize_redirect(request, redirect_uri)
    except Exception as e:
        logger.error(f"Failed to initiate GitHub login: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initiate GitHub login",   
        )

@router.get("/github/callback")
async def github_callback(request: Request, db: Session = Depends(get_db)):
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
        github_id = str(profile.get('id'))
        full_name = profile.get('name') or username

        if not email or not username or not github_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to retrieve user information",
            )

        try: 
            existing_user = db.query(User).filter_by(github_id=github_id).first()

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
                    github_id=github_id,
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

        response = RedirectResponse(url=AUTH_SUCCESS_REDIRECT, status_code=status.HTTP_302_FOUND)

        cookie_settings = {
            "httponly": True,
            "samesite": "lax",
            "max_age": 24 * 60 * 60,
            "secure": settings.ENVIRONMENT == "production"
        }

        response.set_cookie(
            key="user_id", 
            **cookie_settings,
            value=str(user_to_use.id),
        )
        response.set_cookie(
            **cookie_settings,
            key="access_token",
            value=access_token['access_token'],
        )

        logger.info(f"User {user_to_use.username} logged in successfully")
        return response

    except Exception as e:
        logger.error(f"Unexpected error in Github callback: {e}")
        return RedirectResponse(
            status_code=status.HTTP_302_FOUND,
            url=f"{LOGOUT_REDIRECT}/auth/error?message=authentication_failed",
        )
    

@router.get("/github/logout")
async def github_logout(request: Request):
    response = RedirectResponse(url=LOGOUT_REDIRECT, status_code=status.HTTP_302_FOUND)

    cookie_clear_settings = {
        "httponly": True,
        "samesite": "lax",
        "secure": settings.ENVIRONMENT == "production"
    }

    response.delete_cookie(key="user_id", **cookie_clear_settings)
    response.delete_cookie(key="access_token", **cookie_clear_settings)

    return response

@router.get("/github/profile")
async def github_profile(current_user = Depends(get_current_user_from_cookies)):
    user = current_user["user"]

    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "full_name": user.full_name,
        "github_id": user.github_id,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }

@router.get("/github/validate")
async def validate_auth(current_user = Depends(get_current_user_from_cookies)):
    return {
        "authenticated": True,
        "user_id": current_user["user"].id,
        "username": current_user["user"].username
    }