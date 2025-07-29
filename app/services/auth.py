from sqlalchemy.orm import Session
from fastapi import HTTPException, Depends, Request, status

import httpx

from app.models.user import User
from app.db.dependency import get_db

async def get_current_user(request: Request, db: Session = Depends(get_db)):
    user_id = request.headers.get("user_id")
    access_token = request.headers.get("access_token")

    if not user_id or not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated - missing headers",
        )
    
    try:
        user = db.query(User).filter(User.id == user_id).first()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated - user not found",
            )

        async with httpx.AsyncClient() as client:
            headers = { "Authorization": f"Bearer {access_token}"}
            response = await client.get("https://api.github.com/user", headers=headers)

        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated - invalid access token",
            )
        
        return {
            "user": user,
            "github_token": access_token
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTPP_503_SERVICE_UNAVAILABLE,
            detail="Unablee to validate token with Github",
        )