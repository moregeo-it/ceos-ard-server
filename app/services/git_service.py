import asyncio
import logging
import shutil
from collections import defaultdict
from pathlib import Path

import pygit2
from fastapi import HTTPException, status

from app.config import settings
from app.models.user import User
from app.schemas.workspace import SyncResult, SyncStatus
from app.utils.git_utils import UserPassCredentials, get_file_status, get_repo, get_repo_changes, sanitize_git_error
from app.utils.validation import normalize_workspace_path, validate_workspace_path

logger = logging.getLogger(__name__)

# One lock per workspace: prevents concurrent syncs of the same repository.
# A multi-process deployment would need a filesystem lock instead.
_sync_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


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

            # Clone with full history (no depth limit) to preserve upstream history
            callbacks = UserPassCredentials(user.username, user.access_token)
            repo = pygit2.clone_repository(clone_url, str(workspace_path), callbacks=callbacks)

            # Add upstream remote
            upstream_url = f"https://github.com/{upstream_owner}/{upstream_repo}"
            repo.remotes.create("upstream", upstream_url)

            # Fetch from upstream with full history
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
                diff.find_similar()  # Enable rename detection
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

    async def commit_changes(self, repo: pygit2.Repository, message: str, user: User) -> pygit2.Commit:
        """
        Commit changes to the current branch in the repository.

        Args:
            repo: pygit2 Repository instance
            message: Commit message
            user: User object to use for the commit signature
        """
        try:
            signature = pygit2.Signature(user.full_name or user.username, user.email)

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
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to commit changes: {str(e)}") from e

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

    async def sync_with_origin(self, repo: pygit2.Repository, user: User, branch_name: str, workspace_id: str) -> SyncResult:
        """
        Fetch the fork remote and merge remote branch changes into the local branch when safe.

        Fast-forwards when possible, creates a merge commit when histories diverged but merge
        cleanly, and aborts (restoring the repository) when the merge would conflict. The
        working tree is never left in a mid-merge state. Network calls run in worker threads;
        repository mutation stays on the event loop.

        Args:
            repo: pygit2 Repository instance
            user: User object with username and access_token for authentication
            branch_name: The workspace branch to sync with its origin counterpart
            workspace_id: Workspace id, used to serialize concurrent syncs
        """
        async with _sync_locks[workspace_id]:
            callbacks = UserPassCredentials(user.username, user.access_token)
            origin = repo.remotes["origin"]

            # Prune so a branch deleted on the fork is detectable
            try:
                await asyncio.to_thread(origin.fetch, callbacks=callbacks, prune=pygit2.GIT_FETCH_PRUNE)
            except Exception as e:
                error_msg = sanitize_git_error(e, user.username, user.access_token)
                logger.error(f"Unable to fetch origin for workspace {workspace_id}: {error_msg}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch updates from GitHub. Error: {error_msg}"
                ) from None  # don't raise e to avoid leaking sensitive information

            # Best-effort upstream refresh; keeps the get_commits baseline current
            try:
                await asyncio.to_thread(repo.remotes["upstream"].fetch, [settings.CEOS_ARD_BRANCH])
            except Exception as e:
                logger.warning(f"Could not refresh upstream for workspace {workspace_id}: {e}")

            # No awaits from here through the merge: keeps the dirty check and the
            # reset/merge atomic with concurrent file-editing requests
            remote_ref = repo.references.get(f"refs/remotes/origin/{branch_name}")
            if remote_ref is None:
                return SyncResult(status=SyncStatus.REMOTE_MISSING)

            remote_oid = remote_ref.target
            local_oid = repo.head.target
            ahead, behind = repo.ahead_behind(local_oid, remote_oid)

            if get_repo_changes(repo):
                return SyncResult(status=SyncStatus.DIRTY, ahead_commits=ahead, behind_commits=behind)

            if behind == 0:
                if ahead > 0:
                    # Best-effort push of local commits the fork is missing
                    try:
                        await asyncio.to_thread(origin.push, [f"refs/heads/{branch_name}"], callbacks=callbacks)
                    except Exception as e:
                        error_msg = sanitize_git_error(e, user.username, user.access_token)
                        logger.warning(f"Could not push local commits for workspace {workspace_id}: {error_msg}")
                return SyncResult(status=SyncStatus.UP_TO_DATE, ahead_commits=ahead)

            analysis, _ = repo.merge_analysis(remote_oid)

            if analysis & pygit2.GIT_MERGE_ANALYSIS_UP_TO_DATE:
                return SyncResult(status=SyncStatus.UP_TO_DATE, ahead_commits=ahead)

            if analysis & pygit2.GIT_MERGE_ANALYSIS_FASTFORWARD:
                # Tree is clean (checked above), so a hard reset is a safe fast-forward
                repo.reset(remote_oid, pygit2.GIT_RESET_HARD)
                return SyncResult(status=SyncStatus.UPDATED, behind_commits=behind, pulled_commits=behind)

            if not analysis & pygit2.GIT_MERGE_ANALYSIS_NORMAL:
                logger.warning(f"Unexpected merge analysis {analysis} for workspace {workspace_id}")
                return SyncResult(status=SyncStatus.UP_TO_DATE, ahead_commits=ahead)

            try:
                repo.merge(remote_oid)

                if repo.index.conflicts is not None:
                    # Collect paths before resetting — the reset clears the conflict entries
                    conflicting_files = sorted({entry.path for conflict in repo.index.conflicts for entry in conflict if entry is not None})
                    repo.reset(local_oid, pygit2.GIT_RESET_HARD)
                    repo.state_cleanup()
                    return SyncResult(status=SyncStatus.CONFLICT, ahead_commits=ahead, behind_commits=behind, conflicting_files=conflicting_files)

                signature = pygit2.Signature(user.full_name or user.username, user.email)
                tree_id = repo.index.write_tree()
                repo.create_commit(
                    "HEAD",
                    signature,
                    signature,
                    f"Merge remote changes from origin/{branch_name}",
                    tree_id,
                    [local_oid, remote_oid],
                )
                repo.state_cleanup()
            except Exception as e:
                try:
                    repo.reset(local_oid, pygit2.GIT_RESET_HARD)
                    repo.state_cleanup()
                except Exception as cleanup_error:
                    logger.error(f"Failed to restore workspace {workspace_id} after merge error: {cleanup_error}")
                logger.error(f"Error merging remote changes for workspace {workspace_id}: {e}")
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to merge remote changes") from e

            # Best-effort push of the merge commit; if it fails, the next push or sync delivers it
            try:
                await asyncio.to_thread(origin.push, [f"refs/heads/{branch_name}"], callbacks=callbacks)
            except Exception as e:
                error_msg = sanitize_git_error(e, user.username, user.access_token)
                logger.warning(f"Could not push merge commit for workspace {workspace_id}: {error_msg}")

            return SyncResult(status=SyncStatus.MERGED, behind_commits=behind, pulled_commits=behind)

    def get_commits(
        self,
        workspace_path: Path,
        upstream_branch: str = None,
    ) -> list[pygit2.Commit]:
        """
        Get all commits that have been added to the current branch compared to the upstream branch.

        Args:
            workspace_path: Path to the workspace/repository
            upstream_branch: The upstream branch to compare against (defaults to settings.CEOS_ARD_BRANCH)

        Returns:
            List of commit dictionaries with sha, message, and timestamp
        """
        if upstream_branch is None:
            upstream_branch = settings.CEOS_ARD_BRANCH

        repo = get_repo(workspace_path)
        commits = []
        try:
            if repo.head_is_unborn:
                return commits

            # Get the current HEAD commit
            head_commit = repo.head.peel()

            # Get the upstream branch reference
            upstream_ref = repo.references.get(f"refs/remotes/upstream/{upstream_branch}")
            if upstream_ref is None:
                logger.warning(f"Upstream branch refs/remotes/upstream/{upstream_branch} not found")
                return commits

            upstream_commit = upstream_ref.peel()

            # Walk from HEAD and collect commits until we reach the upstream commit
            # This gives us all commits that are in HEAD but not in upstream
            walker = repo.walk(head_commit.id, pygit2.GIT_SORT_TOPOLOGICAL | pygit2.GIT_SORT_TIME)

            # Hide all commits reachable from upstream (i.e., only show commits ahead of upstream)
            walker.hide(upstream_commit.id)

            for commit in walker:
                commits.append(commit)
        except pygit2.GitError as e:
            logger.error(f"Error getting commits ahead of upstream: {e}")

        return commits
