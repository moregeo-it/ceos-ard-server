import logging
import shutil
from pathlib import Path

import git
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.schemas.workspace import FilePatchRequest
from app.services.git_service import GitService
from app.services.workspace_service import WorkspaceService
from app.utils.extraction import get_excerpt, get_file_media_type
from app.utils.validation import normalize_workspace_path, validate_pathname, validate_workspace_path

logger = logging.getLogger(__name__)


class FileService:
    def __init__(self):
        self.git_service = GitService()
        self.workspace_service = WorkspaceService()
        self.ignored_root_paths = {"build", "templates", ".git", "LICENSE"}
        self.searchable_file_extensions = {".txt", ".md", ".json", ".yaml", ".yml", ".xml"}

    def _get_all_file_statuses(self, repo: git.Repo, target_path: Path, workspace_path: Path):
        """Get all file statuses using GitPython API."""
        status_map = {}
        relative_target = normalize_workspace_path(target_path, workspace_path, absolute=False)

        def get_map_key(file_path: str) -> str:
            path = workspace_path / file_path
            return str(path.resolve())

        try:
            path_filter = relative_target if len(relative_target) > 0 else None

            # Process untracked files (added)
            for file_path in repo.untracked_files:
                if path_filter and not file_path.startswith(path_filter):
                    continue
                status_map[get_map_key(file_path)] = "added"

            # Process unstaged changes (working tree vs index)
            for diff in repo.index.diff(None, paths=path_filter):
                filename = get_map_key(diff.b_path or diff.a_path)

                if diff.deleted_file:
                    status_map[filename] = "deleted"
                elif diff.renamed:
                    status_map[filename] = "renamed"
                else:
                    status_map[filename] = "modified"

            # Process staged changes (index vs HEAD)
            for diff in repo.head.commit.diff(None, paths=path_filter):
                filename = get_map_key(diff.b_path or diff.a_path)

                # Skip if already marked (unstaged takes precedence for status display)
                if filename in status_map:
                    continue

                if diff.new_file:
                    status_map[filename] = "added"
                elif diff.deleted_file:
                    status_map[filename] = "deleted"
                elif diff.renamed:
                    status_map[filename] = "renamed"
                else:
                    status_map[filename] = "modified"

        except Exception:
            pass

        return status_map

    def get_file_dict(self, file: Path, workspace_path: Path, status: str | None = None) -> dict:
        return {
            "status": status,
            "name": file.name,
            "is_directory": file.is_dir(),
            "path": normalize_workspace_path(file, workspace_path),
        }

    async def get_workspace_files(self, path: str, db: Session, workspace_id: str, user_id: str, recurse: bool = False):
        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            target_path = validate_workspace_path(path, workspace.abs_path, exists=True)

            # Get the status of all files (e.g. to include deleted files)
            repo = git.Repo(workspace.abs_path, search_parent_directories=True)
            status_map = self._get_all_file_statuses(repo, target_path, workspace.abs_path)

            # Get all files (without deleted files)
            files = self.walk_files(target_path, workspace.abs_path, repo, recurse, status_map)

            # Add deleted files
            for filepath, status in status_map.items():
                file = Path(filepath)
                if status == "deleted" and (recurse or file.is_relative_to(target_path)):
                    files.append(self.get_file_dict(file, workspace.abs_path, status=status))

            # Sort directories first, then files, both alphabetically
            files.sort(key=lambda x: (x["is_directory"] == False, x["name"].lower()))

            return files
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get workspace files: {str(e)}") from e

    def walk_files(self, target_path: Path, workspace_path: Path, repo: git.Repo, recurse: bool = False, status: dict = {}) -> list[dict]:
        all_files = []
        relative_path = normalize_workspace_path(target_path, workspace_path, absolute=False)

        for file in target_path.iterdir():
            if relative_path == "" and file.name in self.ignored_root_paths:
                continue
            if file.name.startswith(".") or file.name.endswith(".pdf"):
                continue

            filepath = str(file.resolve())
            file_status = status.get(filepath, None)
            all_files.append(self.get_file_dict(file, workspace_path, status=file_status))

            if file.is_dir() and recurse:
                all_files.extend(self.walk_files(file, workspace_path, repo, recurse, status))

        return all_files

    async def create(self, db: Session, workspace_id: str, request_data: dict, user_id: str):
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

            if request_data.type == "file":
                return self._create_file(workspace.abs_path, request_data.name, request_data.path)
            elif request_data.type == "folder":
                return self._create_folder(workspace.abs_path, request_data.name, request_data.path)
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid type")

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create file or folder: {str(e)}") from e

    def _create_file(self, workspace_path: Path, name: str, path: str, content: str = None):
        name = validate_pathname(name)
        folder = validate_workspace_path(path, workspace_path, exists=True)

        target_path = folder / name
        if target_path.exists():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File already exists")

        if content is not None:
            target_path.write_text(content, encoding="utf-8")
        else:
            target_path.touch()

        # Add changes to the repository
        try:
            repo = git.Repo(workspace_path, search_parent_directories=True)
            repo.git.add(str(target_path))
        except git.exc.GitCommandError as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add file to repository") from e

        return {"name": name, "path": normalize_workspace_path(target_path, workspace_path), "directory": False}

    def _create_folder(self, workspace_path: Path, name: str, path: str):
        name = validate_pathname(name)
        folder = validate_workspace_path(path, workspace_path)

        target_path = folder / name
        if target_path.exists():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Directory already exists")

        target_path.mkdir(parents=True, exist_ok=True)

        return {"name": name, "path": normalize_workspace_path(target_path, workspace_path), "directory": True}

    async def read_file_content(self, db: Session, workspace_id: str, file_path: str, user_id: str):
        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            file_path = validate_workspace_path(file_path, workspace.abs_path)
            if not Path(file_path).is_file():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File path is not a file")

            return {"content": file_path.read_text(encoding="utf-8"), "media_type": get_file_media_type(file_path)}

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to read file: {str(e)}") from e

    async def store_file_content(self, db: Session, workspace_id: str, file_path: str, content: bytes, user_id: str):
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Content is required")

        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)

            file_path = validate_workspace_path(file_path, workspace.abs_path, exists=True, type="file")
            file_path.write_bytes(content, encoding="utf-8")

            # Add changes to the repository
            try:
                repo = git.Repo(workspace.abs_path, search_parent_directories=True)
                repo.git.add(str(file_path))
            except git.exc.GitCommandError as e:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add file to repository") from e

            return {"message": "File content stored successfully"}

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to store file content: {str(e)}") from e

    async def delete(self, db: Session, workspace_id: str, file_path: str, user_id: str):
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)

        if not file_path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File path is required")

        target_path = validate_workspace_path(file_path, workspace.abs_path)

        if not Path(target_path).exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File or folder not found")

        if target_path.is_file():
            try:
                target_path.unlink()
            except Exception as e:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete file. Please try again.") from e
        elif target_path.is_dir():
            try:
                shutil.rmtree(target_path)
            except Exception as e:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete folder. Please try again.") from e
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path is neither a file nor a folder.")

        # Add changes to the repository
        try:
            relative_path = normalize_workspace_path(target_path, workspace.abs_path, absolute=False)
            repo = git.Repo(workspace.abs_path, search_parent_directories=True)
            repo.git.add(relative_path)
        except git.exc.GitCommandError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="File or folder deleted successfully, but failed to make the changes in the repository",
            ) from e

        return {"message": "File or folder deleted successfully.", "path": normalize_workspace_path(target_path, workspace.abs_path)}

    async def update_file(self, db: Session, workspace_id: str, file_path: str, operation_request: FilePatchRequest, user_id: str):
        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            if operation_request.operation == "rename":
                return await self._update_file_name(workspace.abs_path, file_path, new_name=operation_request.target)
            elif operation_request.operation == "revert":
                return await self._revert_file_changes(workspace.abs_path, file_path)
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid operation")

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update: {str(e)}") from e

    async def _update_file_name(self, workspace_path: Path, file_path: str, new_name: str):
        new_name = validate_pathname(new_name)
        target_path = validate_workspace_path(file_path, workspace_path, exists=True)
        new_path = target_path.parent / new_name

        if new_path.exists():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A file/folder with the given name exists already")

        target_path.replace(new_path)

        if not new_path.exists():
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to rename file/folder. Please try again.")

        # Add changes to the repository
        try:
            repo = git.Repo(workspace_path, search_parent_directories=True)
            repo.git.add(str(new_path), str(target_path))
        except git.exc.GitCommandError as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add file/folder to repository") from e

        return {"name": new_name, "path": normalize_workspace_path(new_path, workspace_path), "directory": new_path.is_dir()}

    async def _revert_file_changes(self, workspace_path: Path, file_path: str):
        try:
            return await self.git_service.revert_file_changes(workspace_path=workspace_path, file_path=file_path)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to revert file changes: {str(e)}") from e

    async def search_files(self, db: Session, workspace_id: str, search_query: str, user_id: str):
        if not search_query:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search query is required")

        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)

        try:
            if not workspace.abs_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
            elif not workspace.abs_path.is_dir():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace path is not a directory")

            files = await self.get_workspace_files("/", db, workspace_id, user_id, recurse=True)

            search_results = []
            pattern = search_query.lower()
            # Traverse the directory tree
            for file in files:
                filepath = workspace.abs_path / file["path"].lstrip("/")

                # Ignore directories
                if file["is_directory"]:
                    continue
                # Ignore deleted files
                if file["status"] == "deleted":
                    continue
                # Ignore non-searchable file extensions
                if filepath.suffix not in self.searchable_file_extensions:
                    continue

                # Check if search query matches filename
                if pattern in filepath.name.lower():
                    file["type"] = "filename"
                    search_results.append(file)
                    continue

                # Search within file content
                try:
                    with filepath.open(encoding="utf-8", errors="ignore") as f:
                        for i, line in enumerate(f):
                            start = line.lower().find(pattern)
                            if start >= 0:
                                file.update(
                                    {
                                        "type": "content",
                                        "line": i + 1,
                                        "column": start + 1,
                                        "excerpt": get_excerpt(line, pattern, start),
                                    }
                                )
                                search_results.append(file)
                                break  # todo: shall we return all results from within a file?
                except (UnicodeDecodeError, FileNotFoundError) as e:
                    logger.warning(f"Could not read file '{file['path']}': {str(e)}")

            return search_results
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to search files: {str(e)}") from e

    async def get_changed_files(self, db: Session, workspace_id: str, user_id: str):
        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            git_status = await self.git_service.get_git_status(workspace.abs_path)

            changed_files = []

            for file in git_status["modified_files"]:
                changed_files.append({"path": normalize_workspace_path(file, workspace.abs_path), "status": file.status})

            for file in git_status["untracked_files"]:
                changed_files.append({"path": normalize_workspace_path(file, workspace.abs_path), "status": "untracked"})

            return changed_files
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get changed files: {str(e)}") from e

    async def get_file_diff(self, db: Session, file_path: str, workspace_id: str, user_id: str):
        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            target_path = validate_workspace_path(file_path, workspace.abs_path, exists=True, type="file")
            relative_file_str = normalize_workspace_path(target_path, workspace.abs_path, absolute=False)

            repo = git.Repo(workspace.abs_path)

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
