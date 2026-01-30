import logging
import re
import shutil
from pathlib import Path

import pygit2
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from yaml import safe_load as yaml_load

from app.models.user import User
from app.models.workspace import PullRequestStatus, WorkspaceStatus
from app.schemas.workspace import FilePatchRequest
from app.services.git_service import GitService
from app.services.workspace_service import WorkspaceService
from app.utils.extraction import get_excerpt, get_file_media_type
from app.utils.git_utils import format_commit, get_file_info, get_file_status, get_repo, get_repo_changes
from app.utils.validation import IGNORE_ROOT_PATHS, ignore_file_path, normalize_workspace_path, validate_pathname, validate_workspace_path

logger = logging.getLogger(__name__)


class FileService:
    def __init__(self):
        self.git_service = GitService()
        self.workspace_service = WorkspaceService()
        self.searchable_file_extensions = {".txt", ".md", ".json", ".yaml", ".yml", ".xml"}

    def _get_all_file_statuses(self, repo: pygit2.Repository, target_path: Path, workspace_path: Path):
        """Get all file statuses using pygit2 API."""
        status_map = {}
        relative_target = normalize_workspace_path(target_path, workspace_path, absolute=False)
        renamed_new_paths = set()
        renamed_old_paths = set()

        def get_map_key(file_path: str) -> str:
            path = workspace_path / file_path
            return str(path.resolve())

        try:
            path_filter = relative_target if len(relative_target) > 0 else None

            # First, detect renames using diff with rename detection
            if not repo.head_is_unborn:
                diff = repo.index.diff_to_tree(repo.head.peel().tree)
                diff.find_similar()  # Enable rename detection
                for delta in diff.deltas:
                    if delta.status == pygit2.GIT_DELTA_RENAMED:
                        new_path = delta.new_file.path
                        old_path = delta.old_file.path
                        # Apply path filter
                        if path_filter and not new_path.startswith(path_filter) and not old_path.startswith(path_filter):
                            continue
                        status_map[get_map_key(new_path)] = "renamed"
                        renamed_new_paths.add(new_path)
                        renamed_old_paths.add(old_path)

            # Get status from pygit2, excluding files that are part of renames
            status_dict = repo.status()

            for file_path, flags in status_dict.items():
                # Skip files that are part of a rename operation
                if file_path in renamed_new_paths or file_path in renamed_old_paths:
                    continue

                # Apply path filter if specified
                if path_filter and not file_path.startswith(path_filter):
                    continue

                # Map pygit2 flags to status strings
                if flags & (pygit2.GIT_STATUS_WT_NEW | pygit2.GIT_STATUS_INDEX_NEW):
                    status_map[get_map_key(file_path)] = "added"
                elif flags & (pygit2.GIT_STATUS_WT_DELETED | pygit2.GIT_STATUS_INDEX_DELETED):
                    status_map[get_map_key(file_path)] = "deleted"
                elif flags & (pygit2.GIT_STATUS_WT_MODIFIED | pygit2.GIT_STATUS_INDEX_MODIFIED):
                    status_map[get_map_key(file_path)] = "modified"

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
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        target_path = validate_workspace_path(path, workspace.abs_path, exists=True)

        repo = get_repo(workspace.abs_path)
        try:
            # Get the status of all files (e.g. to include deleted files)
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

    def walk_files(self, target_path: Path, workspace_path: Path, repo: pygit2.Repository, recurse: bool = False, status: dict | None = None) -> list[dict]:
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
        elif not request_data.type:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Type is required")
        elif not request_data.path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path is required")
        elif request_data.type not in ["file", "folder"]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Type must be file or folder")

        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        name = validate_pathname(request_data.name)
        folder = validate_workspace_path(request_data.path, workspace.abs_path, exists=True)

        target_path = folder / name
        if target_path.exists():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{'Directory' if target_path.is_dir() else 'File'} already exists")

        if request_data.type == "file":
            return self._create_file(workspace.abs_path, request_data.name, target_path)
        elif request_data.type == "folder":
            return self._create_folder(workspace.abs_path, request_data.name, target_path)
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid type")

    def _create_file(self, workspace_path: Path, name: str, target_path: Path, content: bytes = None):
        repo = get_repo(workspace_path)
        try:
            if content is not None:
                target_path.write_bytes(content)
            else:
                target_path.touch()

            # Stage the file using pygit2
            relative_path = str(target_path.relative_to(workspace_path)).replace("\\", "/")
            repo.index.add(relative_path)
            repo.index.write()

            return {
                "name": name,
                "is_directory": False,
                "status": get_file_status(repo, target_path),
                "path": normalize_workspace_path(target_path, workspace_path),
            }
        except pygit2.GitError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="The file has been created, but it failed to be added to the repository"
            ) from e
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create file: {str(e)}") from e

    def _create_folder(self, workspace_path: Path, name: str, target_path: Path):
        repo = get_repo(workspace_path)
        try:
            target_path.mkdir(parents=True, exist_ok=True)
            return {
                "name": name,
                "is_directory": True,
                "status": get_file_status(repo, target_path),
                "path": normalize_workspace_path(target_path, workspace_path),
            }
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create folder: {str(e)}") from e

    async def read_file_content(self, db: Session, workspace_id: str, file_path: str, user_id: str):
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        file_path = validate_workspace_path(file_path, workspace.abs_path, exists=True, type="file")
        try:
            return {"content": file_path.read_bytes(), "media_type": get_file_media_type(file_path)}
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to read file: {str(e)}") from e

    async def store_file_content(self, db: Session, workspace_id: str, file_path: str, content: bytes, user_id: str):
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        file_path = validate_workspace_path(file_path, workspace.abs_path, type="file")
        repo = get_repo(workspace.abs_path)
        try:
            file_path.write_bytes(content)
            # Stage the file using pygit2
            relative_path = str(file_path.relative_to(workspace.abs_path)).replace("\\", "/")
            repo.index.add(relative_path)
            repo.index.write()
            return {
                "name": file_path.name,
                "is_directory": False,
                "status": get_file_status(repo, file_path),
                "path": normalize_workspace_path(file_path, workspace.abs_path),
            }
        except pygit2.GitError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="The file has been stored, but it failed to be added to the repository"
            ) from e
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to store file: {str(e)}") from e

    async def delete(self, db: Session, workspace_id: str, file_path: str, user_id: str):
        if not file_path:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File path is required")

        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        target_path = validate_workspace_path(file_path, workspace.abs_path, exists=True)
        relative_path = normalize_workspace_path(target_path, workspace.abs_path, absolute=False)
        repo = get_repo(workspace.abs_path)

        if target_path.is_file():
            ftype = "File"
            try:
                target_path.unlink()
            except Exception as e:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete file. Please try again.") from e
        elif target_path.is_dir():
            ftype = "Folder"
            try:
                shutil.rmtree(target_path)
            except Exception as e:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete folder. Please try again.") from e
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path is neither a file nor a folder.")

        # Check if file exists in HEAD (has git history)
        is_committed = False
        try:
            if not repo.head_is_unborn:
                head_commit = repo.head.peel()
                try:
                    head_commit.tree[relative_path]
                    is_committed = True
                except KeyError:
                    is_committed = False
        except pygit2.GitError:
            is_committed = False

        if is_committed:
            # File is in git history - stage the deletion
            try:
                repo.index.remove(relative_path)
                repo.index.write()
            except pygit2.GitError as e:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"{ftype} deleted successfully, but failed to make the changes in the repository",
                ) from e
        else:
            # File not in git history - remove from index if staged
            try:
                repo.index.remove(relative_path)
                repo.index.write()
            except pygit2.GitError:
                # File wasn't in index, nothing to do
                pass

        return {
            # Tracked means the file is and was under version control, so the delete can be reverted if needed.
            "tracked": is_committed,
            "file_details": {
                "name": target_path.name,
                "is_directory": target_path.is_dir(),
                "status": get_file_status(repo, target_path),
                "path": normalize_workspace_path(target_path, workspace.abs_path),
            },
        }

    async def update_file(self, db: Session, workspace_id: str, file_path: str, operation_request: FilePatchRequest, user_id: str):
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        if operation_request.operation == "rename":
            return await self._update_file_name(workspace.abs_path, file_path, new_name=operation_request.target)
        elif operation_request.operation == "revert":
            return await self._revert_file_changes(workspace.abs_path, file_path)
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported operation specified")

    async def _update_file_name(self, workspace_path: Path, file_path: str, new_name: str):
        new_name = validate_pathname(new_name)
        source_path = validate_workspace_path(file_path, workspace_path, exists=True)
        repo = get_repo(workspace_path)

        target_path = source_path.parent / new_name
        if target_path.exists():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A file/folder with the given name exists already")

        ftype = "folder" if source_path.is_dir() else "file"

        try:
            source_path.rename(target_path)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to rename {ftype}. Please try again.") from e

        relative_old = normalize_workspace_path(source_path, workspace_path, absolute=False)
        relative_new = normalize_workspace_path(target_path, workspace_path, absolute=False)
        try:
            # Stage the rename: remove old path, add new path
            try:
                repo.index.remove(relative_old)
            except pygit2.GitError:
                pass  # Old path might not be in index yet
            repo.index.add(relative_new)
            repo.index.write()
        except pygit2.GitError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"The {ftype} was renamed, but failed to update the repository"
            ) from e

        return {
            "name": new_name,
            "is_directory": target_path.is_dir(),
            "status": get_file_status(repo, target_path),
            "path": normalize_workspace_path(target_path, workspace_path),
        }

    async def _revert_file_changes(self, workspace_path: Path, file_path: str):
        try:
            return await self.git_service.revert_file_changes(workspace_path=workspace_path, file_path=file_path)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to revert file changes: {str(e)}") from e

    async def search_files(self, db: Session, workspace_id: str, search_query: str, user_id: str):
        if not search_query:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search query is required")
        if len(search_query.strip()) < 3:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search query must be at least 3 characters long")

        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)

        try:
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
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        repo = get_repo(workspace.abs_path)
        return get_repo_changes(repo)

    async def get_file_diff(self, db: Session, file_path: str, workspace_id: str, user_id: str):
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        target_path = validate_workspace_path(file_path, workspace.abs_path, type="file")
        relative_path_str = normalize_workspace_path(target_path, workspace.abs_path, absolute=False)

        repo = get_repo(workspace.abs_path)
        info = get_file_info(repo, target_path)
        try:
            # Get the diff using pygit2
            if repo.head_is_unborn:
                # No commits yet - show all staged content as new
                diff = repo.index.diff_to_tree()
            else:
                head_tree = repo.head.peel().tree
                diff = repo.index.diff_to_tree(head_tree)

            # Find the specific file in the diff
            for patch in diff:
                patch_path = patch.delta.new_file.path
                if patch_path == relative_path_str:
                    return patch.text
                # Handle renamed files
                if info and info.get("status") == "renamed":
                    if patch.delta.old_file.path == info.get("source") or patch.delta.new_file.path == relative_path_str:
                        return patch.text

            # If file not found in staged diff, check working directory changes
            diff_workdir = repo.diff(repo.index, None)
            for patch in diff_workdir:
                if patch.delta.new_file.path == relative_path_str:
                    return patch.text

            return ""
        except pygit2.GitError as e:
            logger.error(f"Git error for {relative_path_str}: {str(e)}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get file diff: {str(e)}") from e

    async def persist_changes(self, db: Session, workspace_id: str, user: User, message: str) -> dict[str, str]:
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user.id)

        if workspace.status == WorkspaceStatus.ARCHIVED:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot commit changes for an archived workspace")
        elif workspace.pull_request_status in [PullRequestStatus.MERGED, PullRequestStatus.CLOSED]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Pull request is already {workspace.pull_request_status.value}; cannot commit changes",
            )

        repo = get_repo(workspace.abs_path)

        # Commit and push changes to the repository
        commit = await self.git_service.commit_changes(repo, message)

        # Try to push, revert commit on failure
        try:
            await self.git_service.push(repo=repo, branch_name=workspace.branch_name, user=user)
        except HTTPException:
            # Push failed - revert the commit but keep changes staged
            # Reset to parent commit (soft reset keeps files staged)
            if not repo.head_is_unborn:
                parent = repo.head.peel().parents[0] if repo.head.peel().parents else None
                if parent:
                    repo.reset(parent.id, pygit2.GIT_RESET_SOFT)
            logger.warning(f"Push failed, reverted commit for workspace {workspace_id}")
            raise

        return format_commit(commit)

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
        workspace = self.workspace_service.get_workspace_by_id(db, workspace_id, user_id)
        target_path = validate_workspace_path(file_path, workspace.abs_path, type="file")
        rel_file_path = normalize_workspace_path(target_path, workspace.abs_path)
        repo = get_repo(workspace.abs_path)

        usage = await self._get_file_usage(workspace.abs_path, rel_file_path)
        file_status = get_file_status(repo, target_path)

        return {
            "name": target_path.name,
            "is_directory": target_path.exists() and target_path.is_dir(),
            "status": file_status,
            "path": rel_file_path,
            "usage": usage,
        }


file_service = FileService()
