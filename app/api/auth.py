import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.dependencies import get_event_broker, get_github_service
from app.models.user import IdentityProvider
from app.oauth.handler import oauth
from app.services.auth_service import get_current_user, get_jwt_token, get_logout_user
from app.services.github_service import GitHubService
from app.services.jwt_service import JWTService
from app.services.token_refresh_service import TokenRefreshService
from app.utils.handle_oauth_callback import handle_oauth_callback
from app.utils.handle_user_info_extractor import extract_github_user_info, extract_google_user_info
from app.utils.http_utils import internal_errors
from app.utils.session_cookie import bearer_token, clear_session_cookie, set_session_cookie

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)

oauth_clients = {
    IdentityProvider.github: oauth.github,
    IdentityProvider.google: oauth.google,
}


@router.get("/login", summary="Initiate login for a specific identity provider", description="Initiate login for a specific identity provider")
async def initiate_login(request: Request, identity_provider: IdentityProvider = Query(IdentityProvider.github)):
    with internal_errors(f"initiate {identity_provider.value} login", logger):
        if identity_provider in oauth_clients:
            redirect_uri = f"{settings.CALLBACK_BASE_URI}/{identity_provider.value}"
            return await oauth_clients[identity_provider].authorize_redirect(request, redirect_uri)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid identity provider",
            )


@router.get("/callback/github", summary="Handle GitHub OAuth callback", description="Handle GitHub OAuth callback")
async def github_auth_callback(request: Request, db: Session = Depends(get_db)):
    return await handle_oauth_callback(request, db, "github", oauth.github, extract_github_user_info)


@router.get("/callback/google", summary="Handle Google OAuth callback", description="Handle Google OAuth callback")
async def google_auth_callback(request: Request, db: Session = Depends(get_db)):
    return await handle_oauth_callback(request, db, "google", oauth.google, extract_google_user_info)


@router.post("/logout", summary="Logout user", description="Logout user, revoke and clear the provider tokens and the session cookie")
async def logout(
    response: Response,
    current_user=Depends(get_logout_user),
    db: Session = Depends(get_db),
    github_service: GitHubService = Depends(get_github_service),
):
    with internal_errors("logout user", logger):
        user = current_user["user"]
        provider = current_user["provider"]

        # Best effort: clearing the tokens below is what ends the session, even when the provider is unreachable
        try:
            if provider == IdentityProvider.google and (user.refresh_token or user.access_token):
                # The refresh token takes its access tokens with it; without one, revoke the access token
                await TokenRefreshService.revoke_google_token(user.refresh_token or user.access_token)
                logger.info(f"Revoked Google token for user {user.username}")
            elif provider == IdentityProvider.github and user.access_token:
                await github_service.revoke_oauth_token(user.access_token)
                logger.info(f"Revoked GitHub token for user {user.username}")
        except Exception as revoke_error:
            logger.warning(f"Failed to revoke {provider.value} token for {user.username}: {revoke_error}")

        # Clear provider tokens from database
        user.access_token = None
        user.refresh_token = None
        user.token_expiry = None
        user.updated_at = datetime.now(UTC)
        db.commit()

        # The JWT cannot be invalidated; its realtime sockets close with 4001 (re-login before reconnecting).
        closed_sockets = get_event_broker().close_user_connections(user.id)

        clear_session_cookie(response)

        logger.info(f"User {user.username} logged out successfully, provider tokens cleared, {closed_sockets} realtime socket(s) closed")

        return {
            "status": "success",
            "message": f"User {user.username} logged out successfully",
        }


@router.get("/user")
async def current_user(current_user=Depends(get_current_user), token: str = Depends(get_jwt_token)):
    with internal_errors("get current user", logger):
        user = current_user["user"]
        # The editor can't read the HttpOnly cookie, so this is how it learns when the session ends
        expires_at = datetime.fromtimestamp(JWTService.decode_access_token(token)["exp"], tz=UTC)

        return {
            "id": user.id,
            "email": user.email,
            "username": user.username,
            "full_name": user.full_name,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "identity_provider": user.identity_provider,
            "expires_at": expires_at.isoformat(),
        }


@router.post(
    "/validate",
    summary="Validate the session and refresh it if needed",
    description="Validate the JWT, auto-refresh provider tokens, and renew the session if it is nearing expiry",
)
async def validate_auth(request: Request, response: Response, token: str = Depends(get_jwt_token), current_user=Depends(get_current_user)):
    """Validate the session and renew it within 15 minutes of expiry.

    - Google: the provider token is refreshed transparently (in get_current_user)
    - GitHub: 401 when the provider token expired (requires re-login)

    A cookie session is renewed by setting a fresh cookie; the JWT is returned in the body only to
    callers that sent it as a bearer header, so page scripts never get to read it.
    """
    with internal_errors("validate user", logger):
        user = current_user["user"]
        provider = current_user["provider"]

        payload = JWTService.decode_access_token(token)

        # Clients are expected to ping every 5 minutes; refresh within three pings of expiry
        REFRESH_THRESHOLD_MINUTES = 15

        jwt_exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        time_until_expiry = jwt_exp - datetime.now(UTC)
        token_refreshed = time_until_expiry.total_seconds() < REFRESH_THRESHOLD_MINUTES * 60

        if token_refreshed:
            jwt_data = JWTService.create_access_token(user)
            token, jwt_exp = jwt_data["access_token"], datetime.fromisoformat(jwt_data["expires_at"])
        logger.info(
            f"Token validation for {user.username} ({provider.value}): "
            f"JWT valid for {int(time_until_expiry.total_seconds() / 60)} minutes{', issued fresh JWT' if token_refreshed else ''}"
        )

        from_header = bearer_token(request) is not None
        if token_refreshed and not from_header:
            set_session_cookie(response, token, jwt_exp)

        body = {
            "valid": True,
            "user_id": user.id,
            "email": user.email,
            "username": user.username,
            "provider": provider.value,
            "updated_at": user.updated_at,
            "expires_in": int((jwt_exp - datetime.now(UTC)).total_seconds()),
            "expires_at": jwt_exp.isoformat(),
            "token_refreshed": token_refreshed,
        }
        if from_header:
            body |= {"token_type": "Bearer", "access_token": token}
        return body
