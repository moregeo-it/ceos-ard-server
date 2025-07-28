from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse

from app.config import settings
from app.models.user import User
from sqlalchemy.orm import Session
from app.db.dependency import get_db
from datetime import datetime, timedelta
from app.oauth.github_oauth import oauth

router = APIRouter(prefix="/auth", tags=["Auth"])

LOGOUT_REDIRECT = settings.LOGOUT_REDIRECT
AUTH_SUCCESS_REDIRECT = settings.AUTH_SUCCESS_REDIRECT

@router.get("/github/login")
async def github_login(request: Request):
    redirect_uri = settings.GITHUB_CALLBACK_URI
    return await oauth.github.authorize_redirect(request, redirect_uri)

@router.get("/github/callback")
async def github_callback(request: Request, db: Session = Depends(get_db)):
    access_token = await oauth.github.authorize_access_token(request)
    user_response = await oauth.github.get('user', token=access_token)
    profile = user_response.json()
    print(access_token)
    print(access_token['access_token'])
    username = profile.get('login')
    githhub_id = str(profile.get('id'))
    email = profile.get('email') or None
    full_name = profile.get('name') or username

    existing_user = db.query(User).filter_by(github_id=githhub_id).first()

    if existing_user:
        existing_user.email = email
        existing_user.username = username
        existing_user.full_name = full_name
        existing_user.updated_at = datetime.now()
        db.commit()
    else:
        new_user = User(
            email=email,
            username=username,
            full_name=full_name,
            github_id=githhub_id,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

    response = RedirectResponse(url=AUTH_SUCCESS_REDIRECT, status_code=302)

    response.set_cookie(
        key="user_id", 
        value=str(new_user.id or existing_user.id), 
        httponly=True, secure=True, samesite="lax"
    )
    response.set_cookie(
        key="access_token", 
        value=access_token['access_token']
    )

    return response
    

@router.get("/github/logout")
async def github_logout(request: Request):
    response = RedirectResponse(url=LOGOUT_REDIRECT, status_code=302)
    response.delete_cookie(key="user_id")
    response.delete_cookie(key="access_token")
    return response

@router.get("/github/profile")
async def github_profile(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if user_id is None:
        return {"message": "Not authenticated"}
    user = db.query(User).filter_by(id=user_id).first()
    if user is None:
        return {"message": "User not found"}
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "full_name": user.full_name,
        "github_id": user.github_id,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }