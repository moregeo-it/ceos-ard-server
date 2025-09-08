import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)


async def validate_github_token(access_token: str, db: Session) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.get(settings.GITHUB_API_BASE_URL + "/user", headers=headers)

        if response.status_code != 200:
            logger.debug(f"GitHub token validation failed with status: {response.status_code}")
            return None

        res = response.json()
        external_id = str(res.get("id"))

        if not external_id:
            logger.warning("GitHub API response missing user ID")
            return None

        user = db.query(User).filter(User.external_id == external_id, User.identity_provider == "github").first()

        if not user:
            logger.debug(f"GitHub user with external_id {external_id} not found in database")
            return None

        return {"user": user, "access_token": access_token, "provider": "github"}

    except httpx.RequestError as e:
        logger.error(f"Network error validating GitHub token: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error validating GitHub token: {e}")
        return None


async def validate_google_token(access_token: str, db: Session) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.get(settings.GOOGLE_API_BASE_URL + "/oauth2/v1/userinfo", headers=headers)

        if response.status_code != 200:
            logger.debug(f"Google token validation failed with status: {response.status_code}")
            return None
        res = response.json()
        external_id = str(res.get("id"))
        if not external_id:
            logger.warning("Google API response missing user ID")
            return None
        user = db.query(User).filter(User.external_id == external_id, User.identity_provider == "google").first()
        if not user:
            logger.debug(f"Google user with external_id {external_id} not found in database")
            return None
        return {"user": user, "access_token": access_token, "provider": "google"}
    except httpx.RequestError as e:
        logger.error(f"Network error validating Google token: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error validating Google token: {e}")
        return None
