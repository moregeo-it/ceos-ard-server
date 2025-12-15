import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import IdentityProvider, User
from app.services.jwt_service import JWTService

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

        # Create or update user in database (stores provider tokens)
        user_to_use = await create_or_update_user(db, user_info, provider, token)

        # Generate JWT token for client (never expose provider token)
        # JWT expiry is derived from provider token expiry stored in user.token_expiry
        jwt_data = JWTService.create_access_token(user_to_use)

        # Build redirect URL with JWT token (not provider token)
        redirect_url = (
            f"{settings.AUTH_SUCCESS_CLIENT_REDIRECT}"
            f"?access_token={jwt_data['access_token']}"
            f"&token_type={jwt_data['token_type']}"
            f"&expires_in={jwt_data['expires_in']}"
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


async def create_or_update_user(db: Session, user_info: dict[str, Any], provider: str, token: dict[str, Any]) -> User:
    try:
        email = user_info["email"]
        username = user_info["username"]
        full_name = user_info["full_name"]
        external_id = user_info["external_id"]

        # Extract token information
        access_token = token.get("access_token")  # Provider's access token
        refresh_token = token.get("refresh_token")
        expires_in = token.get("expires_in")

        # Calculate access token expiry time (use UTC for consistency with JWT)
        if expires_in:
            # Google provides expires_in (typically 3600 seconds = 1 hour)
            token_expiry = datetime.utcnow() + timedelta(seconds=int(expires_in))
        elif provider == IdentityProvider.github.value:
            # GitHub doesn't provide expires_in, but tokens typically last 8 hours
            token_expiry = datetime.utcnow() + timedelta(hours=8)
            logger.info("GitHub token created with default 8-hour expiry")
        else:
            # Default fallback
            token_expiry = datetime.utcnow() + timedelta(hours=1)
            logger.warning(f"No expires_in for {provider}, using 1-hour default")

        provider_enum = IdentityProvider(provider)

        existing_user = db.query(User).filter_by(external_id=external_id, identity_provider=provider_enum).first()

        if existing_user:
            existing_user.email = email
            existing_user.username = username
            existing_user.full_name = full_name
            existing_user.access_token = access_token  # Store provider token
            existing_user.refresh_token = refresh_token
            existing_user.token_expiry = token_expiry
            existing_user.updated_at = datetime.utcnow()

            db.commit()
            logger.info(f"Updated existing {provider} user: {existing_user.username}")
            return existing_user
        else:
            new_user = User(
                email=email,
                username=username,
                full_name=full_name,
                external_id=external_id,
                identity_provider=provider_enum,
                access_token=access_token,  # Store provider token
                refresh_token=refresh_token,
                token_expiry=token_expiry,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
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
