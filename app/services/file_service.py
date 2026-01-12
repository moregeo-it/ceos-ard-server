import logging
import re
import shutil
from pathlib import Path

import git
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.schemas.workspace import FilePatchRequest
from app.services.git_service import GitService
from app.services.workspace_service import WorkspaceService
from app.utils.extraction import get_file_media_type
from app.utils.sanitization import sanitize_filename, sanitize_path

logger = logging.getLogger(__name__)


class FileService:
    def __init__(self):
        self.git_service = GitService()
        self.workspace_service = WorkspaceService()

    def get_file_status(self, repo: git.Repo, path: Path):
        try:
            git_status = repo.git.status(path, porcelain=True)
            if "A" in git_status:
                dir_status = "added"
            elif "M" in git_status:
                dir_status = "modified"
            elif "R" in git_status:
                dir_status = "renamed"
            else:
                dir_status = None
        except git.exc.GitCommandError:
            dir_status = "deleted"

        return dir_status


    async def get_workspace_files(self, path: str, db: Session, workspace_id: str, user_id: str, recurse: bool = False):
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

            repo = git.Repo(workspace.workspace_path, search_parent_directories=True)

            # Walk through the directory tree
            for root_path, dirs, files in target_path.walk():
                # Skip .git directories completely
                if ".git" in root_path.parts:
                    continue

                # Calculate relative path for root-level exclusions
                resolved_workspace_path = workspace_path.resolve()
                relative_root = root_path.relative_to(resolved_workspace_path)
                root_parts = relative_root.parts if relative_root != Path(".") else ()

                # Skip root-level build, templates directories (LICENSE is a file, not directory)
                if len(root_parts) >= 1 and (not recurse or root_parts[0] in ["build", "templates"]):
                    dirs[:] = []  # Don't traverse subdirectories
                    continue

                # Prune directories we don't want to traverse
                dirs[:] = [d for d in dirs if not d.startswith(".") and not (len(root_parts) == 0 and d in ["build", "templates"])]

                # Process directories in current level
                for dir_path in dirs:
                    if dir_path.startswith("."):
                        continue

                    full_dir_path = root_path / dir_path
                    relative_dir_path = full_dir_path.relative_to(resolved_workspace_path)
                    dir_status = self.get_file_status(repo, full_dir_path)
                    file_and_folder.append(
                        {
                            "status": dir_status,
                            "name": dir_path,
                            "is_directory": True,
                            "path": str(relative_dir_path),
                        }
                    )

                # Process files in current level
                for file_path in files:
                    # Skip dotfiles and PDFs
                    if file_path.startswith(".") or file_path.endswith(".pdf"):
                        continue

                    # Skip root-level LICENSE file
                    if len(root_parts) == 0 and file_path == "LICENSE":
                        continue

                    full_file_path = root_path / file_path
                    relative_file_path = full_file_path.relative_to(resolved_workspace_path)
                    file_status = self.get_file_status(repo, full_file_path)
                    file_and_folder.append(
                        {
                            "status": file_status,
                            "name": file_path,
                            "is_directory": False,
                            "path": str(relative_file_path),
                        }
                    )

            return file_and_folder
        except HTTPException:
            raise
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

        # Add changes to the repository
        try:
            repo = git.Repo(workspace_path, search_parent_directories=True)
            repo.git.add(str(target_path))
        except git.exc.GitCommandError as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add file to repository") from e

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

            # Add changes to the repository
            try:
                repo = git.Repo(workspace_path, search_parent_directories=True)
                repo.git.add(str(file_path))
            except git.exc.GitCommandError as e:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add file to repository") from e

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

            response = await self._delete(workspace_path, file_path)

            if response["message"] == "File deleted successfully":
                # Add changes to the repository
                try:
                    repo = git.Repo(workspace_path, search_parent_directories=True)
                    repo.git.add(file_path)
                except git.exc.GitCommandError as e:
                    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add file to repository") from e

                return response

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
                return {"message": "File deleted successfully", "file_path": str(target_path)}
        elif Path(target_path).is_dir():
            shutil.rmtree(target_path)
            return {"message": "Folder deleted successfully", "file_path": str(target_path)}
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

        # Add changes to the repository
        try:
            repo = git.Repo(workspace_path, search_parent_directories=True)
            repo.git.add(str(new_file_path))
        except git.exc.GitCommandError as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add file to repository") from e

        return {"name": new_name, "path": str(new_file_path.relative_to(workspace_path.resolve())), "directory": False}

    async def _revert_file_changes(self, workspace_path: Path, file_path: str):
        try:
            return await self.git_service.revert_file_changes(workspace_path=workspace_path, file_path=file_path)
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

            # Traverse the directory tree
            for root_path, _dirs, files in workspace_path.walk():
                # Skip .git directories completely
                if ".git" in root_path.parts:
                    continue

                # Process files in current directory
                for file_name in files:
                    # Skip dotfiles and ignored extensions
                    if file_name.startswith("."):
                        continue

                    file_path = root_path / file_name

                    # Skip files with ignored extensions
                    if ignored_extensions.search(file_name):
                        continue

                    # Check if search query matches filename
                    if search_query in file_name:
                        search_results.append({"name": file_name, "type": "filename", "path": str(file_path.relative_to(workspace_path))})
                        continue

                    # Search within file content
                    try:
                        with file_path.open(encoding="utf-8", errors="ignore") as f:
                            for i, line in enumerate(f):
                                line_lower = line.lower()
                                if search_query_lower in line_lower:
                                    column = line_lower.index(search_query_lower)
                                    search_results.append(
                                        {
                                            "name": file_name,
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

            git_status = await self.git_service.get_git_status(workspace_path)

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

            repo = git.Repo(workspace_path)

            relative_file_path = target_path.relative_to(workspace_path.resolve())
            relative_file_str = str(relative_file_path).replace("\\", "/")  # Ensure forward slashes for git

            # Check if file is tracked by Git
            is_tracked = False
            try:
                repo.git.cat_file("-e", f"HEAD:{relative_file_str}")
                is_tracked = True
            except git.GitCommandError:
                is_tracked = False
            # If not tracked, read the file content
            if not is_tracked:
                try:
                    # Read the file content
                    with open(target_path, encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    # Get the lines of the file and create a diff output
                    lines = content.splitlines()
                    diff_out = f"--- /dev/null\n+++ {relative_file_str}\n@@ -0,0 +1,{len(lines)} @@\n"

                    for line in lines:
                        diff_out += f"+{line}\n"

                    return diff_out

                except UnicodeDecodeError:
                    return f"Binary file b/{relative_file_str} differs"

                except Exception as e:
                    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get file diff: {str(e)}") from e

            # If tracked, get the diff from Git
            try:
                diff_out = repo.git.diff(relative_file_str)

                # If no unstaged changes, check for staged changes
                if not diff_out:
                    diff_out = repo.git.diff("--cached", relative_file_str)

                    # If still no changes, return a message
                    if not diff_out:
                        return f"No changes found for {relative_file_str}"

                return diff_out

            except git.GitCommandError as e:
                logger.error(f"Git command failed for {relative_file_str}: {str(e)}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get file diff: {str(e)}") from e

        except git.InvalidGitRepositoryError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a valid git repository") from e

        except ValueError as e:
            logger.error(f"Invalid file path: {file_path}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid file path: {file_path}") from e

        except Exception as e:
            logger.error(f"Failed to get file diff: {str(e)}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get file diff: {str(e)}") from e


file_service = FileService()
