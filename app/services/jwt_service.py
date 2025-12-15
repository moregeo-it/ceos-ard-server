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
from app.models.user import User

logger = logging.getLogger(__name__)


class JWTService:
    """Service for creating and validating JWT tokens."""

    # JWT expiry: 8 hours for both providers (sliding window with periodic validation)
    JWT_EXPIRY_HOURS = 8

    @staticmethod
    def create_access_token(user: User, provider_token_expiry: datetime | None = None) -> dict[str, Any]:
        """Create a JWT access token for the user.

        JWT expiry is set to 8 hours for both GitHub and Google users.
        With periodic validation (every 5 minutes), active users get fresh JWTs
        before expiry, creating a sliding window session. Inactive users are
        logged out after 8 hours.

        The backend handles provider token differences transparently:
        - GitHub: 8-hour provider token, no refresh (JWT matches provider lifetime)
        - Google: 1-hour provider token, auto-refreshed (JWT independent of provider)

        Args:
            user: User object with identity provider info and token_expiry
            provider_token_expiry: When the provider's token expires (used if user.token_expiry not set)

        Returns:
            Dictionary with token and expiry info
        """
        # Use 8-hour expiry for both providers (sliding window session)
        expires_delta = timedelta(hours=JWTService.JWT_EXPIRY_HOURS)
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
