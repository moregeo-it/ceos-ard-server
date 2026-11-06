import logging
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.dependencies import get_comment_service
from app.schemas.comment import CommentRequest, ThreadCreateRequest, ThreadResponse, ThreadUpdateRequest
from app.services.auth_service import require_github_user
from app.services.comment_service import CommentService
from app.utils.http_utils import internal_errors

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Comments"])


@router.get(
    "/workspaces/{workspace_id}/threads",
    summary="List the comment threads of a workspace",
    response_model=list[ThreadResponse],
    status_code=status.HTTP_200_OK,
)
async def list_threads(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    comment_service: CommentService = Depends(get_comment_service),
):
    with internal_errors("list comment threads", logger):
        return await comment_service.list_threads(db=db, workspace_id=workspace_id, user=current_user["user"])


@router.post(
    "/workspaces/{workspace_id}/threads",
    summary="Start a comment thread on a file",
    response_model=ThreadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread(
    workspace_id: str,
    request: ThreadCreateRequest,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    comment_service: CommentService = Depends(get_comment_service),
):
    with internal_errors("create comment thread", logger):
        return await comment_service.create_thread(db=db, workspace_id=workspace_id, user=current_user["user"], request=request)


@router.patch(
    "/workspaces/{workspace_id}/threads/{thread_id}",
    summary="Resolve or reopen a comment thread",
    response_model=ThreadResponse,
    status_code=status.HTTP_200_OK,
)
async def update_thread(
    workspace_id: str,
    thread_id: str,
    request: ThreadUpdateRequest,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    comment_service: CommentService = Depends(get_comment_service),
):
    with internal_errors("update comment thread", logger):
        return await comment_service.update_thread(db=db, workspace_id=workspace_id, thread_id=thread_id, user=current_user["user"], request=request)


@router.post(
    "/workspaces/{workspace_id}/threads/{thread_id}/comments",
    summary="Reply to a comment thread",
    response_model=ThreadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_comment(
    workspace_id: str,
    thread_id: str,
    request: CommentRequest,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    comment_service: CommentService = Depends(get_comment_service),
):
    with internal_errors("add comment", logger):
        return await comment_service.add_comment(db=db, workspace_id=workspace_id, thread_id=thread_id, user=current_user["user"], request=request)


@router.patch(
    "/workspaces/{workspace_id}/threads/{thread_id}/comments/{comment_id}",
    summary="Edit your own comment",
    response_model=ThreadResponse,
    status_code=status.HTTP_200_OK,
)
async def edit_comment(
    workspace_id: str,
    thread_id: str,
    comment_id: str,
    request: CommentRequest,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    comment_service: CommentService = Depends(get_comment_service),
):
    with internal_errors("edit comment", logger):
        return await comment_service.edit_comment(
            db=db, workspace_id=workspace_id, thread_id=thread_id, comment_id=comment_id, user=current_user["user"], request=request
        )


@router.delete(
    "/workspaces/{workspace_id}/threads/{thread_id}/comments/{comment_id}",
    summary="Delete a comment",
    response_model=ThreadResponse,
    status_code=status.HTTP_200_OK,
    responses={204: {"description": "The thread was deleted with its last comment"}},
)
async def delete_comment(
    workspace_id: str,
    thread_id: str,
    comment_id: str,
    db: Session = Depends(get_db),
    current_user: dict[str, Any] = Depends(require_github_user),
    comment_service: CommentService = Depends(get_comment_service),
):
    with internal_errors("delete comment", logger):
        thread = await comment_service.delete_comment(
            db=db, workspace_id=workspace_id, thread_id=thread_id, comment_id=comment_id, user=current_user["user"]
        )
    if thread is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return thread
