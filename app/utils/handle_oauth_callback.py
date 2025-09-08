import logging
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)


async def handle_oauth_callback(request: Request, db: Session, provider: str, oauth_client, user_info_extractor) -> RedirectResponse:
    try:
        token = await oauth_client.authorize_access_token(request)
        if not token or "access_token" not in token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to retrieve access token from {provider}",
            )

        user_info = await user_info_extractor(token)

        # Validate required fields
        required_fields = ["email", "username", "external_id", "full_name"]
        if not all(user_info.get(field) for field in required_fields):
            missing_fields = [field for field in required_fields if not user_info.get(field)]
            logger.error(f"Missing required user fields from {provider}: {missing_fields}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to retrieve required user information from {provider}",
            )

        # Create or update user in database
        user_to_use = await create_or_update_user(db, user_info, provider)

        # Build redirect URL with access token
        access_token = token.get("access_token")
        redirect_url = (
            f"{settings.AUTH_SUCCESS_REDIRECT}"
            f"?access_token={access_token}"
            f"&user_id={user_to_use.id}"
            f"&username={user_to_use.username}"
            f"&provider={provider}"
        )

        response = RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)

        logger.info(f"User {user_to_use.username} logged in successfully via {provider}")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in {provider} callback: {e}")
        return RedirectResponse(
            status_code=status.HTTP_302_FOUND,
            url=f"{settings.LOGOUT_REDIRECT}/auth/error?message=authentication_failed&provider={provider}",
        )


async def create_or_update_user(db: Session, user_info: dict[str, Any], provider: str) -> User:
    try:
        email = user_info["email"]
        username = user_info["username"]
        full_name = user_info["full_name"]
        external_id = user_info["external_id"]

        existing_user = db.query(User).filter_by(external_id=external_id, identity_provider=provider).first()

        if existing_user:
            existing_user.email = email
            existing_user.username = username
            existing_user.full_name = full_name
            existing_user.updated_at = datetime.now()

            db.commit()
            logger.info(f"Updated existing {provider} user: {existing_user.username}")
            return existing_user
        else:
            new_user = User(
                email=email,
                username=username,
                full_name=full_name,
                external_id=external_id,
                identity_provider=provider,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )

            db.add(new_user)
            db.commit()
            db.refresh(new_user)

            logger.info(f"Created new {provider} user: {new_user.username}")
            return new_user

    except SQLAlchemyError as e:
        logger.error(f"Database error during user creation/update for {provider}: {e}")
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create or update user for {provider}",
        ) from e
