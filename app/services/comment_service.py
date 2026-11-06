import logging
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.comment import Comment, CommentThread, ThreadStatus
from app.models.user import User
from app.models.workspace import GitWorkspace, WorkspaceStatus
from app.models.workspace_share import AccessMode
from app.schemas.comment import CommentRequest, ThreadCreateRequest, ThreadResponse, ThreadUpdateRequest
from app.schemas.events import EventType
from app.services.events_service import EventBroker, event_broker
from app.services.workspace_service import WorkspaceService
from app.utils.locks import run_exclusive
from app.utils.validation import normalize_workspace_path, validate_workspace_path

logger = logging.getLogger(__name__)


def move_threads(db: Session, workspace_id: str, old_path: str, new_path: str) -> None:
    """Let the threads follow a renamed file, or the files of a renamed folder. Paths as the file API names them.

    A failure is logged, not raised: the rename itself has happened, and the threads only show as outdated.
    """
    if old_path == new_path:
        return
    try:
        threads = (
            db.query(CommentThread)
            .filter(
                CommentThread.workspace_id == workspace_id,
                or_(CommentThread.path == old_path, CommentThread.path.startswith(f"{old_path}/", autoescape=True)),
            )
            .all()
        )
        for thread in threads:
            thread.previous_paths = [*thread.previous_paths, thread.path]
            thread.path = new_path + thread.path[len(old_path) :]
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Failed to move the comment threads of {old_path} to {new_path} in workspace {workspace_id}: {e}")


def _author(user_id: str | None, github_id: str | None, username: str | None) -> dict | None:
    if github_id is None:
        return None
    return {"user_id": user_id, "github_id": github_id, "username": username}


def serialize_thread(thread: CommentThread, workspace: GitWorkspace) -> dict:
    """The Thread response body as JSON, which the thread events carry unmodified."""
    thread_data = {
        "id": thread.id,
        "workspace_id": thread.workspace_id,
        "path": thread.path,
        "previous_paths": list(thread.previous_paths or []),
        "status": thread.status.value,
        "outdated": not (workspace.abs_path / thread.path.lstrip("/")).is_file(),
        "created_by": _author(thread.created_by_user_id, thread.created_by_github_id, thread.created_by_username),
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
        "resolved_by": _author(thread.resolved_by_user_id, thread.resolved_by_github_id, thread.resolved_by_username),
        "resolved_at": thread.resolved_at,
        "comments": [
            {
                "id": comment.id,
                "author": _author(comment.author_user_id, comment.author_github_id, comment.author_username),
                "body": comment.body,
                "deleted": comment.deleted_at is not None,
                "created_at": comment.created_at,
                "edited_at": comment.edited_at,
            }
            for comment in thread.comments
        ],
    }
    return ThreadResponse.model_validate(thread_data).model_dump(mode="json")


class CommentService:
    """Comment threads on the files of a workspace.

    Everyone with access reads them. Commenters and the owner write, reply, resolve and reopen,
    and edit and delete their own comments; the owner deletes any comment. Only the first and the
    last comment of a thread can be deleted, and threads can't be deleted as such. On an archived
    workspace they are read-only for everyone.
    """

    def __init__(self, workspace_service: WorkspaceService | None = None, broker: EventBroker | None = None):
        self.workspace_service = workspace_service or WorkspaceService()
        self.broker = broker or event_broker

    def _workspace(self, db: Session, workspace_id: str, user: User, write: bool) -> GitWorkspace:
        min_role = AccessMode.COMMENT.value if write else AccessMode.READONLY.value
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user.id, min_role=min_role)
        if write and workspace.status == WorkspaceStatus.ARCHIVED:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The workspace is archived, so its comments can't be changed")
        return workspace

    @staticmethod
    def _thread(db: Session, workspace: GitWorkspace, thread_id: str) -> CommentThread:
        thread = db.query(CommentThread).filter(CommentThread.id == thread_id, CommentThread.workspace_id == workspace.id).first()
        if not thread:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
        return thread

    @staticmethod
    def _comment(thread: CommentThread, comment_id: str) -> Comment:
        comment = next((comment for comment in thread.comments if comment.id == comment_id), None)
        if not comment or comment.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
        return comment

    def _emit(self, workspace: GitWorkspace, event_type: EventType, user: User, thread: dict) -> None:
        self.broker.emit(workspace.id, event_type, actor_user_id=user.id, path=thread["path"], thread=thread)

    async def list_threads(self, db: Session, workspace_id: str, user: User) -> list[dict]:
        workspace = self._workspace(db, workspace_id, user, write=False)
        threads = db.query(CommentThread).filter(CommentThread.workspace_id == workspace.id).order_by(CommentThread.created_at.desc()).all()
        return [serialize_thread(thread, workspace) for thread in threads]

    async def create_thread(self, db: Session, workspace_id: str, user: User, request: ThreadCreateRequest) -> dict:
        workspace = self._workspace(db, workspace_id, user, write=True)

        # Under the workspace lock, so a rename can't move the file away between the check and the insert
        async def transaction():
            target = validate_workspace_path(request.path, workspace.abs_path, exists=True, type="file", rules=workspace.workspace_type.paths)
            now = datetime.now(UTC)
            thread = CommentThread(
                workspace_id=workspace.id,
                repository=f"{workspace.upstream_owner}/{workspace.upstream_repo}",
                path=normalize_workspace_path(target, workspace.abs_path),
                previous_paths=[],
                status=ThreadStatus.OPEN,
                created_by_user_id=user.id,
                created_by_github_id=user.external_id,
                created_by_username=user.username,
                created_at=now,
                updated_at=now,
            )
            thread.comments.append(self._new_comment(user, request.body, now))
            db.add(thread)
            db.commit()
            result = serialize_thread(thread, workspace)
            self._emit(workspace, EventType.THREAD_CREATED, user, result)
            return result

        return await run_exclusive(workspace.id, transaction)

    async def update_thread(self, db: Session, workspace_id: str, thread_id: str, user: User, request: ThreadUpdateRequest) -> dict:
        workspace = self._workspace(db, workspace_id, user, write=True)
        thread = self._thread(db, workspace, thread_id)
        if thread.status != request.status:
            thread.status = request.status
            if request.status == ThreadStatus.RESOLVED:
                thread.resolved_by_user_id = user.id
                thread.resolved_by_github_id = user.external_id
                thread.resolved_by_username = user.username
                thread.resolved_at = datetime.now(UTC)
            else:
                thread.resolved_by_user_id = thread.resolved_by_github_id = thread.resolved_by_username = thread.resolved_at = None
            db.commit()
            result = serialize_thread(thread, workspace)
            self._emit(workspace, EventType.THREAD_UPDATED, user, result)
            return result
        return serialize_thread(thread, workspace)

    async def add_comment(self, db: Session, workspace_id: str, thread_id: str, user: User, request: CommentRequest) -> dict:
        workspace = self._workspace(db, workspace_id, user, write=True)
        thread = self._thread(db, workspace, thread_id)
        now = datetime.now(UTC)
        thread.comments.append(self._new_comment(user, request.body, now))
        thread.updated_at = now
        db.commit()
        result = serialize_thread(thread, workspace)
        self._emit(workspace, EventType.THREAD_UPDATED, user, result)
        return result

    async def edit_comment(self, db: Session, workspace_id: str, thread_id: str, comment_id: str, user: User, request: CommentRequest) -> dict:
        workspace = self._workspace(db, workspace_id, user, write=True)
        thread = self._thread(db, workspace, thread_id)
        comment = self._comment(thread, comment_id)
        if comment.author_github_id != user.external_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only edit your own comments")
        if comment.body != request.body:
            comment.body = request.body
            comment.edited_at = thread.updated_at = datetime.now(UTC)
            db.commit()
            result = serialize_thread(thread, workspace)
            self._emit(workspace, EventType.THREAD_UPDATED, user, result)
            return result
        return serialize_thread(thread, workspace)

    async def delete_comment(self, db: Session, workspace_id: str, thread_id: str, comment_id: str, user: User) -> dict | None:
        """Delete the first or the last comment of a thread; replies in between stay, so the discussion keeps its context.

        The last comment is removed. The first comment becomes a placeholder while there are replies.
        Threads can't be deleted as such: a thread only goes when nothing but deleted comments is left,
        e.g. with its only comment. Returns the thread, or None when it went.
        """
        workspace = self._workspace(db, workspace_id, user, write=True)
        thread = self._thread(db, workspace, thread_id)
        comment = self._comment(thread, comment_id)
        if comment.author_github_id != user.external_id and workspace.viewer_role != "owner":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only delete your own comments")

        first = thread.comments[0]
        if comment is not first and comment is not thread.comments[-1]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only the last reply of a thread can be deleted")

        now = datetime.now(UTC)
        if comment is first:
            # The replies stay, under a placeholder for the first comment
            comment.body = None
            comment.deleted_at = now
        else:
            thread.comments.remove(comment)

        # Nothing left but deleted comments: the thread goes
        if all(comment.deleted_at is not None for comment in thread.comments):
            result = serialize_thread(thread, workspace)
            db.delete(thread)
            db.commit()
            self.broker.emit(workspace.id, EventType.THREAD_DELETED, actor_user_id=user.id, path=result["path"], thread_id=result["id"])
            return None

        thread.updated_at = now
        db.commit()
        result = serialize_thread(thread, workspace)
        self._emit(workspace, EventType.THREAD_UPDATED, user, result)
        return result

    @staticmethod
    def _new_comment(user: User, body: str, now: datetime) -> Comment:
        return Comment(author_user_id=user.id, author_github_id=user.external_id, author_username=user.username, body=body, created_at=now)
