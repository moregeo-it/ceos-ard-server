from sqlalchemy.orm import Session
from fastapi.security import HTTPBearer
from fastapi import HTTPException, Depends, Request, status

import httpx

from app.models.user import User
from app.db.dependency import get_db

async def get_current_user(authorization: str = Depends(HTTPBearer()), db: Session = Depends(get_db)):
    access_token = authorization.credentials

    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated - missing headers",
        )
    
    try:
        async with httpx.AsyncClient() as client:
            headers = { "Authorization": f"Bearer {access_token}"}
            response = await client.get("https://api.github.com/user", headers=headers)

        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated - invalid access token",
            )
        
        res = response.json()
        external_id = str(res.get("id"))

        user = db.query(User).filter(User.external_id == external_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="USer not found - login again",
            )
        
        return {
            "user": user,
            "access_token": access_token
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTPP_503_SERVICE_UNAVAILABLE,
            detail="Unablee to validate token with Github",
        )