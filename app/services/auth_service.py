from sqlalchemy.orm import Session
from fastapi.security import HTTPBearer
from typing import List, Dict, Any, Callable
from fastapi import HTTPException, Depends, status

import logging

from app.db.database import get_db
from app.utils.token_validator import validate_google_token, validate_github_token

logger = logging.getLogger(__name__)

TOKEN_VALIDATORS: List[Callable] = [
    validate_google_token,
    validate_github_token
]

async def get_current_user(
        authorization: str = Depends(HTTPBearer()),
        db: Session = Depends(get_db)
) -> Dict[str, Any]:
    access_token = authorization.credentials

    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated - missing headers",
        )
    
    try:
        for validator in TOKEN_VALIDATORS:
            user_info = await validator(access_token, db)
            if user_info:
                logger.debug(f"Token validated successfully with {user_info.get('provider', 'unknown')} provider")
                return user_info
            
        logger.warning(f"Token validation failed for all {len(TOKEN_VALIDATORS)} providers")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated - token validation failed",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating token: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to validate token",
        )