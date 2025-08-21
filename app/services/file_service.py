import logging
import re
import shutil
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.schemas.workspace import FilePatchRequest
from app.services.git_service import git_service
from app.services.workspace_service import workspace_service
from app.utils.extraction import get_file_media_type
from app.utils.sanitization import sanitize_filename, sanitize_path

logger = logging.getLogger(__name__)


class FileService:
    def __init__(self):
        self.git_service = git_service
        self.workspace_service = workspace_service

    async def get_workspace_files(self, path: str, db: Session, workspace_id: str, user_id: str):
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = Path(workspace.workspace_path)

            target_path = sanitize_path(path, workspace_path)

            if not target_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target path not found")

            file_and_folder = []

            for file_path in target_path.rglob("*"):
                if "build" in str(file_path) or ".git" in str(file_path):
                    continue
                if file_path.name in ["templates", "LICENSE"] or file_path.name.startswith("."):
                    continue
                if file_path.parent.name in ["templates"] or file_path.parent.name.startswith("."):
                    continue
                if file_path.is_file() and (file_path.name.startswith(".") or file_path.name.endswith(".pdf")):
                    continue
                file_and_folder.append(
                    {"name": file_path.name, "path": str(file_path.relative_to(workspace_path.resolve())), "is_directory": file_path.is_dir()}
                )
            return file_and_folder
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get workspace files: {str(e)}") from e

    async def create(self, db: Session, workspace_id: str, request_data: dict, user_id: str):
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not request_data.name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name is required")

        if not request_data.type:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Type is required")

        if not request_data.path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path is required")

        if request_data.type not in ["file", "folder"]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Type must be file or folder")

        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = Path(workspace.workspace_path)

            if request_data.type == "file":
                return self._create_file(workspace_path=workspace_path, name=request_data.name, path=request_data.path)
            elif request_data.type == "folder":
                return self._create_folder(workspace_path=workspace_path, name=request_data.name, path=request_data.path)
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid type")

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create file or folder: {str(e)}") from e

    def _create_file(self, workspace_path: Path, name: str, path: str, content: str = None):
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name is required")

        if not path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path is required")

        if not workspace_path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace path is required")

        sanitized_filename = sanitize_filename(name)
        sanitized_path = sanitize_path(path, workspace_path)

        target_path = sanitized_path / sanitized_filename

        if target_path.exists():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File already exists")

        target_path.touch(exist_ok=True)
        if content is not None:
            target_path.write_text(content, encoding="utf-8")

        return {"name": name, "path": str(target_path), "directory": False}

    def _create_folder(self, workspace_path: Path, name: str, path: str):
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name is required")

        if not path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path is required")

        if not workspace_path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace path is required")

        sanitized_path = sanitize_path(path, workspace_path)
        sanitized_name = sanitize_filename(name)

        target_path = sanitized_path / sanitized_name

        if target_path.exists():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Directory already exists")

        target_path.mkdir(parents=True, exist_ok=True)

        return {"name": sanitized_name, "path": str(target_path.relative_to(workspace_path.resolve())), "directory": True}

    async def read_file_content(self, db: Session, workspace_id: str, file_path: str, user_id: str):
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not file_path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File path is required")

        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = Path(workspace.workspace_path)

            file_path = sanitize_path(file_path, workspace_path)

            if not Path(file_path).exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

            if not Path(file_path).is_file():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File path is not a file")

            content = Path(file_path).read_text()

            media_type = get_file_media_type(file_path)

            return {"content": content, "media_type": media_type}

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to read file: {str(e)}") from e

    async def store_file_content(self, db: Session, workspace_id: str, file_path: str, content: bytes, user_id: str):
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not file_path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File path is required")

        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Content is required")

        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = Path(workspace.workspace_path)

            file_path = sanitize_path(file_path, workspace_path)

            if not Path(file_path).exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

            if not Path(file_path).is_file():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path does not point to a file")

            Path(file_path).write_bytes(content)

            return {"message": "File content stored successfully"}

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to store file content: {str(e)}") from e

    async def delete(self, db: Session, workspace_id: str, file_path: str, user_id: str):
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not file_path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File path is required")

        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = Path(workspace.workspace_path)

            return await self._delete(workspace_path, file_path)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete file or folder: {str(e)}") from e

    async def _delete(self, workspace_path: Path, file_path: str):
        if not file_path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File path is required")

        if not workspace_path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace path is required")

        target_path = sanitize_path(file_path, workspace_path)

        if not Path(target_path).exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File or folder not found")

        if Path(target_path).is_file():
            target_path.unlink()
            if not target_path.exists():
                return {"message": "File deleted successfully"}
        elif Path(target_path).is_dir():
            shutil.rmtree(target_path)
            return {"message": "Folder deleted successfully"}
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid type")

    async def update_file(self, db: Session, workspace_id: str, file_path: str, operation_request: FilePatchRequest, user_id: str):
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not file_path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File path is required")

        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = Path(workspace.workspace_path)

            if operation_request.operation not in ["rename", "revert"]:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid operation")

            if operation_request.operation == "rename":
                return await self._update_file_name(workspace_path, file_path, new_name=operation_request.target)
            elif operation_request.operation == "revert":
                return await self._revert_file_changes(workspace_path, file_path)
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid operation")

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update file: {str(e)}") from e

    async def _update_file_name(self, workspace_path: Path, file_path: str, new_name: str):
        target_path = sanitize_path(file_path, workspace_path)

        if not target_path.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

        if not target_path.is_file() or not target_path.is_relative_to(workspace_path.resolve()):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

        if not new_name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New name is required")

        new_name = sanitize_filename(new_name)
        new_file_path = Path(target_path.parent / new_name)
        if new_file_path.exists():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File already exists")
        target_path.replace(new_file_path)

        return {"name": new_name, "path": str(new_file_path.relative_to(workspace_path.resolve())), "directory": False}

    async def _revert_file_changes(self, workspace_path: Path, file_path: str):
        try:
            target_path = sanitize_path(file_path, workspace_path)

            if not target_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

            if not target_path.is_file() or not target_path.is_relative_to(workspace_path.resolve()):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

            await git_service.revert_file_changes(workspace_path=workspace_path, file_path=file_path)

            return {"path": str(target_path), "name": str(target_path.name), "directory": False}
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to revert file changes: {str(e)}") from e

    async def search_files(self, db: Session, workspace_id: str, search_query: str, user_id: str):
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not search_query:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search query is required")

        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        workspace_path = Path(workspace.workspace_path)

        return await self._search_files(workspace_path, search_query)

    async def _search_files(self, workspace_path: Path, search_query: str) -> list[dict]:
        try:
            search_results = []
            search_query_lower = search_query.lower()
            ignored_extensions = re.compile(r"\.(pdf|jpg|jpeg|png|gif|bmp|docx|xlsx)$", re.IGNORECASE)

            if not workspace_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

            if not workspace_path.is_dir():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace path is not a directory")

            for file_path in workspace_path.rglob("*"):
                if not file_path.is_file():
                    continue

                if search_query in file_path.name:
                    search_results.append({"name": file_path.name, "type": "filename", "path": str(file_path.relative_to(workspace_path))})
                    continue

                if ignored_extensions.search(file_path.name):
                    continue

                try:
                    with file_path.open(encoding="utf-8", errors="ignore") as f:
                        for i, line in enumerate(f):
                            line_lower = line.lower()
                            if search_query_lower in line_lower:
                                column = line_lower.index(search_query_lower)
                                search_results.append(
                                    {
                                        "name": file_path.name,
                                        "type": "content",
                                        "path": str(file_path.relative_to(workspace_path)),
                                        "line": i + 1,
                                        "column": column + 1,
                                        "excerpt": line.strip(),
                                    }
                                )
                                break
                except (UnicodeDecodeError, FileNotFoundError) as e:
                    logger.warning(f"Could not read file {file_path}: {str(e)}")

            return search_results
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to search files: {str(e)}") from e

    async def get_changed_files(self, db: Session, workspace_id: str, user_id: str):
        try:
            if not workspace_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = Path(workspace.workspace_path)

            if not workspace_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

            if not workspace_path.is_dir():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace path is not a directory")

            git_status = await git_service.get_git_status(workspace_path)

            changed_files = []

            for file in git_status["modified_files"]:
                changed_files.append({"path": file.path, "status": file.status})

            for file in git_status["untracked_files"]:
                changed_files.append({"path": file, "status": "untracked"})

            return changed_files
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get changed files: {str(e)}") from e

    async def get_file_diff(self, db: Session, file_path: str, workspace_id: str, user_id: str):
        try:
            if not workspace_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

            if not file_path:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File path is required")

            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = Path(workspace.workspace_path)

            target_path = sanitize_path(file_path, workspace_path)

            if not target_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

            if not target_path.is_file():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path is not a file")

            stdout, stderr, returncode = git_service._run_git_command(["git", "diff", file_path], cwd=workspace_path)

            if returncode != 0:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get file diff: {stderr}")

            return stdout
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get file diff: {str(e)}") from e


file_service = FileService()
