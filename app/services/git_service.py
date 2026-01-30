import logging
import shutil
from pathlib import Path

import pygit2
from fastapi import HTTPException, status

from app.config import settings
from app.models.user import User
from app.utils.git_utils import UserPassCredentials, get_file_status, get_repo, sanitize_git_error
from app.utils.validation import normalize_workspace_path, validate_workspace_path

logger = logging.getLogger(__name__)


class GitService:
    def __init__(self):
        self.workspaces_root = settings.WORKSPACES_ROOT
        self.workspaces_root.mkdir(parents=True, exist_ok=True)

    async def clone_repository(
        self,
        user: User,
        clone_url: str,
        workspace_path: Path,
        branch_name: str,
        upstream_owner: str,
        upstream_repo: str,
        upstream_branch: str = "main",
    ) -> bool:
        try:
            workspace_path.parent.mkdir(parents=True, exist_ok=True)

            # Clone with depth=1 (shallow clone)
            callbacks = UserPassCredentials(user.username, user.access_token)
            repo = pygit2.clone_repository(clone_url, str(workspace_path), callbacks=callbacks, depth=1)

            # Add upstream remote
            upstream_url = f"https://github.com/{upstream_owner}/{upstream_repo}"
            repo.remotes.create("upstream", upstream_url)

            # Fetch from upstream
            upstream_remote = repo.remotes["upstream"]
            upstream_remote.fetch([upstream_branch])

            # Get the upstream branch commit
            upstream_ref = repo.references.get(f"refs/remotes/upstream/{upstream_branch}")
            if upstream_ref is None:
                raise Exception(f"Could not find upstream branch: {upstream_branch}")

            upstream_commit = upstream_ref.peel()

            # Create and checkout new branch from upstream
            repo.create_branch(branch_name, upstream_commit)
            repo.checkout(f"refs/heads/{branch_name}")

            # Push to origin and set upstream tracking
            await self.push(repo=repo, branch_name=branch_name, user=user, set_upstream=True)

            logger.info(f"Successfully cloned repository to {workspace_path}")

            return True
        except pygit2.GitError as e:
            logger.error(f"Invalid git repository: {clone_url}")

            if workspace_path.exists():
                shutil.rmtree(workspace_path)

            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a valid git repository") from e
        except Exception as e:
            logger.error(f"Error cloning repository: {e}")

            if workspace_path.exists():
                shutil.rmtree(workspace_path)

            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to clone repository: {e}") from e

    async def revert_file_changes(self, workspace_path: Path, file_path: str):
        target_file_path = validate_workspace_path(file_path, workspace_path)
        relative_file_str = normalize_workspace_path(target_file_path, workspace_path, absolute=False)

        repo = get_repo(workspace_path)

        # Check if file exists in HEAD commit
        try:
            if not repo.head_is_unborn:
                head_commit = repo.head.peel()
                try:
                    # Try to get the file from HEAD
                    head_commit.tree[relative_file_str]
                    # File exists in HEAD - restore it
                    repo.checkout_head(paths=[relative_file_str], strategy=pygit2.GIT_CHECKOUT_FORCE)

                    return {
                        "name": str(target_file_path.name),
                        "is_directory": target_file_path.is_dir(),
                        "status": get_file_status(repo, target_file_path),
                        "path": normalize_workspace_path(target_file_path, workspace_path),
                    }
                except KeyError:
                    # File not in HEAD
                    pass
        except pygit2.GitError:
            pass

        # Check for renames in staged changes
        try:
            if not repo.head_is_unborn:
                diff = repo.index.diff_to_tree(repo.head.peel().tree)
                for delta in diff.deltas:
                    if delta.status == pygit2.GIT_DELTA_RENAMED and delta.new_file.path == relative_file_str:
                        old_path = delta.old_file.path

                        # Reset the index for both paths
                        repo.index.remove(relative_file_str)
                        repo.checkout_head(paths=[old_path], strategy=pygit2.GIT_CHECKOUT_FORCE)
                        repo.index.add(old_path)
                        repo.index.write()

                        # Remove new file if it exists
                        if target_file_path.exists():
                            target_file_path.unlink()

                        old_file_path = workspace_path / old_path
                        return {
                            "name": str(old_file_path.name),
                            "is_directory": old_file_path.is_dir(),
                            "status": get_file_status(repo, old_file_path),
                            "path": normalize_workspace_path(old_file_path, workspace_path),
                        }
        except pygit2.GitError as e:
            logger.error(f"Git error reverting file: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to revert file changes: {str(e)}") from e
        except Exception as e:
            logger.error(f"Error reverting file changes: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to revert file changes") from e

        # File has no git history - cannot revert
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot revert file with no git history. File was never committed.")

    async def commit_changes(self, repo: pygit2.Repository, message: str):
        """
        Commit changes to the current branch in the repository.

        Args:
            repo: pygit2 Repository instance
            message: Commit message
        """
        try:
            # Get the current user signature from config or use defaults
            try:
                signature = repo.default_signature
            except pygit2.GitError:
                signature = pygit2.Signature("CEOS-ARD Editor", "noreply@ceos.org")

            # Get the tree from the index
            tree_id = repo.index.write_tree()

            # Get parent commit
            if repo.head_is_unborn:
                parents = []
            else:
                parents = [repo.head.peel().id]

            # Create the commit
            commit_id = repo.create_commit(
                "HEAD",
                signature,
                signature,
                message,
                tree_id,
                parents,
            )

            return repo.get(commit_id)
        except Exception as e:
            logger.error(f"Unable to commit changes: {str(e)}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to commit changes, please try again.") from e

    async def push(self, repo: pygit2.Repository, branch_name: str, user: User, set_upstream: bool = False):
        """
        Push changes with user-specific credentials.

        Args:
            repo: pygit2 Repository instance
            branch_name: Branch to push to
            user: User object with username and access_token for authentication
        """
        try:
            origin = repo.remotes["origin"]
            callbacks = UserPassCredentials(user.username, user.access_token)
            ref = f"refs/heads/{branch_name}"

            if set_upstream:
                # Push with upstream tracking
                origin.push([f"{ref}:{ref}"], callbacks=callbacks)
                # Fetch to update remote refs
                origin.fetch(callbacks=callbacks)
                # Set tracking branch
                branch = repo.branches.get(branch_name)
                if branch:
                    branch.upstream = repo.branches.remote.get(f"origin/{branch_name}")
            else:
                origin.push([ref], callbacks=callbacks)

            logger.info(f"Pushed changes to remote branch {branch_name}")
        except Exception as e:
            error_msg = sanitize_git_error(e, user.username, user.access_token)
            logger.error(f"Unable to push: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to send changes to GitHub, please try again. Error: {error_msg}"
            ) from None  # don't raise e to avoid leaking sensitive information
