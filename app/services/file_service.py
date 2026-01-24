import logging
import re
import shutil
from pathlib import Path

import git
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from yaml import safe_load as yaml_load

from app.schemas.workspace import FilePatchRequest
from app.services.git_service import GitService
from app.services.workspace_service import WorkspaceService
from app.utils.extraction import get_excerpt, get_file_media_type
from app.utils.git_utils import extract_fileinfo, get_file_status, get_repo_changes
from app.utils.validation import IGNORE_ROOT_PATHS, ignore_file_path, normalize_workspace_path, validate_pathname, validate_workspace_path

logger = logging.getLogger(__name__)


class FileService:
    def __init__(self):
        self.git_service = GitService()
        self.workspace_service = WorkspaceService()
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
            repo = git.Repo(workspace.abs_path)
            status_map = self._get_all_file_statuses(repo, target_path, workspace.abs_path)

            # Get all files (without deleted files)
            files = self.walk_files(target_path, workspace.abs_path, repo, recurse, status_map)

            # Add deleted files
            for filepath, file_status in status_map.items():
                file = Path(filepath)
                if file_status == "deleted" and (recurse or file.parent == target_path):
                    files.append(self.get_file_dict(file, workspace.abs_path, status=file_status))

            # Sort directories first, then files, both alphabetically
            files.sort(key=lambda x: (not x["is_directory"], x["name"].lower()))

            return files
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get workspace files: {str(e)}") from e

    def walk_files(self, target_path: Path, workspace_path: Path, repo: git.Repo, recurse: bool = False, status: dict | None = None) -> list[dict]:
        if status is None:
            status = {}
        all_files = []

        for file in target_path.iterdir():
            if ignore_file_path(file, file.relative_to(workspace_path), IGNORE_ROOT_PATHS):
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

            name = validate_pathname(request_data.name)
            folder = validate_workspace_path(request_data.path, workspace.abs_path, exists=True)

            target_path = folder / name

            if target_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=f"{'Directory' if target_path.is_dir() else 'File'} already exists"
                )
            if request_data.type == "file":
                return self._create_file(workspace.abs_path, request_data.name, target_path)
            elif request_data.type == "folder":
                return self._create_folder(workspace.abs_path, request_data.name, target_path)
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid type")

        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create file or folder: {str(e)}") from e

    def _create_file(self, workspace_path: Path, name: str, target_path: Path, content: str = None):
        if content is not None:
            target_path.write_text(content, encoding="utf-8")
        else:
            target_path.touch()
        try:
            repo = git.Repo(workspace_path)
            repo.git.add(str(target_path))
        except git.exc.GitCommandError as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add file to repository") from e
        relative_path = normalize_workspace_path(target_path, workspace_path, absolute=False)
        return {
            "name": name,
            "is_directory": False,
            "status": get_file_status(repo, relative_path),
            "path": normalize_workspace_path(target_path, workspace_path),
        }

    def _create_folder(self, workspace_path: Path, name: str, target_path: Path):
        try:
            target_path.mkdir(parents=True, exist_ok=True)
            repo = git.Repo(workspace_path)
        except git.exc.GitCommandError as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to access repository") from e

        relative_path = normalize_workspace_path(target_path, workspace_path, absolute=False)
        return {
            "name": name,
            "is_directory": True,
            "status": get_file_status(repo, relative_path),
            "path": normalize_workspace_path(target_path, workspace_path),
        }

    async def read_file_content(self, db: Session, workspace_id: str, file_path: str, user_id: str):
        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            file_path = validate_workspace_path(file_path, workspace.abs_path, exists=True, type="file")

            return {"content": file_path.read_text(encoding="utf-8"), "media_type": get_file_media_type(file_path)}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to read file: {str(e)}") from e

    async def store_file_content(self, db: Session, workspace_id: str, file_path: str, content: bytes, user_id: str):
        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            file_path = validate_workspace_path(file_path, workspace.abs_path, type="file")

            file_path.write_bytes(content)
            # Add changes to the repository
            try:
                repo = git.Repo(workspace.abs_path)
                repo.git.add(str(file_path))
            except git.exc.GitCommandError as e:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add file to repository") from e
            relative_path = normalize_workspace_path(file_path, workspace.abs_path, absolute=False)
            return {
                "name": file_path.name,
                "is_directory": file_path.is_dir(),
                "status": get_file_status(repo, relative_path),
                "path": normalize_workspace_path(file_path, workspace.abs_path),
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to store file content: {str(e)}") from e

    async def delete(self, db: Session, workspace_id: str, file_path: str, user_id: str):
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        if not file_path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File path is required")
        target_path = validate_workspace_path(file_path, workspace.abs_path, exists=True)

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
            repo = git.Repo(workspace.abs_path)
            # Check if file exists in HEAD (has git history)
            is_committed = False
            try:
                repo.git.cat_file("-e", f"HEAD:{relative_path}")
                is_committed = True
            except git.GitCommandError:
                is_committed = False
            if is_committed:
                # File is in git history - stage the deletion
                repo.git.add(relative_path)
            else:
                # File not in git history - remove from index if staged
                try:
                    repo.git.rm("--staged", "--force", relative_path)
                except git.GitCommandError:
                    # File wasn't in index, nothing to do
                    pass
        except git.exc.GitCommandError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="File or folder deleted successfully, but failed to make the changes in the repository",
            ) from e

        relative_path = normalize_workspace_path(target_path, workspace.abs_path, absolute=False)
        return {
            # Tracked means the file is and was under version control, so the delete can be reverted if needed.
            "tracked": is_committed,
            "file_details": {
                "name": target_path.name,
                "is_directory": target_path.is_dir(),
                "status": get_file_status(repo, relative_path),
                "path": normalize_workspace_path(target_path, workspace.abs_path),
            },
        }

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

        try:
            repo = git.Repo(workspace_path)
            relative_old = normalize_workspace_path(target_path, workspace_path, absolute=False)
            relative_new = normalize_workspace_path(new_path, workspace_path, absolute=False)

            repo.git.add(relative_new, relative_old)
        except git.exc.GitCommandError as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add file/folder to repository") from e

        return {
            "name": new_name,
            "is_directory": new_path.is_dir(),
            "status": get_file_status(repo, relative_new),
            "path": normalize_workspace_path(new_path, workspace_path),
        }

    async def _revert_file_changes(self, workspace_path: Path, file_path: str):
        try:
            return await self.git_service.revert_file_changes(workspace_path=workspace_path, file_path=file_path)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to revert file changes: {str(e)}") from e

    async def search_files(self, db: Session, workspace_id: str, search_query: str, user_id: str):
        if not search_query:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search query is required")
        if len(search_query.strip()) < 3:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search query must be at least 3 characters long")

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

            return get_repo_changes(workspace.abs_path)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get changed files: {str(e)}") from e

    async def get_file_diff(self, db: Session, file_path: str, workspace_id: str, user_id: str):
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        target_path = validate_workspace_path(file_path, workspace.abs_path, type="file")
        relative_path_str = normalize_workspace_path(target_path, workspace.abs_path, absolute=False)

        try:
            repo = git.Repo(workspace.abs_path)
            # Get general status for the file
            git_status = repo.git.status(Path(target_path).parent, porcelain=True)
            info = None
            for line in git_status.splitlines():
                info = extract_fileinfo(line)
                if Path(info["path"]) == Path(relative_path_str):
                    break

            # Handle renamed files differently, otherwise they show as added
            if info["status"] == "renamed":
                return repo.git.diff("--staged", "-M", "--", info["source"], info["path"])
            else:
                return repo.git.diff("--staged", relative_path_str)
        except git.GitCommandError as e:
            logger.error(f"Git command failed for {relative_path_str}: {str(e)}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get file diff: {str(e)}") from e

    async def _get_file_usage(self, workspace_path: Path, file_path: str) -> list[str]:
        """
        Get the list of PFS documents that use a specific requirement file.

        Args:
            workspace_path: The absolute path to the workspace
            file_path: The relative file path (e.g., '/requirements/metadata/traceability-sar.yaml')

        Returns:
            List of PFS folder names that use this requirement
        """
        match = re.match(r"^/?requirements/(.+)\.ya?ml$", file_path)
        if not match:
            # TODO: implement for glossary, sections, references as well
            return None

        requirement_name = match.group(1)

        # Find all PFS folders
        pfs_dir = workspace_path / "pfs"
        if not pfs_dir.exists() or not pfs_dir.is_dir():
            return None

        pfs_documents = []
        # Check each PFS folder's requirements.yaml file
        for pfs_folder in pfs_dir.iterdir():
            if not pfs_folder.is_dir():
                continue

            requirements_file = pfs_folder / "requirements.yaml"
            if not requirements_file.exists():
                continue

            try:
                content = requirements_file.read_text(encoding="utf-8")
                categories = yaml_load(content)
            except Exception as e:
                logger.warning(f"Could not read or parse requirements file for {pfs_folder.name}: {e}")
                continue

            # Check if this is a properly structured YAML array with categories
            if not isinstance(categories, list):
                continue

            for category in categories:
                if isinstance(category, dict) and "requirements" in category:
                    requirements = category.get("requirements", [])
                    if isinstance(requirements, list) and requirement_name in requirements:
                        pfs_documents.append(pfs_folder.name)
                        break

        return pfs_documents

    async def get_file_context(self, db: Session, file_path: str, workspace_id: str, user_id: str):
        try:
            workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
            target_path = validate_workspace_path(file_path, workspace.abs_path, exists=True, type="file")

            try:
                repo = git.Repo(workspace.abs_path)
            except git.exc.GitCommandError as e:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to access repository") from e

            relative_file_str = normalize_workspace_path(target_path, workspace.abs_path, absolute=False)
            rel_file_path = normalize_workspace_path(target_path, workspace.abs_path)
            return {
                "name": target_path.name,
                "is_directory": target_path.is_dir(),
                "status": get_file_status(repo, relative_file_str),
                "path": normalize_workspace_path(target_path, workspace.abs_path),
                "usage": await self._get_file_usage(workspace.abs_path, rel_file_path),
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get file context: {str(e)}") from e


file_service = FileService()
