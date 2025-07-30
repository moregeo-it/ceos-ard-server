from fastapi import HTTPException, APIRouter, Request, Query, status, Depends
from typing import Optional, Dict, Any

import logging

from app.config import settings
from app.services.auth import get_current_user
from app.services.github import github_service
from app.utils.sanitization import sanitize_query_params

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pfs", tags=["PFS"])

@router.get("/list-pfs-folders")
async def list_pfs_folders(
    request: Request,
    current_user = Depends(get_current_user),
    branch: Optional[str] = Query(None, example="main"),
    owner: Optional[str] = Query(None, example="ceos-org"),
    repo: Optional[str] = Query(None, example="ceos-ard"),
) -> Dict[str, Any]:
    try:
        github_token = current_user["access_token"]

        query_params = {
            "branch": branch,
            "owner": owner,
            "repo": repo
        }

        sanitized_params = sanitize_query_params(query_params)

        final_owner = sanitized_params["owner"] or settings.CEOS_ARD_OWNER
        final_repo = sanitized_params["repo"] or settings.CEOS_ARD_REPO
        final_branch = sanitized_params["branch"] or settings.CEOS_ARD_MAIN_BRANCH

        logger.info(f"Fetching PFS folders for {final_owner}/{final_repo} on {final_branch} branch")

        pfs_folders = await github_service.get_pfs_folders(
            final_owner, 
            final_repo, 
            github_token,
            final_branch
        )

        return {
            "success": True,
            "pfsFolders": pfs_folders,
            "message": f"Successfully fetched PFS folders for {final_owner}/{final_repo} on {final_branch} branch"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error listing PFS folders: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while listing PFS folders"
        )