import asyncio
import hashlib
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models.workspace import GitWorkspace, WorkspaceStatus
from app.schemas.workspace import PFS_ID_PATTERN
from app.utils.process import OutputTooLarge
from app.utils.sandbox import BuildOutOfMemory, BuildOutputTooLarge, run_build
from app.utils.toolchains import ToolchainError, toolchains
from app.utils.validation import check_read_size
from app.workspace_types import WorkspaceType

logger = logging.getLogger(__name__)


class BuildService:
    @staticmethod
    def output_prefix(workspace_path: Path, pfs: list[str] | None, build_dir: Path | None = None) -> str:
        """Where a selection's outputs go (`<prefix>.html`, `.pdf`, `.docx`): `build_dir`, else the workspace's `build/`.

        Every build and read goes through here, so the ids are checked here too, stored ones included:
        `../` in an id would reach another workspace, a leading `-` would be a CLI option.
        """
        if not pfs:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Select at least one PFS for the preview")
        if not all(re.fullmatch(PFS_ID_PATTERN, pfs_id) for pfs_id in pfs):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid PFS id")
        name = "-".join(pfs)
        if len(name) > 200:  # a file name holds at most 255 bytes
            name = hashlib.sha256(name.encode()).hexdigest()
        return str((build_dir or workspace_path / "build") / name)

    @staticmethod
    def artifact(path: Path) -> Path | None:
        """
        A file the build wrote, unless it is missing or not a regular file: a build can leave symlinks to anything,
        and a workspace cloned before core.symlinks=false can have its `build` folder link elsewhere.
        """
        if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
            return None
        return path

    @staticmethod
    def read_preview(path: Path) -> str:
        """The preview HTML of a build, which the server reads into its memory, unlike documents it sends as files."""
        check_read_size(path.stat().st_size, path.name)
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
        """Start installing the toolchains of all active workspaces, e.g. after a deployment."""
        for workspace in db.query(GitWorkspace).filter(GitWorkspace.status == WorkspaceStatus.ACTIVE):
            self.prepare(workspace.workspace_type, workspace.abs_path)

    async def build(
        self,
        workspace_path: Path,
        workspace_id: str,
        workspace_type: WorkspaceType,
        pfs: list[str],
        include_format: str | None = None,
        build_dir: Path | None = None,
    ) -> dict[str, Any]:
        if not workspace_path or not workspace_id:
            raise ValueError("Workspace path and ID must be provided")

        if not workspace_path.exists():
            raise FileNotFoundError(f"Workspace path {workspace_path} does not exist")

        output_file = self.output_prefix(workspace_path, pfs, build_dir)

        def error(message: str) -> dict[str, Any]:
            return {"status": "error", "message": message, "output_file": output_file}

        try:
            toolchain = workspace_type.toolchain(workspace_path)
            if toolchain is None:
                return error(f"Workspaces of type {workspace_type.id} can't be built.")
            # Its own timeout, not the build's
            await toolchains.ensure(toolchain)
        except ToolchainError as e:
            return error(str(e))

        logger.info(f"Building workspace {workspace_id} with PFS {' '.join(pfs)} and the toolchain {toolchain.name}")
        command = workspace_type.build_command(workspace_path, pfs, output_file, include_format)
        output_dir = Path(output_file).parent
        # The build may write there, so not through a symlink, e.g. a committed `build` link
        if output_dir.is_symlink() or not output_dir.resolve().is_relative_to((build_dir or workspace_path).resolve()):
            return error("The build folder of the workspace is not a folder.")
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            process = await run_build(
                command,
                path=toolchains.bin_path(toolchain.name),
                read_only=[toolchains.path(toolchain.name), workspace_path],
                writable=[output_dir],
                hidden=[workspace_path / ".git"],
                cwd=workspace_path,
                timeout=settings.BUILD_TIMEOUT,
            )
        except TimeoutError:
            return error(f"Generating the document took longer than {settings.BUILD_TIMEOUT} seconds and was stopped.")
        except BuildOutOfMemory:
            return error("Generating the document needed too much memory and was stopped.")
        except OutputTooLarge:
            return error("Generating the document printed too much output and was stopped.")
        except BuildOutputTooLarge:
            # Not left on the disk until the next build
            await asyncio.to_thread(shutil.rmtree, output_dir, ignore_errors=True)
            return error("Generating the document wrote too much and was stopped.")

        if process.returncode == 0:
            return {"status": "success", "message": "Build completed successfully.", "output_file": output_file}
        logger.error(f"Build failed for workspace {workspace_id} with code {process.returncode}: {process.stderr.decode(errors='replace')[-2000:]}")
        return error("Generating document failed, likely one of the changes caused an issue (e.g., invalid YAML).")
