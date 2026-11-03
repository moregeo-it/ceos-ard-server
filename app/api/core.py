import logging

from fastapi import APIRouter, Depends, status
from fastapi.responses import HTMLResponse

from app.config import settings
from app.dependencies import get_github_service
from app.schemas.workspace import PFSTypesResponse
from app.schemas.workspace_type import WorkspaceTypeResponse
from app.services.auth_service import get_current_user
from app.services.github_service import GitHubService
from app.utils.http_utils import internal_errors
from app.workspace_types import all_types, available_types, get_type

logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["Core"])


@router.get(
    "/",
    summary="API Root",
    description="Root endpoint of the Editor API",
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
        <title>Editor API</title>
        <style>body {{ font-family: sans-serif; }}</style>
    </head>
    <body>
        <div class="container">
            <h1>Editor API</h1>
            <p>
                You are probably looking for the <a href="{settings.CLIENT_URL}">Editor</a>.
            </p>
            <h2>Status</h2>
            <p>Running</p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.get(
    "/pfs",
    response_model=PFSTypesResponse,
    summary="List available PFS types",
    description="Retrieves all available PFS types for CEOS-ARD repository",
    status_code=status.HTTP_200_OK,
)
async def list_pfs_folders(
    current_user=Depends(get_current_user),
    github_service: GitHubService = Depends(get_github_service),
) -> PFSTypesResponse:
    with internal_errors("list PFS folders", logger):
        access_token = current_user["user"].access_token

        upstream = get_type("ceos-ard").upstream
        final_owner = upstream.owner
        final_repo = upstream.repo
        final_branch = upstream.branch

        logger.info(f"Fetching PFS folders for {final_owner}/{final_repo} on {final_branch} branch")

        pfs_types = await github_service.get_pfs_types(owner=final_owner, repo=final_repo, token=access_token, branch=final_branch)
        response_pfs_types = [{"id": pfs} for pfs in pfs_types]
        return {"pfs_types": response_pfs_types}


@router.get(
    "/types",
    summary="List workspace types",
    description="Every workspace type the server knows, with what the editor needs to know about them",
    status_code=status.HTTP_200_OK,
    response_model=list[WorkspaceTypeResponse],
    response_model_exclude_none=True,
)
async def list_workspace_types(current_user=Depends(get_current_user)) -> list[dict]:
    available = available_types()
    return [workspace_type.to_public(workspace_type in available) for workspace_type in all_types()]
