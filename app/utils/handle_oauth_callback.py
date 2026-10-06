import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import IdentityProvider, User
from app.services.jwt_service import JWTService
from app.services.share_service import activate_pending_shares
from app.services.token_refresh_service import TokenRefreshService
from app.utils.session_cookie import set_session_cookie

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
        # GitHub includes expires_in and a refresh token only with expiring tokens (offline_access scope)
        user_to_use = await create_or_update_user(db, user_info, provider, token)

        # The JWT goes into the HttpOnly session cookie only, never into the redirect URL
        jwt_data = JWTService.create_access_token(user_to_use)
        response = RedirectResponse(url=settings.AUTH_SUCCESS_CLIENT_REDIRECT, status_code=status.HTTP_302_FOUND)
        set_session_cookie(response, jwt_data["access_token"], datetime.fromisoformat(jwt_data["expires_at"]))

        logger.info(f"User {user_to_use.username} logged in successfully via {provider}")
        return response

    except Exception as e:
        # A browser navigation: every failure, expected or not, lands on the editor's error page
        logger.error(f"{provider} callback failed: {getattr(e, 'detail', e)}")
        return RedirectResponse(
            status_code=status.HTTP_302_FOUND,
            url=f"{settings.CLIENT_URL}/auth/error?message=authentication_failed&provider={provider}",
        )


async def create_or_update_user(db: Session, user_info: dict[str, Any], provider: str, token: dict[str, Any]) -> User:
    try:
        email = user_info["email"]
        username = user_info["username"]
        full_name = user_info["full_name"]
        external_id = user_info["external_id"]

        # Extract token information
        access_token = token.get("access_token")  # Provider's access token
        refresh_token = token.get("refresh_token")  # None for a GitHub token without expiry
        expires_in = token.get("expires_in")  # Seconds until the provider token expires; None for a GitHub token without expiry

        # Calculate access token expiry time (use UTC for consistency with JWT)
        if expires_in:
            # Google: typically 3600 s. GitHub expiring tokens: 28800 s
            token_expiry = datetime.now(UTC) + timedelta(seconds=int(expires_in))
        elif provider == IdentityProvider.github.value:
            # A GitHub token without expiry cannot be renewed; its session still ends after the same lifetime
            token_expiry = datetime.now(UTC) + TokenRefreshService.GITHUB_TOKEN_LIFETIME
            logger.info("GitHub token without expiry, using the default lifetime")
        else:
            # Default fallback
            token_expiry = datetime.now(UTC) + timedelta(hours=1)
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
            existing_user.updated_at = datetime.now(UTC)
            existing_user.last_seen_at = datetime.now(UTC)

            db.commit()
            logger.info(f"Updated existing {provider} user: {existing_user.username}")
            activate_pending_shares(db, existing_user)
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
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
            )

            db.add(new_user)
            db.commit()
            db.refresh(new_user)

            logger.info(f"Created new {provider} user: {new_user.username}")
            activate_pending_shares(db, new_user)
            return new_user

    except SQLAlchemyError as e:
        logger.error(f"Database error during user creation/update for {provider}: {e}")
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create or update user for {provider}",
        ) from e
