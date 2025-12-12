"""JWT Token Service for internal authentication.

This service generates and validates JWT tokens for client authentication.
Provider tokens (GitHub, Google) are stored server-side and never exposed to clients.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

import jwt
from fastapi import HTTPException, status

from app.config import settings
from app.models.user import IdentityProvider, User

logger = logging.getLogger(__name__)


class JWTService:
    """Service for creating and validating JWT tokens."""

    # Fallback expiry values if provider token expiry is not available
    GITHUB_TOKEN_EXPIRY_HOURS = 8
    GOOGLE_REFRESH_TOKEN_DAYS = 180  # Google refresh tokens last ~6 months

    @staticmethod
    def create_access_token(user: User, provider_token_expiry: datetime | None = None) -> dict[str, Any]:
        """Create a JWT access token for the user.

        The JWT expiry is derived from the provider token:
        - GitHub: Uses actual provider token expiry (no refresh available)
        - Google: Uses refresh token lifetime since we can auto-refresh access token

        Args:
            user: User object with identity provider info and token_expiry
            provider_token_expiry: When the provider's token expires (used if user.token_expiry not set)

        Returns:
            Dictionary with token and expiry info
        """
        # Use the actual provider token expiry from the user object or parameter
        actual_token_expiry = user.token_expiry or provider_token_expiry

        # Calculate JWT expiry based on provider capabilities
        if user.identity_provider == IdentityProvider.github:
            # GitHub: No refresh token, JWT must expire when provider token expires
            if actual_token_expiry:
                expires_delta = actual_token_expiry - datetime.utcnow()
            else:
                # Fallback if token expiry not available
                logger.warning(f"GitHub token expiry not available for user {user.username}, using fallback")
                expires_delta = timedelta(hours=JWTService.GITHUB_TOKEN_EXPIRY_HOURS)
        else:  # google
            # Google: We have refresh token, so JWT can last as long as refresh token (~6 months)
            # Backend automatically refreshes the access token as needed
            # Google doesn't provide refresh token expiry, so we use the standard ~180 day lifetime
            if user.refresh_token:
                expires_delta = timedelta(days=JWTService.GOOGLE_REFRESH_TOKEN_DAYS)
            else:
                # Fallback to access token expiry if no refresh token
                logger.warning(f"Google refresh token not available for user {user.username}, using access token expiry")
                if actual_token_expiry:
                    expires_delta = actual_token_expiry - datetime.utcnow()
                else:
                    expires_delta = timedelta(days=JWTService.GOOGLE_REFRESH_TOKEN_DAYS)

        expiry_time = datetime.utcnow() + expires_delta

        # Create JWT payload
        payload = {
            "sub": user.id,  # Subject (user ID)
            "user_id": user.id,
            "username": user.username,
            "email": user.email,
            "external_id": user.external_id,
            "provider": user.identity_provider.value,  # Serialize enum to string
            "exp": expiry_time,
            "iat": datetime.utcnow(),  # Issued at
            "type": "access",
        }

        # Generate JWT
        token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

        # Calculate seconds until expiry
        expires_in = int(expires_delta.total_seconds())

        logger.info(f"Created JWT token for user {user.username} ({user.identity_provider}), expires in {expires_in}s")

        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "expires_at": expiry_time.isoformat(),
        }

    @staticmethod
    def decode_access_token(token: str) -> dict[str, Any]:
        """Decode and validate a JWT access token.

        Args:
            token: JWT token string

        Returns:
            Decoded token payload

        Raises:
            HTTPException: If token is invalid, expired, or malformed
        """
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])

            # Validate token type
            if payload.get("type") != "access":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token type",
                )

            # Validate required fields
            required_fields = ["user_id", "provider", "exp"]
            if not all(field in payload for field in required_fields):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token payload",
                )

            return payload

        except jwt.ExpiredSignatureError as e:
            logger.warning("JWT token expired")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired. Please refresh or log in again.",
            ) from e
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid JWT token: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token. Please log in again.",
            ) from e
