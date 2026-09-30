import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.dependencies import get_file_service
from app.schemas.workspace import (
    CommitRequest,
    CommitResult,
    CreateFileRequest,
    FileContextResponse,
    FilePatchRequest,
    FileResponse,
    FileSearchResponse,
    ListDiffsResponse,
)
from app.services.auth_service import require_github_user
from app.services.file_service import FileService
from app.utils.git_utils import format_commit
from app.utils.http_utils import USER_CONTENT_HEADERS, internal_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["Files"])


@router.get(
    "/{workspace_id}/files",
    summary="List files in a workspace",
    description="List files and folders in a workspace",
    response_model=list[FileResponse],
    status_code=status.HTTP_200_OK,
)
async def list_workspace_files(
    workspace_id: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
    path: str | None = Query(default="/", description="Path to list files from"),
    recurse: bool = Query(default=False, description="Whether to list files recursively"),
):
    with internal_errors("list workspace files", logger):
        return await file_service.get_workspace_files(db=db, path=path, workspace_id=workspace_id, user_id=current_user["user"].id, recurse=recurse)


@router.post(
    "/{workspace_id}/files",
    summary="Create a file or folder in a workspace",
    description="Create a file or folder in a workspace",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create(
    workspace_id: str,
    create_file_request: CreateFileRequest,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    with internal_errors("create file or folder", logger):
        return await file_service.create(db=db, workspace_id=workspace_id, request_data=create_file_request, user_id=current_user["user"].id)


@router.get(
    "/{workspace_id}/files/{file_path:path}",
    summary="Read content of a file",
    description="Read content of a file",
)
async def read_file_content(
    file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    with internal_errors("read file", logger):
        file_info = await file_service.read_file_content(db=db, workspace_id=workspace_id, file_path=file_path, user_id=current_user["user"].id)

        return Response(
            content=file_info["content"], media_type=file_info["media_type"], status_code=status.HTTP_200_OK, headers=USER_CONTENT_HEADERS
        )


@router.put(
    "/{workspace_id}/files/{file_path:path}",
    summary="Store content of a file",
    description="Store content of a file",
    response_model=FileResponse,
    status_code=status.HTTP_200_OK,
)
async def store_file_content(
    request: Request,
    file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    with internal_errors("store file", logger):
        return await file_service.store_file_content(
            db=db,
            workspace_id=workspace_id,
            file_path=file_path,
            content=await request.body(),
            user_id=current_user["user"].id,
        )


@router.delete(
    "/{workspace_id}/files/{file_path:path}",
    summary="Delete a file or folder in a workspace",
    description="Delete a file or folder in a workspace",
)
async def delete(
    file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    with internal_errors("delete file", logger):
        deleted_file = await file_service.delete(db=db, file_path=file_path, workspace_id=workspace_id, user_id=current_user["user"].id)

        if deleted_file["tracked"]:
            return JSONResponse(content=deleted_file["file_details"], status_code=status.HTTP_200_OK)
        else:
            return Response(content=None, status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/{workspace_id}/files/{file_path:path}",
    summary="Update file metadata or operations",
    description="Perform file operations such rename or revert changes",
    response_model=FileResponse,
    status_code=status.HTTP_200_OK,
)
async def patch_file(
    file_path: str,
    workspace_id: str,
    operation_request: FilePatchRequest,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    with internal_errors("update file", logger):
        return await file_service.update_file(
            db=db,
            file_path=file_path,
            workspace_id=workspace_id,
            operation_request=operation_request,
            user_id=current_user["user"].id,
        )


@router.get(
    "/{workspace_id}/search",
    summary="Search files in a workspace",
    description="search through all files in a workspace",
    response_model=list[FileSearchResponse],
    status_code=status.HTTP_200_OK,
)
async def search_files(
    workspace_id: str,
    query: str = Query(description="Search terms to look for in files and folders"),
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    with internal_errors("search files", logger):
        return await file_service.search_files(db=db, workspace_id=workspace_id, search_query=query, user_id=current_user["user"].id)


@router.get(
    "/{workspace_id}/diffs",
    summary="Get list of changed files",
    description="Retrieve a list of changed files in a workspace - includes untracked files",
    response_model=ListDiffsResponse,
    status_code=status.HTTP_200_OK,
)
async def get_changed_files(
    workspace_id: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    with internal_errors("get changed files", logger):
        return {"files": await file_service.get_changed_files(db=db, workspace_id=workspace_id, user_id=current_user["user"].id)}


@router.put(
    "/{workspace_id}/diffs",
    response_model=CommitResult,
    status_code=status.HTTP_200_OK,
    summary="Create a commit of the changes",
    description="Creates a new git commit for all current workspace changes and pushes it to GitHub. "
    "When the branch on GitHub moved ahead, the remote changes are merged into the workspace automatically; "
    "if they conflict with the committed changes, a 409 with the conflicting files is returned instead.",
)
async def commit_changes(
    workspace_id: str,
    commit: CommitRequest,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    file_service: FileService = Depends(get_file_service),
):
    with internal_errors("commit changes", logger):
        commit, merged_remote = await file_service.persist_changes(
            db=db,
            workspace_id=workspace_id,
            message=commit.message,
            user=current_user["user"],
        )
        return {**format_commit(commit), "merged_remote": merged_remote}


@router.get(
    "/{workspace_id}/diffs/{file_path:path}",
    summary="Get diff for a specific file",
    description="Retrieve the diff for a specific file in a workspace",
)
async def get_file_diff(
    file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    diff = await file_service.get_file_diff(db=db, file_path=file_path, workspace_id=workspace_id, user_id=current_user["user"].id)
    if not diff:
        diff = ""

    return Response(content=diff, media_type="text/plain; charset=utf-8", status_code=status.HTTP_200_OK)


@router.get(
    "/{workspace_id}/context/{file_path:path}",
    summary="Get aditional context for a specific file",
    description="Retrieve additional context for a specific file in a workspace",
    response_model=FileContextResponse,
    status_code=status.HTTP_200_OK,
)
async def get_file_context(
    file_path: str,
    workspace_id: str,
    db: Session = Depends(get_db),
    file_service: FileService = Depends(get_file_service),
    current_user: dict[str, Any] = Depends(require_github_user),
):
    with internal_errors("get file context", logger):
        return await file_service.get_file_context(db=db, file_path=file_path, workspace_id=workspace_id, user_id=current_user["user"].id)
