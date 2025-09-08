import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import settings
from app.dependencies import get_github_service
from app.schemas.error import create_error_detail
from app.services.auth_service import get_current_user
from app.services.github_service import GitHubService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pfs", tags=["PFS"])


@router.get(
    "",
    summary="List available PFS types",
    description="Retrieves all available PFS types for CEOS-ARD repository",
    status_code=status.HTTP_200_OK,
)
async def list_pfs_folders(
    current_user=Depends(get_current_user),
    github_service: GitHubService = Depends(get_github_service),
) -> dict[str, Any]:
    try:
        access_token = current_user["access_token"]

        final_owner = settings.CEOS_ARD_OWNER
        final_repo = settings.CEOS_ARD_REPO
        final_branch = settings.CEOS_ARD_MAIN_BRANCH

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
