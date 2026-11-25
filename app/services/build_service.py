import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models.workspace import GitWorkspace, WorkspaceStatus
from app.utils import limits
from app.utils.process import OutputTooLarge
from app.utils.sandbox import BuildOutOfMemory, BuildOutputTooLarge, run_build
from app.utils.toolchains import Toolchain, ToolchainError, toolchains
from app.workspace_types import WorkspaceType
from app.workspace_types.base import SelectionError

logger = logging.getLogger(__name__)

MAX_CACHE_SIZE = 16 * limits.MB
MAX_CACHE_FILES = 5000


def _copy_sources(workspace_path: Path, target: Path) -> None:
    # Without what builds never read; clones have no symlinks, and any left are copied as links
    shutil.copytree(
        workspace_path, target, symlinks=True, ignore=lambda folder, names: {".git", "build"} if Path(folder) == workspace_path else set()
    )


def _caches_folder(workspace_path: Path) -> Path:
    # Never in a build's copy of the workspace or in a commit, and removed with the workspace
    return workspace_path / ".git" / "ceos-ard-build-caches"


def _real_folder(path: Path, root: Path) -> bool:
    """Whether `path` is a folder in `root` that no symlink leads to: the build may have replaced any of them."""
    try:
        return path.is_dir() and path.resolve() == root.resolve() / path.relative_to(root)
    except (OSError, ValueError):
        return False


def _restore_caches(workspace_path: Path, source_path: Path, caches: list[str]) -> None:
    for cache in caches:
        saved, target = _caches_folder(workspace_path) / cache, source_path / cache
        if _real_folder(saved, workspace_path) and not target.is_symlink() and _real_folder(target.parent, source_path):
            shutil.copytree(saved, target, dirs_exist_ok=True)


def _save_caches(workspace_path: Path, source_path: Path, caches: list[str]) -> None:
    """Keep the caches the build left for the next build: only their regular files, and only if they are small."""
    if not _real_folder(workspace_path / ".git", workspace_path):
        return
    for cache in caches:
        saved, folder = _caches_folder(workspace_path) / cache, source_path / cache
        if _real_folder(saved, workspace_path):
            shutil.rmtree(saved)
        if not _real_folder(folder, source_path):
            continue
        files = []
        for root, folders, names in os.walk(folder):
            folders[:] = [name for name in folders if not (Path(root) / name).is_symlink()]
            files += [path for path in (Path(root) / name for name in names) if not path.is_symlink() and path.is_file()]
        if len(files) > MAX_CACHE_FILES or sum(path.stat().st_size for path in files) > MAX_CACHE_SIZE:
            logger.warning(f"Not keeping the build cache {cache} of {workspace_path}: it's too large")
            continue
        for path in files:
            target = saved / path.relative_to(folder)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


class BuildService:
    @staticmethod
    def output_prefix(workspace_type: WorkspaceType, workspace_path: Path, selection: list[str] | None, build_dir: Path | None = None) -> str:
        """Where a selection's outputs go (`<prefix>.html`, `.pdf`, `.docx`): `build_dir`, else the workspace's `build/`."""
        try:
            name = workspace_type.output_name(selection or [])
        except SelectionError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
        return str((build_dir or workspace_path / "build") / name)

    @staticmethod
    def artifact(path: Path, output_dir: Path) -> Path | None:
        """
        A file the build wrote directly into `output_dir`, unless it is missing or not a regular file: a build can leave
        symlinks and folders that lead anywhere, and a workspace cloned before core.symlinks=false can have its `build`
        folder link elsewhere.
        """
        if path.parent != output_dir or output_dir.is_symlink() or path.is_symlink() or not path.is_file():
            return None
        return path

    @staticmethod
    def read_preview(path: Path) -> str:
        """The preview HTML of a build, which the server reads into its memory, unlike documents it sends as files."""
        limits.check_read_size(path.stat().st_size, path.name, limits.MAX_PREVIEW_SIZE)
        return path.read_text(encoding="utf-8")

    @staticmethod
    def prepare(workspace_type: WorkspaceType, workspace_path: Path) -> None:
        """Start installing the workspace's toolchain in the background, so its first build doesn't wait for it."""
        try:
            toolchain = workspace_type.toolchain(workspace_path)
        except ToolchainError:
            return  # The build reports it
        except Exception as e:
            # Workspace content, so it mustn't break e.g. the server's start
            logger.warning(f"Can't prepare the toolchain of {workspace_path}: {e}")
            return
        if toolchain:
            toolchains.prepare(toolchain)

    def prepare_all(self, db: Session) -> None:
        """Remove the toolchains no workspace uses anymore, and start installing those of active workspaces, e.g. after a deployment."""
        used = set()
        for workspace in db.query(GitWorkspace):
            try:
                if toolchain := workspace.workspace_type.toolchain(workspace.abs_path):
                    used.add(toolchain.name)
            except Exception:
                continue
        toolchains.remove_unused(used)
        for workspace in db.query(GitWorkspace).filter(GitWorkspace.status == WorkspaceStatus.ACTIVE):
            self.prepare(workspace.workspace_type, workspace.abs_path)

    @staticmethod
    def missing_toolchain(workspace_type: WorkspaceType, workspace_path: Path) -> Toolchain | None:
        """The toolchain a build would have to install first, if any; a build reports invalid ones."""
        try:
            toolchain = workspace_type.toolchain(workspace_path)
        except ToolchainError:
            return None
        return toolchain if toolchain and not toolchains.is_installed(toolchain.name) else None

    async def build(
        self,
        workspace_path: Path,
        workspace_id: str,
        workspace_type: WorkspaceType,
        selection: list[str],
        include_format: str | None = None,
        build_dir: Path | None = None,
    ) -> dict[str, Any]:
        if not workspace_path or not workspace_id:
            raise ValueError("Workspace path and ID must be provided")

        if not workspace_path.exists():
            raise FileNotFoundError(f"Workspace path {workspace_path} does not exist")

        output_file = self.output_prefix(workspace_type, workspace_path, selection, build_dir)

        def error(message: str) -> dict[str, Any]:
            return {"status": "error", "message": message, "output_file": output_file}

        try:
            await workspace_type.refresh_toolchain()
            toolchain = workspace_type.toolchain(workspace_path)
            if toolchain is None:
                return error(f"Workspaces of type {workspace_type.id} can't be built.")
            # Its own timeout, not the build's
            await toolchains.ensure(toolchain)
        except ToolchainError as e:
            return error(str(e))

        timeout = workspace_type.build_timeout or settings.BUILD_TIMEOUT
        output_dir = Path(output_file).parent
        # The build may write there, so not through a symlink, e.g. a committed `build` link
        if output_dir.is_symlink() or not output_dir.resolve().is_relative_to((build_dir or workspace_path).resolve()):
            return error("The build folder of the workspace is not a folder.")
        output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Building workspace {workspace_id} with {' '.join(selection)} and the toolchain {toolchain.name}")
        sources = None
        caches = []
        env = {**toolchains.env_vars(toolchain.name), **workspace_type.build_env}
        try:
            if workspace_type.writable_sources:
                sources = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="ceos-ard-sources-"))
                source_path = sources / "workspace"
                await asyncio.to_thread(_copy_sources, workspace_path, source_path)
                env.update(await asyncio.to_thread(workspace_type.prepare_sources, sources))
                caches = workspace_type.build_caches(selection)
                await asyncio.to_thread(_restore_caches, workspace_path, source_path, caches)
                read_only, writable, hidden = [toolchains.path(toolchain.name)], [output_dir, sources], []
            else:
                source_path = workspace_path
                read_only, writable, hidden = [toolchains.path(toolchain.name), workspace_path], [output_dir], [workspace_path / ".git"]
            command = workspace_type.build_command(source_path, selection, output_file, include_format)
            process = await run_build(
                command,
                path=toolchains.bin_path(toolchain.name),
                read_only=read_only,
                writable=writable,
                hidden=hidden,
                cwd=source_path / workspace_type.build_folder(selection),
                timeout=timeout,
                env=env,
                network=workspace_type.build_network,
                memory_max=workspace_type.build_memory_max,
            )
            # Previews only: they run one at a time per workspace, unlike downloads into their own `build_dir`
            if caches and build_dir is None:
                try:
                    await asyncio.to_thread(_save_caches, workspace_path, source_path, caches)
                except OSError as e:
                    logger.warning(f"Can't keep the build caches of workspace {workspace_id}: {e}")
            if sources and process.returncode == 0:
                try:
                    await asyncio.to_thread(workspace_type.finish_build, sources, output_file)
                except Exception as e:
                    # Only adds to a document that is complete without it
                    logger.warning(f"Can't complete the build of workspace {workspace_id}: {e}")
        except TimeoutError:
            return error(f"Generating the document took longer than {timeout} seconds and was stopped.")
        except BuildOutOfMemory:
            return error("Generating the document needed too much memory and was stopped.")
        except OutputTooLarge:
            return error("Generating the document printed too much output and was stopped.")
        except BuildOutputTooLarge:
            # Not left on the disk until the next build
            await asyncio.to_thread(shutil.rmtree, output_dir, ignore_errors=True)
            return error("Generating the document wrote too much and was stopped.")
        finally:
            if sources:
                await asyncio.to_thread(shutil.rmtree, sources, ignore_errors=True)

        if process.returncode == 0:
            return {"status": "success", "message": "Build completed successfully.", "output_file": output_file}
        logger.error(f"Build failed for workspace {workspace_id} with code {process.returncode}: {process.stderr.decode(errors='replace')[-2000:]}")
        message = workspace_type.build_error(process.stderr.decode(errors="replace") + process.stdout.decode(errors="replace"))
        return error(message or "Generating document failed, likely one of the changes caused an issue (e.g., invalid YAML).")
