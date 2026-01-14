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
from app.utils.sanitization import sanitize_filename, sanitize_path, fix_path

logger = logging.getLogger(__name__)


class FileService:
    def __init__(self):
        self.git_service = GitService()
        self.workspace_service = WorkspaceService()
        self.ignored_root_paths = {"build", "templates", ".git", "LICENSE"}

    @staticmethod
    def _is_direct_child(file_path: str, relative_target: str) -> bool:
        """Check if file_path is a direct child of target directory."""
        if relative_target == ".":
            return "/" not in file_path
        try:
            rel = Path(file_path).relative_to(relative_target)
            return "/" not in str(rel)
        except ValueError:
            return False

    def _get_all_file_statuses(self, repo: git.Repo, target_path: Path, workspace_path: Path):
        """Get all file statuses using GitPython API."""
        status_map = {}

        relative_target = str(target_path.relative_to(workspace_path))

        try:
            path_filter = relative_target if relative_target != "." else None

            # Process untracked files (added)
            for file_path in repo.untracked_files:
                if path_filter and not file_path.startswith(path_filter):
                    continue
                if not self._is_direct_child(file_path, relative_target):
                    continue
                filename = Path(file_path).name
                status_map[filename] = "added"

            # Process unstaged changes (working tree vs index)
            for diff in repo.index.diff(None, paths=path_filter):
                if not self._is_direct_child(diff.a_path or diff.b_path, relative_target):
                    continue

                filename = Path(diff.b_path or diff.a_path).name

                if diff.deleted_file:
                    status_map[filename] = "deleted"
                elif diff.renamed:
                    status_map[filename] = "renamed"
                else:
                    status_map[filename] = "modified"

            # Process staged changes (index vs HEAD)
            for diff in repo.head.commit.diff(None, paths=path_filter):
                if not self._is_direct_child(diff.a_path or diff.b_path, relative_target):
                    continue

                filename = Path(diff.b_path or diff.a_path).name

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

    def _get_deleted_files(self, repo: git.Repo, target_path: Path, workspace_path: Path):
        """Get list of deleted tracked files that no longer exist on disk."""
        deleted_files = []
        seen_paths = set()

        relative_target = str(target_path.relative_to(workspace_path))

        def process_diff(diff):
            """Process a diff object and add to deleted_files if valid."""
            if not diff.a_path or diff.a_path in seen_paths:
                return

            if not self._is_direct_child(diff.a_path, relative_target):
                return

            seen_paths.add(diff.a_path)
            deleted_files.append({
                "status": "deleted",
                "name": Path(diff.a_path).name,
                "is_directory": False,
                "path": fix_path(diff.a_path),
            })

        try:
            path_filter = relative_target if relative_target != "." else None

            # Process unstaged and staged deletions
            for diff in repo.index.diff(None, paths=path_filter):
                if diff.deleted_file:
                    process_diff(diff)

            for diff in repo.head.commit.diff(None, paths=path_filter):
                if diff.change_type == 'D' or diff.deleted_file:
                    process_diff(diff)
        except Exception:
            pass

        return deleted_files

    async def get_workspace_files(self, path: str, db: Session, workspace_id: str, user_id: str, recurse: bool = False):
        if not workspace_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required")

        if not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User ID is required")

        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            workspace_path = Path(workspace.workspace_path).resolve()

            target_path = sanitize_path(path, workspace_path)

            if not target_path.exists():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target path not found")

            repo = git.Repo(workspace.workspace_path, search_parent_directories=True)

            return self.walk_files(target_path, workspace_path, repo, recurse)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get workspace files: {str(e)}") from e

    def walk_files(self, target_path: Path, workspace_path: Path, repo: git.Repo, recurse: bool):
        all_files = []
        relative_path = str(target_path.relative_to(workspace_path))

        # Get all file statuses in one git call
        status_map = self._get_all_file_statuses(repo, target_path, workspace_path)

        # Get deleted files separately
        deleted_files = self._get_deleted_files(repo, target_path, workspace_path)

        for file in target_path.iterdir():
            if relative_path == "." and file.name in self.ignored_root_paths:
                continue
            if file.name.startswith(".") or file.name.endswith(".pdf"):
                continue

            # Look up status from the map (no git call per file)
            status = status_map.get(file.name, None) if not file.is_dir() else None

            all_files.append({
                "status": status,
                "name": file.name,
                "is_directory": file.is_dir(),
                "path": fix_path(file.relative_to(workspace_path)),
            })

            if file.is_dir() and recurse:
                all_files.extend(self.walk_files(file, workspace_path, repo, recurse))

        # Add deleted files to results
        all_files.extend(deleted_files)

        # sort directories first, then files, both alphabetically
        all_files.sort(key=lambda x: (x["is_directory"] == False, x["name"].lower()))

        return all_files

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

        rel_path = target_path.relative_to(workspace_path.resolve())
        return {"name": sanitized_name, "path": fix_path(rel_path), "directory": True}

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

            if not file_path:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File path is required")

            if not workspace_path:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace path is required")

            target_path = sanitize_path(file_path, workspace_path)

            response = await self._delete(target_path)

            if response["message"] in ["File deleted successfully", "Folder deleted successfully"]:
                # Add changes to the repository
                try:
                    repo = git.Repo(workspace_path, search_parent_directories=True)

                    relative_path = target_path.relative_to(workspace_path.resolve())
                    repo.git.add(str(relative_path))
                except git.exc.GitCommandError as e:
                    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add file to repository") from e

                return response

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete file or folder: {str(e)}") from e

    async def _delete(self, target_path: Path):
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

        rel_path = new_file_path.relative_to(workspace_path.resolve())
        return {"name": new_name, "path": fix_path(rel_path), "directory": False}

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

                    rel_path = file_path.relative_to(workspace_path)
                    # Check if search query matches filename
                    if search_query in file_name:
                        search_results.append({"name": file_name, "type": "filename", "path": fix_path(rel_path)})
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
                                            "path": fix_path(rel_path),
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
                changed_files.append({"path": fix_path(file.path), "status": file.status})

            for file in git_status["untracked_files"]:
                changed_files.append({"path": fix_path(file), "status": "untracked"})

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
            relative_file_str = fix_path(relative_file_path)

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
