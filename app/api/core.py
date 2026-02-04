import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse

from app.config import settings
from app.dependencies import get_github_service
from app.schemas.error import create_error_detail
from app.services.auth_service import get_current_user
from app.services.github_service import GitHubService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["Core"])


@router.get(
    "/",
    summary="API Root",
    description="Root endpoint of the CEOS-ARD API",
    status_code=status.HTTP_200_OK,
    response_class=HTMLResponse,
)
async def api_root() -> HTMLResponse:
    """Return an HTML page with API status and link to the Editor client."""
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>CEOS-ARD API</title>
        <style>body {{ font-family: sans-serif; }}</style>
    </head>
    <body>
        <div class="container">
            <h1>CEOS-ARD API</h1>
            <p>
                You are probably looking for the CEOS-ARD web apps:
            </p>
            <ul>
                <li><a href="{settings.CLIENT_URL}">CEOS-ARD Editor</a></li>
                <li>CEOS-ARD Assessor (not yet available)</li>
            </ul>
            <h2>Status</h2>
            <p>Running</p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.get(
    "/pfs",
    summary="List available PFS types",
    description="Retrieves all available PFS types for CEOS-ARD repository",
    status_code=status.HTTP_200_OK,
)
async def list_pfs_folders(
    current_user=Depends(get_current_user),
    github_service: GitHubService = Depends(get_github_service),
) -> dict[str, Any]:
    try:
        access_token = current_user["user"].access_token

        final_owner = settings.CEOS_ARD_ORG
        final_repo = settings.CEOS_ARD_REPO
        final_branch = settings.CEOS_ARD_BRANCH

        logger.info(f"Fetching PFS folders for {final_owner}/{final_repo} on {final_branch} branch")

        pfs_types = await github_service.get_pfs_types(owner=final_owner, repo=final_repo, token=access_token, branch=final_branch)

        return {"pfsTypes": pfs_types}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error listing PFS folders: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=create_error_detail("list PFS folders", e),
        ) from e
