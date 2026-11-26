import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse

from app.config import settings
from app.dependencies import get_github_service, get_workspace_service
from app.schemas.workspace import PullRequest, Repository, RepositoryName, SelectionOptionsResponse
from app.schemas.workspace_type import WorkspaceTypeResponse
from app.services.auth_service import get_current_user, require_github_user
from app.services.github_service import GitHubService
from app.services.workspace_service import WorkspaceService
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


@router.get(
    "/types/{type_id}/selection-options",
    summary="List what new workspaces of a type can select",
    description="The options of the upstream repository, for workspace types that select on creation, e.g. the PFS of CEOS-ARD.",
    status_code=status.HTTP_200_OK,
    response_model=SelectionOptionsResponse,
)
async def list_type_selection_options(
    type_id: str,
    current_user=Depends(require_github_user),
    github_service: GitHubService = Depends(get_github_service),
) -> dict:
    workspace_type = get_type(type_id)
    if not workspace_type.selection or not workspace_type.selection.on_create:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"New workspaces of type {type_id} select nothing")
    with internal_errors("list selection options", logger):
        options = await workspace_type.upstream_selection_options(github_service, current_user["user"].access_token)
        return {"options": options}


@router.get(
    "/types/{type_id}/repositories",
    summary="List the repositories of a workspace type",
    description="The repositories new workspaces of a type can choose from, for types that choose one",
    status_code=status.HTTP_200_OK,
    response_model=list[Repository],
)
async def list_type_repositories(
    type_id: str,
    current_user=Depends(require_github_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> list[dict]:
    with internal_errors("list repositories", logger):
        return await workspace_service.list_repositories(get_type(type_id), current_user["user"])


@router.get(
    "/types/{type_id}/repositories/{repository}/pull-requests",
    summary="List the open pull requests of a repository",
    description="The pull requests new workspaces of a type can continue, for types that choose a repository",
    status_code=status.HTTP_200_OK,
    response_model=list[PullRequest],
)
async def list_type_pull_requests(
    type_id: str,
    repository: RepositoryName,
    current_user=Depends(require_github_user),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> list[dict]:
    with internal_errors("list pull requests", logger):
        return await workspace_service.list_pull_requests(get_type(type_id), repository, current_user["user"])
